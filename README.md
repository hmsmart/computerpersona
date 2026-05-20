# compusona

Give your servers a personality. `compusona` sends in-character host notifications to Telegram, with message text generated through OpenRouter using your persona + event facts.

This repository ships:

- `compusona.py` (single-file stdlib-only script)
- installable templates for env, config, persona, and systemd units
- a `Makefile` install target that deploys to system paths

## Features

- Stdlib-only Python implementation (Python 3.11+ for `tomllib`)
- Event-based facts gathering with graceful fallback defaults
- OpenRouter LLM generation with raw-facts fallback on any LLM failure
- Telegram delivery with timeout and stderr-only failure logging
- Shutdown-safe behavior: script always exits `0`

## Repository Files

- `compusona.py`
- `env.example`
- `config.toml.example`
- `persona.md.example`
- `compusona-shutdown.service.example`
- `compusona-boot.service.example`
- `Makefile`

## Installation

Run as root on the target host:

```bash
make install
```

This installs:

- `/usr/local/bin/compusona.py` (0755)
- `/etc/compusona/env` (0600)
- `/etc/compusona/config.toml` (0644)
- `/etc/compusona/persona.md` (0644)
- `/etc/systemd/system/compusona-shutdown.service` (0644)
- `/etc/systemd/system/compusona-boot.service` (0644)

Then edit secrets and persona:

```bash
sudoedit /etc/compusona/env
sudoedit /etc/compusona/persona.md
```

Enable services:

```bash
systemctl enable compusona-shutdown.service compusona-boot.service
```

## Required Secrets

`/etc/compusona/env` needs at least:

```env
OPENROUTER_API_KEY=sk-or-v1-...
TG_TOKEN=...
TG_CHAT_ID=...
```

Optional:

```env
BACKUP_PATH=/backup/latest
UPS_NAME=myups@localhost
```

## Usage

Run manually:

```bash
/usr/local/bin/compusona.py <event_name>
```

Optional outcome suffix:

```bash
/usr/local/bin/compusona.py <event_name> <outcome>
```

Examples:

```bash
/usr/local/bin/compusona.py shutdown
/usr/local/bin/compusona.py boot
/usr/local/bin/compusona.py updates_available
/usr/local/bin/compusona.py backup failure
/usr/local/bin/compusona.py backup_ok
/usr/local/bin/compusona.py backup_fail
/usr/local/bin/compusona.py foo
```

Unknown events are supported and produce generic facts.

## Supported Events

- `backup_ok`
- `backup_fail`
- `shutdown`
- `boot`
- `updates_available`
- `ups_battery`

You can also add new event prompt tuning in `/etc/compusona/config.toml` under `[events.<name>]`. If no code handler exists, compusona still runs with generic facts.

## Backup Service Facts From TOML

Use `backup_ok` and `backup_fail` tables to control which service log is checked and to append extra context.

Example:

```toml
[events.backup_ok]
service = "mybackup.service"
facts = "Nightly backup run completed."
prompt_suffix = "Treat large backups as a victory."

[events.backup_fail]
service = "mybackup.service"
facts = "Nightly backup run failed."
prompt_suffix = "Express frustration but stay in character. Imply you'll try again."
```

Behavior:

- `backup_ok` facts include backup size/file count, plus latest `journalctl -u <service> -n 1` line.
- `backup_fail` facts include failure context plus latest `journalctl -u <service> -n 1` line.
- If `service` is omitted, it defaults to `backup.service`.
- `facts` is optional free-form context appended to the facts string (it does not replace run-log capture).

## Quick Validation

Syntax check:

```bash
python3 -m py_compile compusona.py
```

Fallback behavior check (no API key):

```bash
python3 compusona.py foo; echo "Exit code: $?"
```

You should see exit code `0`.

## Operational Notes

- OpenRouter timeout is 8 seconds.
- Telegram timeout is 5 seconds.
- Subprocess fact commands use 5-second timeout.
- Script never raises a fatal error to caller and always exits `0`.

## Troubleshooting

- If Telegram is not sent, check stderr for missing `TG_TOKEN` or `TG_CHAT_ID`.
- If LLM generation fails, raw event facts are used automatically.
- If systemd units changed, run:

```bash
systemctl daemon-reload
```
