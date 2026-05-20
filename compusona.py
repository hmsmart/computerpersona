#!/usr/bin/env python3
"""compusona: in-character host notifications via OpenRouter + Telegram."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import tomllib

ENV_PATH = Path("/etc/compusona/env")
PERSONA_PATH = Path("/etc/compusona/persona.md")
CONFIG_PATH = Path("/etc/compusona/config.toml")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TELEGRAM_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

LLM_TIMEOUT_SECONDS = 8
TELEGRAM_TIMEOUT_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 5

DEFAULT_MODEL = "moonshotai/kimi-k2"
DEFAULT_TEMPERATURE = 0.9
DEFAULT_MAX_TOKENS = 80

BUILTIN_EVENTS = {
    "backup_ok",
    "backup_fail",
    "shutdown",
    "boot",
    "updates_available",
    "ups_battery",
}


def log_stderr(message: str) -> None:
    print(f"compusona: {message}", file=sys.stderr)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log_stderr(f"could not read env file {path}: {exc}")
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_persona(path: Path) -> str:
    try:
        persona = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log_stderr(f"could not read persona file {path}: {exc}")
        return "You are a system notification voice. Respond in one line."
    return persona or "You are a system notification voice. Respond in one line."


def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log_stderr(f"could not load config {path}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def run_command(args: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    output = (proc.stdout or "").strip()
    if output:
        return output
    return (proc.stderr or "").strip()


def backup_ok_facts(env: dict[str, str]) -> str:
    backup_path = Path(env.get("BACKUP_PATH", "/backup/latest"))
    try:
        if not backup_path.exists():
            return f"Backup succeeded; path {backup_path} was not found for size/count checks."
        total_bytes = 0
        file_count = 0
        for root, _, files in os.walk(backup_path):
            for name in files:
                file_count += 1
                file_path = Path(root) / name
                try:
                    total_bytes += file_path.stat().st_size
                except OSError:
                    continue
        size_mb = total_bytes / (1024 * 1024)
        return f"Backup completed successfully: {size_mb:.1f} MB across {file_count} files."
    except Exception:
        return "Backup completed successfully, but size/file count were unavailable."


def backup_fail_facts() -> str:
    try:
        out = run_command(["journalctl", "-u", "backup.service", "-n", "1", "--no-pager"])
        if out:
            return f"Backup failed. Last backup.service log line: {out.splitlines()[-1]}"
    except Exception:
        pass
    return "Backup failed, and no backup.service journal details were available."


def _service_show_map(service: str) -> dict[str, str]:
    out = run_command(
        [
            "systemctl",
            "show",
            service,
            "--no-pager",
            "--property=Result,ActiveState,SubState,ExecMainStatus",
        ]
    )
    fields: dict[str, str] = {}
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip():
            fields[key.strip()] = value.strip()
    return fields


def service_facts(
    service: str,
    result_hint: str = "",
    success_hint: bool | None = None,
    free_facts: str = "",
    outcome_hint: str = "",
) -> str:
    hint = result_hint.strip().lower()
    if success_hint is True:
        hint = "success"
    elif success_hint is False:
        hint = "fail"
    cli_hint = outcome_hint.strip().lower()
    if cli_hint:
        hint = cli_hint

    base = free_facts.strip()
    status = _service_show_map(service)
    last_line = run_command(["journalctl", "-u", service, "-n", "1", "--no-pager"])
    log_line = last_line.splitlines()[-1] if last_line else "no recent journal line"

    active = status.get("ActiveState", "unknown")
    sub = status.get("SubState", "unknown")
    result = status.get("Result", "unknown")
    exit_status = status.get("ExecMainStatus", "unknown")

    parts = [
        f"Service {service}: active={active}, sub={sub}, result={result}, exit={exit_status}.",
        f"Last log: {log_line}",
    ]
    if hint in {"success", "fail", "failed", "any"}:
        normalized_hint = "fail" if hint == "failed" else hint
        parts.append(f"Expected outcome: {normalized_hint}.")
    if base:
        parts.append(base)
    return " ".join(parts)


def configured_service_facts(
    config: dict[str, Any], event_name: str, outcome_hint: str = ""
) -> str | None:
    events_cfg = config.get("events", {})
    if not isinstance(events_cfg, dict):
        return None

    event_cfg = events_cfg.get(event_name, {})
    if not isinstance(event_cfg, dict):
        return None

    event_type = str(event_cfg.get("type", "")).strip().lower()
    if event_type != "service":
        return None

    service = str(event_cfg.get("service", "")).strip()
    if not service:
        return f"Event '{event_name}' requested type=service but no service name was configured."

    result_hint_raw = event_cfg.get("result", "")
    result_hint = result_hint_raw if isinstance(result_hint_raw, str) else ""
    success_hint_raw = event_cfg.get("success")
    success_hint = success_hint_raw if isinstance(success_hint_raw, bool) else None
    free_facts_raw = event_cfg.get("facts", "")
    free_facts = free_facts_raw if isinstance(free_facts_raw, str) else ""

    try:
        return service_facts(service, result_hint, success_hint, free_facts, outcome_hint)
    except Exception:
        return f"Service event '{event_name}' for {service} could not gather service facts."


def shutdown_facts() -> str:
    try:
        uptime = run_command(["uptime", "-p"]) or "uptime unavailable"
        loadavg = "load average unavailable"
        try:
            raw = Path("/proc/loadavg").read_text(encoding="utf-8").strip().split()
            if len(raw) >= 3:
                loadavg = f"load avg {raw[0]} {raw[1]} {raw[2]}"
        except OSError:
            pass
        return f"Shutdown requested after {uptime}; {loadavg}."
    except Exception:
        return "Shutdown requested; uptime/load average details unavailable."


def boot_facts() -> str:
    try:
        out = run_command(["systemd-analyze"])
        if out:
            return f"Boot complete. {out.splitlines()[0]}"
    except Exception:
        pass
    return "Boot complete; startup timing details were unavailable."


def _count_alnum_lines(text: str) -> int:
    pattern = re.compile(r"^[A-Za-z0-9]", flags=re.MULTILINE)
    return len(pattern.findall(text))


def updates_available_facts() -> str:
    try:
        updates_out = run_command(["dnf", "check-update", "-q"])
        security_out = run_command(["dnf", "updateinfo", "list", "security", "-q"])
        update_count = _count_alnum_lines(updates_out) if updates_out else 0
        security_count = _count_alnum_lines(security_out) if security_out else 0
        return f"Updates pending: {update_count} packages, including {security_count} security updates."
    except Exception:
        return "Updates may be available, but package manager details were unavailable."


def ups_battery_facts(env: dict[str, str]) -> str:
    ups_name = env.get("UPS_NAME", "").strip()
    if not ups_name:
        return "UPS alert: UPS_NAME is not set, battery charge/runtime are unknown."
    try:
        charge = run_command(["upsc", ups_name, "battery.charge"]) or "unknown"
        runtime = run_command(["upsc", ups_name, "battery.runtime"]) or "unknown"
        return f"UPS battery warning for {ups_name}: charge {charge}, runtime {runtime} seconds."
    except Exception:
        return f"UPS battery warning for {ups_name}: charge/runtime details unavailable."


def generic_event_facts(event_name: str) -> str:
    return f"Event '{event_name}' occurred; no event-specific facts are configured."


def gather_event_facts(
    event_name: str,
    env: dict[str, str],
    config: dict[str, Any],
    outcome_hint: str = "",
) -> str:
    configured = configured_service_facts(config, event_name, outcome_hint)
    if configured:
        return configured

    match event_name:
        case "backup_ok":
            return backup_ok_facts(env)
        case "backup_fail":
            return backup_fail_facts()
        case "shutdown":
            return shutdown_facts()
        case "boot":
            return boot_facts()
        case "updates_available":
            return updates_available_facts()
        case "ups_battery":
            return ups_battery_facts(env)
        case _:
            return generic_event_facts(event_name)


def prompt_suffix_for_event(config: dict[str, Any], event_name: str) -> str:
    events_cfg = config.get("events", {})
    if not isinstance(events_cfg, dict):
        return ""
    event_cfg = events_cfg.get(event_name, {})
    if not isinstance(event_cfg, dict):
        return ""
    suffix = event_cfg.get("prompt_suffix", "")
    return suffix if isinstance(suffix, str) else ""


def _normalized_outcome_token(token: str) -> str:
    value = token.strip().lower().replace("-", "_")
    aliases = {
        "failure": "fail",
        "failed": "fail",
        "error": "fail",
        "success": "ok",
        "succeeded": "ok",
    }
    return aliases.get(value, value)


def resolve_event_name(config: dict[str, Any], base_event: str, outcome_hint: str) -> str:
    event = base_event.strip()
    if not event:
        return "unknown"

    qualifier = _normalized_outcome_token(outcome_hint)
    if not qualifier:
        return event

    events_cfg = config.get("events", {})
    configured_names = set(events_cfg.keys()) if isinstance(events_cfg, dict) else set()
    candidates = [f"{event}_{qualifier}"]

    for candidate in candidates:
        if candidate in configured_names or candidate in BUILTIN_EVENTS:
            return candidate
    return event


def generate_llm_line(
    persona: str,
    facts: str,
    prompt_suffix: str,
    config: dict[str, Any],
    env: dict[str, str],
) -> str | None:
    api_key = env.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        log_stderr("OPENROUTER_API_KEY missing; using raw facts fallback")
        return None

    model = config.get("model", DEFAULT_MODEL)
    temperature = config.get("temperature", DEFAULT_TEMPERATURE)
    max_tokens = config.get("max_tokens", DEFAULT_MAX_TOKENS)

    if not isinstance(model, str):
        model = DEFAULT_MODEL
    if not isinstance(temperature, (int, float)):
        temperature = DEFAULT_TEMPERATURE
    if not isinstance(max_tokens, int):
        max_tokens = DEFAULT_MAX_TOKENS

    user_prompt = (
        f"Event facts: {facts}\n\n{prompt_suffix}\n\n"
        "Generate ONE in-character line."
    )
    payload = {
        "model": model,
        "temperature": float(temperature),
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        request = Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=LLM_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
        if isinstance(content, str) and content.strip():
            return " ".join(content.split())
    except Exception as exc:
        log_stderr(f"OpenRouter request failed: {exc}")
    return None


def send_telegram(message: str, env: dict[str, str]) -> None:
    token = env.get("TG_TOKEN", "").strip()
    chat_id = env.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        log_stderr("TG_TOKEN or TG_CHAT_ID missing; cannot send Telegram message")
        return

    payload = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(
        TELEGRAM_URL_TEMPLATE.format(token=token),
        data=payload,
        method="POST",
    )

    try:
        with urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS):
            pass
    except Exception as exc:
        log_stderr(f"Telegram send failed: {exc}")


def main(argv: list[str]) -> int:
    env: dict[str, str] = {}
    base_event = argv[1] if len(argv) > 1 else "unknown"
    outcome_hint = argv[2] if len(argv) > 2 else ""
    event_name = base_event
    facts = generic_event_facts(base_event)

    try:
        env = load_env_file(ENV_PATH)
        persona = load_persona(PERSONA_PATH)
        config = load_config(CONFIG_PATH)

        event_name = resolve_event_name(config, base_event, outcome_hint)
        facts = gather_event_facts(event_name, env, config, outcome_hint)
        suffix = prompt_suffix_for_event(config, event_name)
        llm_line = generate_llm_line(persona, facts, suffix, config, env)
        message = llm_line if llm_line else facts
        send_telegram(message, env)
    except Exception as exc:
        log_stderr(f"unexpected error: {exc}")
        send_telegram(facts, env)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
