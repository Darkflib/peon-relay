# peon-relay — Build Spec

## What you are building

A lightweight local HTTP relay that receives Claude Code hook events from one or
more agents (including headless), translates them to CESP event categories, and
dispatches to a handler pipeline. Initial handlers: local audio playback
(CESP-compliant) and a structured log handler.

The CESP (Coding Event Sound Pack) open standard is documented at
https://www.openpeon.com/integrate — read it before starting. The spec defines
the `openpeon.json` manifest format, category names, alias resolution, and
audio playback requirements.

---

## Repo layout

```
peon-relay/
├── peon_relay/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, lifespan, startup tasks
│   ├── hooks.py          # /hook endpoint, payload parsing, category mapping
│   ├── queue.py          # Asyncio event queue + drain loop
│   ├── cesp.py           # CESP manifest loader, pack resolution, sound picker
│   ├── handlers/
│   │   ├── __init__.py   # Handler registry, dispatch
│   │   ├── audio.py      # Local audio playback (cross-platform)
│   │   └── log.py        # Structured log handler (always enabled)
│   └── config.py         # Pydantic Settings config model
├── config.example.yaml
├── config.yaml           # gitignored; user copy of example
├── sounds/
│   └── .gitkeep          # Pack storage; contents gitignored
├── systemd/
│   └── peon-relay.service
├── tests/
│   ├── test_hooks.py
│   ├── test_cesp.py
│   └── test_queue.py
├── pyproject.toml
├── .gitignore
└── README.md
```

---

## Dependencies (`pyproject.toml`)

```toml
[project]
name = "peon-relay"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "pyyaml>=6.0",
    "structlog>=24.1",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "httpx", "ruff"]

[project.scripts]
peon-relay = "peon_relay.main:run"
```

---

## Config schema

### `config.example.yaml`

```yaml
server:
  host: "0.0.0.0"
  port: 9876

audio:
  enabled: true
  pack_dir: "sounds"        # relative to CWD or absolute
  active_pack: "peon"
  volume: 0.7               # 0.0–1.0
  mute: false
  disabled_categories: []   # e.g. ["task.progress"]
  debounce_ms: 500          # minimum ms between sounds in same category

log:
  enabled: true
  level: "INFO"
```

### `config.py`

Use `pydantic-settings` with a `BaseSettings` model. Support:

- Loading from `config.yaml` in CWD
- Environment variable overrides prefixed `PEON_`, nested with double underscore,
  e.g. `PEON_AUDIO__MUTE=true`, `PEON_SERVER__PORT=9999`

All sub-sections (`server`, `audio`, `log`) should be nested Pydantic models.

---

## HTTP endpoints

### `POST /hook`

The main intake endpoint. Claude Code sends hook payloads here with
`Content-Type: application/json`. Always returns 2xx. Never blocks on
playback — enqueue and return immediately.

Response: `{"status": "queued", "category": "<resolved-cesp-category-or-null>"}`

### `GET /health`

Returns: `{"status": "ok", "queue_depth": <int>, "active_pack": "<name>"}`

### `GET /packs`

Lists installed packs found in `pack_dir`.

Returns:
```json
[
  {
    "name": "peon",
    "display_name": "Warcraft Peon",
    "categories": ["session.start", "task.complete", "task.error", "input.required"],
    "sound_count": 12,
    "active": true
  }
]
```

### `POST /test/{category}`

Manually enqueue a CESP category event. Useful for verifying audio setup.
Returns `{"status": "queued", "category": "<category>"}` or 404 if the
category is unknown.

---

## Hook payload → CESP category mapping

Claude Code hook payloads arrive as JSON. The key field is `hook_event_name`.
Additional fields vary by hook type.

### Session tracking

Maintain an in-memory dict `{ session_id: last_seen_timestamp }`. On each
incoming event, prune entries last seen more than 4 hours ago. If a
`PreToolUse` event arrives with an unseen `session_id`, emit `session.start`
for that event and record the session. Subsequent `PreToolUse` events from
the same session are silently skipped (no sound).

### Mapping table

| `hook_event_name` | Condition                                      | CESP category      |
|-------------------|------------------------------------------------|--------------------|
| `PreToolUse`      | `session_id` not seen before                   | `session.start`    |
| `PreToolUse`      | `session_id` already known                     | *(skip)*           |
| `PostToolUse`     | no error signal in response                    | `task.complete`    |
| `PostToolUse`     | error signal present                           | `task.error`       |
| `Notification`    | message contains "input" or "approval"         | `input.required`   |
| `Notification`    | message contains "limit" or "quota"            | `resource.limit`   |
| `Notification`    | other                                          | *(skip)*           |
| `Stop`            | —                                              | `session.end`      |

Error detection heuristic for `PostToolUse`: check if `tool_response` contains
a top-level `is_error: true` field, or if the response string contains "error"
or "failed" (case-insensitive). This is a best-effort heuristic; log the raw
payload at DEBUG level so it can be refined.

If `hook_event_name` is missing or unrecognised, log a warning and return
`{"status": "ignored"}`.

---

## Event queue (`queue.py`)

```python
@dataclass
class PeonEvent:
    category: str       # CESP category string
    session_id: str     # from hook payload
    timestamp: float    # time.monotonic()
```

Use `asyncio.Queue`. Start a single drain coroutine in the FastAPI lifespan
context manager.

**Drain loop behaviour:**

1. Pull next event from queue (await)
2. Check debounce: if the same category was last dispatched within
   `debounce_ms`, drop the event and log at DEBUG level
3. Dispatch to each enabled handler concurrently using `asyncio.gather`,
   with a 5-second timeout per handler
4. Catch all handler exceptions individually; log and continue — a broken
   handler must never halt the drain loop
5. Update last-dispatched timestamp for the category

---

## CESP module (`cesp.py`)

Loaded once at startup.

**Pack discovery:**
- Scan all subdirectories of `pack_dir` for `openpeon.json`
- Parse and validate `cesp_version` field (warn but don't crash on unknown versions)
- Resolve `category_aliases` as per the CESP spec: if a category is not in
  `categories`, check `category_aliases` for a mapping, then look up the
  mapped name

**Sound selection — `pick_sound(category) -> Path | None`:**
- Resolve category (with alias fallback) against the active pack
- If category absent in pack, return None silently
- Track last-played sound per category (in-memory dict)
- If more than one sound available, exclude the last-played from candidates
- Pick randomly from remaining candidates
- Return absolute Path to the sound file

**`list_packs() -> list[PackInfo]`:** returns metadata for all discovered packs.

No hot-reload. Restart the service to pick up new packs.

---

## Audio handler (`handlers/audio.py`)

**Player detection:**
Probe available tools once at startup. Cache the first working tool found.
Do not re-probe on every sound.

Detection order:
- macOS: `afplay`
- Linux: `pw-play`, `paplay`, `ffplay`, `mpv`, `play` (SoX), `aplay`
- Windows: PowerShell `System.Windows.Media.MediaPlayer` (via `powershell -Command`)

**Playback:**

```python
async def play(event: PeonEvent, sound_path: Path, volume: float) -> None:
    ...
```

- Use `asyncio.create_subprocess_exec` — never `subprocess.run`
- Route stdout and stderr to `asyncio.subprocess.DEVNULL`
- Scale `volume` (0.0–1.0) to each tool's native range
- If the player process exits non-zero, log a warning with the category and path
- A non-zero exit is not retried; log and move on
- If `config.audio.mute` is True, return immediately without spawning a process
- If the category is in `config.audio.disabled_categories`, skip silently

**Volume scaling per tool:**

| Tool      | Scale                        |
|-----------|------------------------------|
| `afplay`  | `-v {volume}` (0.0–1.0)      |
| `paplay`  | `--volume={int(volume*65536)}` |
| `pw-play` | `--volume={volume}`          |
| `ffplay`  | `-volume {int(volume*100)}`  |
| `mpv`     | `--volume={int(volume*100)}` |
| `play`    | `-v {volume}`                |
| `aplay`   | no volume control            |

---

## Log handler (`handlers/log.py`)

Always enabled regardless of config. Uses structlog.

Emits one structured log line per event dispatched:

```json
{
  "event": "peon.fired",
  "category": "task.complete",
  "session_id": "abc123",
  "pack": "peon",
  "sound": "JobsDone.wav"
}
```

If no sound was resolved (category absent in pack), emit:

```json
{
  "event": "peon.no_sound",
  "category": "task.progress",
  "reason": "category_not_in_pack"
}
```

---

## Handler registry (`handlers/__init__.py`)

Handlers are called in order: `[log, audio]`. Both are instantiated at
startup with their respective config sections. The registry is a simple list;
no dynamic loading needed at this stage.

Each handler implements:

```python
class BaseHandler:
    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        ...
```

`sound_path` may be None if CESP resolution found no sound for the category.
Each handler decides what to do with a None path (log handler always fires;
audio handler skips).

---

## `main.py`

- FastAPI app with lifespan context manager
- On startup: load config, initialise CESP module, start drain coroutine,
  probe audio tool
- On shutdown: cancel drain coroutine cleanly
- Include a `run()` function for the `peon-relay` entry point:

```python
def run():
    uvicorn.run("peon_relay.main:app", host=config.server.host,
                port=config.server.port, reload=False)
```

---

## systemd unit (`systemd/peon-relay.service`)

```ini
[Unit]
Description=Peon Relay — CESP event relay for Claude Code agents
After=network.target sound.target

[Service]
Type=simple
WorkingDirectory=/opt/peon-relay
ExecStart=/opt/peon-relay/.venv/bin/peon-relay
Restart=on-failure
RestartSec=5
Environment=PEON_LOG__LEVEL=INFO

[Install]
WantedBy=multi-user.target
```

---

## Claude Code hook configuration

In each agent's `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "http",
        "url": "http://RELAY_HOST:9876/hook",
        "timeout": 5
      }
    ],
    "PostToolUse": [
      {
        "type": "http",
        "url": "http://RELAY_HOST:9876/hook",
        "timeout": 5
      }
    ],
    "Notification": [
      {
        "type": "http",
        "url": "http://RELAY_HOST:9876/hook",
        "timeout": 5
      }
    ],
    "Stop": [
      {
        "type": "http",
        "url": "http://RELAY_HOST:9876/hook",
        "timeout": 5
      }
    ]
  }
}
```

Claude Code sends the hook payload as the POST body with
`Content-Type: application/json`. A non-2xx response or connection failure
produces a non-blocking error that allows agent execution to continue — the
relay being down must never interrupt an agent.

Replace `RELAY_HOST` with the IP or hostname of the machine running the relay.
For local use: `127.0.0.1`.

---

## Initial pack install (README instructions)

```bash
mkdir -p sounds
curl -fsSL https://github.com/PeonPing/og-packs/archive/refs/tags/v1.1.0.tar.gz \
  | tar xz -C /tmp
cp -r /tmp/og-packs-*/peon sounds/peon
```

Then verify with: `curl -X POST http://localhost:9876/test/session.start`

---

## What not to build

- No authentication (local network assumption; a shared-secret header can be
  added later via `allowedEnvVars` in the hook config)
- No persistence or database
- No web UI
- No pack installer CLI — manual curl is sufficient
- No Windows support beyond noting the PowerShell audio path
- No hot-reload of packs or config — restart is acceptable

---

## Testing notes

- `test_hooks.py`: test category mapping for each `hook_event_name` value,
  including session-tracking logic (first vs. subsequent PreToolUse)
- `test_cesp.py`: test manifest loading, alias resolution, sound picker
  no-repeat logic, missing category returns None
- `test_queue.py`: test debounce logic, handler exception isolation

Use `pytest-asyncio` for async tests. Use `httpx.AsyncClient` with the FastAPI
`app` directly (no running server needed) for endpoint tests.