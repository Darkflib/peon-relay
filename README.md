# peon-relay

> **AI coding agents don't tell you when they finish or need permission.** You tab away, lose focus, and waste fifteen minutes getting back into flow. peon-relay fixes this with game-character voice lines and audio banners — across your whole network.

peon-relay is the **network companion to [peon-ping](https://github.com/PeonPing/peon-ping)**. Where peon-ping runs locally alongside a single agent, peon-relay acts as a central relay — like [Growl](https://growl.github.io/growl/) for AI-agent events — so that agents running on remote or headless machines can still trigger audio notifications on your workstation.

It receives hook events from AI coding agents, translates them to [CESP](https://www.openpeon.com/integrate) sound categories, and plays audio feedback using any installed sound pack — Warcraft Peon, StarCraft Zerg, Portal GLaDOS, Zelda, and more.

## Why it exists

Modern AI coding agents — Claude Code, Amp, GitHub Copilot, Codex, Cursor, OpenCode, Kilo CLI, Kiro, Kimi Code, Windsurf, Rovo Dev CLI, and others — work asynchronously and silently. They finish tasks, hit permission prompts, or run into errors without any notification to you. peon-relay bridges that gap. It sits on your network, receives events from any agent that can send an HTTP POST (or be wrapped with a `curl` one-liner), and makes sure you hear about it.

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

## Other agents and generic curl usage

Any agent or script that can run `curl` can send events to peon-relay — no native HTTP hook support required.

### Signal that a task is finished

```bash
curl -s -X POST http://RELAY_HOST:9876/hook \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"Stop","session_id":"my-session"}'
```

### Signal that input or approval is needed

```bash
curl -s -X POST http://RELAY_HOST:9876/hook \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"Notification","message":"input required","session_id":"my-session"}'
```

### Signal a task error

```bash
curl -s -X POST http://RELAY_HOST:9876/hook \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","tool_response":{"is_error":true},"session_id":"my-session"}'
```

### Wrap any shell command

Append a `curl` call so you get notified when a long-running command finishes:

```bash
my-long-command && curl -s -X POST http://RELAY_HOST:9876/hook \
  -H "Content-Type: application/json" \
  -d "{\"hook_event_name\":\"Stop\",\"session_id\":\"$(date +%s)-$$\"}"
```

Or define a helper function in your shell profile:

```bash
peon_done() {
  curl -s -X POST http://RELAY_HOST:9876/hook \
    -H "Content-Type: application/json" \
    -d "{\"hook_event_name\":\"Stop\",\"session_id\":\"$(date +%s)-$$\"}" \
    > /dev/null
}

# Usage: some-command; peon_done
```

> **Tip:** The `session_id` field is used only for grouping events in logs. Using `$$` (shell PID) ties all commands in the same terminal to one session — fine for most uses. Use `$(date +%s)-$$` for a unique ID per invocation.

Replace `RELAY_HOST` with the IP or hostname of the machine running peon-relay. For local use: `127.0.0.1`.

Use the optional `X-Peon-Pack` header to select a specific sound pack per caller:

```bash
curl -s -X POST http://RELAY_HOST:9876/hook \
  -H "Content-Type: application/json" \
  -H "X-Peon-Pack: zerg" \
  -d '{"hook_event_name":"Stop","session_id":"my-session"}'
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

## Desktop notifications

Desktop popup notifications are enabled by default. When events fire, you'll see OS-native notifications (macOS Notification Center, Linux D-Bus, Windows toast).

Configure in `config.yaml`:

```yaml
notification:
  enabled: true
  disabled_categories: ["task.progress", "task.acknowledge"]
  desktop:
    enabled: true
```

Or via environment variables: `PEON_NOTIFICATION__ENABLED=false`, `PEON_NOTIFICATION__DESKTOP__ENABLED=false`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/hook` | Main intake for agent hook payloads (Claude Code, curl, any HTTP client) |
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
