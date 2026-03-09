# peon-relay

A lightweight local HTTP relay that receives Claude Code hook events, translates them to [CESP](https://www.openpeon.com/integrate) sound categories, and plays audio feedback.

## Quick start

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e .
```

### Install a sound pack

Browse and install packs from the registry:

```bash
# List available packs
curl http://localhost:9876/registry/packs

# Search for packs
curl 'http://localhost:9876/registry/packs?search=warcraft'

# Install a pack
curl -X POST http://localhost:9876/registry/install/peon
```

Or install manually:

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

## Per-agent sound packs

Each agent can use a different sound pack. Resolution order (first match wins):

1. **`X-Peon-Pack` header** — set per agent in hook config
2. **`client_packs`** — IP-to-pack mapping in `config.yaml`
3. **`active_pack`** — the global default

### Header override

Set the `X-Peon-Pack` header in each agent's `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "type": "http",
      "url": "http://127.0.0.1:9876/hook",
      "timeout": 5,
      "headers": { "X-Peon-Pack": "zerg" }
    }],
    "PostToolUse": [{
      "type": "http",
      "url": "http://127.0.0.1:9876/hook",
      "timeout": 5,
      "headers": { "X-Peon-Pack": "zerg" }
    }],
    "Notification": [{
      "type": "http",
      "url": "http://127.0.0.1:9876/hook",
      "timeout": 5,
      "headers": { "X-Peon-Pack": "zerg" }
    }],
    "Stop": [{
      "type": "http",
      "url": "http://127.0.0.1:9876/hook",
      "timeout": 5,
      "headers": { "X-Peon-Pack": "zerg" }
    }]
  }
}
```

### IP-based mapping

Map client IPs to packs in `config.yaml` (useful for remote agents):

```yaml
audio:
  active_pack: "peon"
  client_packs:
    "192.168.1.50": "zerg"
    "192.168.1.51": "marine"
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/hook` | Main intake for Claude Code hook payloads |
| `GET` | `/health` | Health check with queue depth |
| `GET` | `/packs` | List installed sound packs |
| `POST` | `/test/{category}` | Manually trigger a sound category |
| `GET` | `/registry/packs` | Browse available packs from the registry |
| `POST` | `/registry/install/{name}` | Download and install a pack |
| `DELETE` | `/registry/packs/{name}` | Uninstall a pack |

## Pack registry

The server fetches available packs from a registry (default: `https://peonping.github.io/registry/index.json`). Packs are downloaded as GitHub release tarballs, verified by SHA256, and hot-loaded without restart.

Query parameters for `GET /registry/packs`:

| Param | Description |
|-------|-------------|
| `search` | Filter by name, description, or tags |
| `category` | Filter by CESP category support |
| `trust_tier` | Filter by `official` or `community` |

Configure additional registries in `config.yaml`:

```yaml
registry:
  urls:
    - "https://peonping.github.io/registry/index.json"
    - "https://my-company.example.com/peon-registry/index.json"
```

## Config

Environment overrides use `PEON_` prefix with `__` nesting: `PEON_AUDIO__MUTE=true`, `PEON_SERVER__PORT=9999`.
