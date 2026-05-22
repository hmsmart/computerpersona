# compusona

> Give your servers a personality. Notifications with vibes, powered by an LLM and a config file.

`compusona` is a small Python utility that sends in-character notifications about a host's state to a notification backend (Telegram by default), using an LLM via [OpenRouter](https://openrouter.ai) to generate message text from a configurable persona and event-specific facts.

Instead of a notification that says:

> Backup completed. Size: 847MB. Files: 12043.

You get one that says whatever your persona file tells it to say, with the numbers woven in.

---

## Design priorities

1. **Must never block or delay system shutdown.** Short timeouts, graceful failure, always exits 0.
2. **Persona and per-event prompt tuning are editable without touching code.** This is the whole point.
3. **Stdlib only.** No `pip install`. Runs on a fresh box with just `python3`.
4. **Easy to add new event types.** One function, one config table.

---

## File layout

```
/usr/local/bin/compusona                 # the script (0755, root:root)
/etc/compusona/persona.md                # system prompt; character description
/etc/compusona/config.toml               # model, temperature, per-event prompt tuning
/etc/compusona/env                       # secrets (0600, root:root)
/etc/systemd/system/compusona-shutdown.service  # systemd hook for shutdown
/etc/systemd/system/compusona-boot.service      # systemd hook for boot
```

An install script or Makefile target is welcome but not required. It should create `/etc/compusona/`, copy templates, set permissions, and reload systemd.

---

## `/etc/compusona/env` format

Simple `KEY=value` lines, `#` comments allowed, quotes optional:

```
OPENROUTER_API_KEY=sk-or-v1-...
TG_TOKEN=...
TG_CHAT_ID=...
```

Parse with a small loader function — no `python-dotenv` dependency.

---

## `/etc/compusona/config.toml` format

```toml
model = "moonshotai/kimi-k2"
temperature = 0.9
max_tokens = 80

[events.backup_ok]
prompt_suffix = "Treat large backups as a victory."

[events.backup_fail]
prompt_suffix = "Express frustration but stay in character. Imply you'll try again."

[events.shutdown]
prompt_suffix = "Bittersweet farewell. Hint at returning."

[events.boot]
prompt_suffix = "Triumphant return after a brief slumber."

[events.updates_available]
prompt_suffix = "Treat pending packages as tasks to be conquered."

[events.ups_battery]
prompt_suffix = "Ominous, urgent — power reserves failing."
```

Parse with `tomllib` (stdlib, Python 3.11+). Unknown events should still run with no suffix rather than crash.

---

## `/etc/compusona/persona.md` format

Plain markdown/text, loaded verbatim as the LLM system prompt. The script should not parse or modify it. Ship a template — users will rewrite this to taste. A reasonable default:

```markdown
You are this server's compusona — its in-character voice for system notifications.

Rules:

- Respond in ONE line, under 200 characters.
- When given event facts with numbers (MB, seconds, counts), incorporate at least
  one specific number naturally — don't just restate them.
- Maximum one emoji per response.
- Stay in character even when reporting errors.

[Replace this paragraph with a description of your server's personality —
e.g. "You are a stoic medieval knight..." or "You are a grumpy 1970s mainframe..."
or "You are a cheerful golden retriever who happens to administer Linux systems."]
```

---

## Script: `/usr/local/bin/compusona`

### Invocation

```
compusona <event_name>
```

Where `<event_name>` matches a key under `[events.*]` in `config.toml`. Unknown event names should still produce a reasonable notification using the persona and a generic fact string.

### Behavior

1. Load env file from `/etc/compusona/env`.
2. Load persona from `/etc/compusona/persona.md`.
3. Load config from `/etc/compusona/config.toml`.
4. Dispatch on event name to gather facts (see event table below).
5. Call OpenRouter chat completions with:
   - `system` role = persona contents
   - `user` role = `f"Event facts: {facts}\n\n{prompt_suffix}\n\nGenerate ONE in-character line."`
   - `model` / `temperature` / `max_tokens` from config
   - 8-second timeout
6. If the LLM call fails for any reason (network, timeout, malformed JSON, missing keys), fall back to sending the raw facts string. Never error out without sending _something_.
7. Send the resulting message to Telegram via `sendMessage`. Use `urlencode` on form data. 5-second timeout. Fire-and-forget — failures here are logged to stderr only.
8. Exit 0 always (so systemd never thinks the unit failed and delays shutdown).

### Constraints

- **Stdlib only.** Use `urllib.request` for HTTP. No `requests`, no `httpx`.
- Use `tomllib` for config (stdlib since 3.11).
- Use `subprocess.run(..., capture_output=True, text=True, timeout=5)` for shelling out. Never let a hung subprocess block the script.
- Type-annotate function signatures. Use `match` / `case` for event dispatch.
- Module-level constants for paths and timeouts at the top of the file.
- One file. No package structure. ~200 lines is the target ceiling.

### Event → Facts mapping

Implement these. Each returns a short string (under ~300 chars) suitable to inject into the user prompt. All should degrade gracefully if the underlying command is missing or returns nothing.

| Event               | Facts to gather                                                                                                                                         |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backup_ok`         | Size in MB and file count from `$BACKUP_PATH` (env var, default `/backup/latest`)                                                                       |
| `backup_fail`       | Last line from `journalctl -u backup.service -n 1 --no-pager` if available, else generic failure string                                                 |
| `shutdown`          | `uptime -p` and current load avg from `/proc/loadavg`                                                                                                   |
| `boot`              | First line of `systemd-analyze` (e.g. "Startup finished in 4.2s")                                                                                       |
| `updates_available` | Count of upgradable packages from `dnf check-update -q` (lines starting with alphanumeric); security update count via `dnf updateinfo list security -q` |
| `ups_battery`       | `upsc $UPS_NAME battery.charge` and `upsc $UPS_NAME battery.runtime` (UPS name from env var)                                                            |

Wrap each in a try/except. Missing data → use a sensible default string rather than raising.

For commands that vary by distro (e.g. `dnf` vs `apt`), prefer trying the command and catching `FileNotFoundError` over OS detection.

---

## Systemd units

### `/etc/systemd/system/compusona-shutdown.service`

```ini
[Unit]
Description=compusona farewell on shutdown
DefaultDependencies=no
Before=shutdown.target reboot.target halt.target
Requires=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/true
ExecStop=/usr/local/bin/compusona shutdown
RemainAfterExit=yes
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

The trick that makes a oneshot unit run its `ExecStop` on shutdown: `ExecStart=/bin/true` + `RemainAfterExit=yes` means systemd considers the unit "active" after boot, so it has something to stop on shutdown.

### `/etc/systemd/system/compusona-boot.service`

```ini
[Unit]
Description=compusona greeting on boot
After=network-online.target
Requires=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/compusona boot

[Install]
WantedBy=multi-user.target
```

---

## Acceptance criteria

1. Running `compusona shutdown` on the command line as root produces a Telegram message within ~10 seconds, even with no network (falls back to raw facts).
2. Pointing `OPENROUTER_API_KEY` at a bad value still produces a Telegram message containing the raw facts.
3. `systemctl start compusona-shutdown.service` succeeds and does nothing visible. `systemctl stop compusona-shutdown.service` triggers the shutdown notification.
4. A clean `systemctl poweroff` triggers the notification before network teardown.
5. Editing `/etc/compusona/persona.md` changes the message style on the next run with no script changes.
6. Adding a new `[events.foo]` table to `config.toml` and invoking with `foo` produces a notification (using generic facts) without code changes.
7. Script exits 0 in all observed code paths.
8. `python3 -m py_compile /usr/local/bin/compusona` passes with no warnings on Python 3.12.

---

## Out of scope (v1)

- Retry logic on API failures (one shot, fall back, move on).
- Multiple notification backends (Telegram only for v1; structure code so adding Signl4/Teams/Discord/ntfy is a single function).
- Persistent logging beyond stderr.
- Concurrent event dispatching.
- Templating in the persona file.

---

## Stretch goals

- `--dry-run` flag that prints the would-be message to stdout instead of sending.
- `--event-list` flag that prints all configured event names from `config.toml`.
- Optional `[notifications.telegram]`, `[notifications.signl4]`, `[notifications.ntfy]`, `[notifications.discord]` tables in config.toml so users can switch backends without code edits.
- Structured logging via the stdlib `logging` module to `/var/log/compusona.log`, with rotation handled externally by logrotate.
- Example persona files in `examples/` (e.g. `examples/medieval-knight.md`, `examples/grumpy-mainframe.md`, `examples/cheerful-dog.md`).

---

## License

MIT recommended. The persona files users create are theirs.
