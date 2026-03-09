# peon-relay

A lightweight local HTTP relay that receives Claude Code hook events, translates them to [CESP](https://www.openpeon.com/integrate) sound categories, and plays audio feedback.

## Quick start

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e .
```

### Install a sound pack

```bash
mkdir -p sounds
curl -fsSL https://github.com/PeonPing/og-packs/archive/refs/tags/v1.1.0.tar.gz \
  | tar xz -C /tmp
cp -r /tmp/og-packs-*/peon sounds/peon
```

### Configure

```bash
cp config.example.yaml config.yaml
# Edit config.yaml as needed
```

### Run

```bash
peon-relay
```

Verify: `curl -X POST http://localhost:9876/test/session.start`

## Claude Code hook setup

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{ "type": "http", "url": "http://127.0.0.1:9876/hook", "timeout": 5 }],
    "PostToolUse": [{ "type": "http", "url": "http://127.0.0.1:9876/hook", "timeout": 5 }],
    "Notification": [{ "type": "http", "url": "http://127.0.0.1:9876/hook", "timeout": 5 }],
    "Stop": [{ "type": "http", "url": "http://127.0.0.1:9876/hook", "timeout": 5 }]
  }
}
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/hook` | Main intake for Claude Code hook payloads |
| `GET` | `/health` | Health check with queue depth |
| `GET` | `/packs` | List installed sound packs |
| `POST` | `/test/{category}` | Manually trigger a sound category |

## Config

Environment overrides use `PEON_` prefix with `__` nesting: `PEON_AUDIO__MUTE=true`, `PEON_SERVER__PORT=9999`.
