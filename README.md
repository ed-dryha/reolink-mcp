# reolink-mcp

An open-source MCP (Model Context Protocol) server for Reolink cameras: lets
Claude — or any MCP client — see and control Reolink cameras on the local
network. Snapshots, device state, AI detection states, PTZ presets, and
deterrence controls (spotlight, siren, IR/white LED). Local network only, no
cloud.

**Market gap (verified 2026-07-08):** no dedicated Reolink MCP server exists
on GitHub, npm, or PyPI. Closest prior art requires a middleman:
`dedsxc/mcp-frigate` (needs Frigate NVR), `homeassistant-mcp` (needs Home
Assistant). This is a first-mover standalone niche.

A user with a Reolink camera adds `reolink-mcp` to their MCP client config,
asks "show me the front door camera," and gets a live snapshot — direct to
camera, no NVR or home-automation daemon in between.

<!-- mcp-name: io.github.ed-dryha/reolink-mcp -->

## Quickstart

Requires a Reolink camera reachable on the local network and its admin
username/password.

### 1. Create your camera config

Copy [`config.example.yaml`](config.example.yaml) to
`~/.config/reolink-mcp/config.yaml` (or point `RMCP_CONFIG_FILE` at a copy
anywhere else on disk):

```yaml
cameras:
  front_door:
    host: 192.168.1.44
    username: admin
```

Passwords never go in this file. Set one `RMCP_CAMERAS__<name>__PASSWORD`
environment variable per camera instead — `<name>` must exactly match the
camera's key in the YAML above (lowercase snake_case):

```bash
RMCP_CAMERAS__front_door__PASSWORD=<camera-password>
```

> **Concurrent-session limit:** Reolink cameras allow only a limited number
> of simultaneous sessions. `reolink-mcp` holds one `reolink-aio` session per
> configured camera for as long as the server runs — running it alongside
> another system that also holds sessions on the same cameras (an NVR,
> another MCP server, etc.) can exhaust that limit. See
> [Camera compatibility & session limits](#camera-compatibility--session-limits)
> below.

### 2. Add it to your MCP client

**Claude Code:**

```bash
claude mcp add reolink -- uvx reolink-mcp
```

**Claude Desktop** (`claude_desktop_config.json` — macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`, Windows:
`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "reolink": {
      "command": "uvx",
      "args": ["reolink-mcp"],
      "env": {
        "RMCP_CAMERAS__front_door__PASSWORD": "<camera-password>"
      }
    }
  }
}
```

**Any other MCP client** (generic stdio):

```json
{
  "command": "uvx",
  "args": ["reolink-mcp"]
}
```

That's it — ask your MCP client to "show me the front door camera."

## Tools

Built on [`reolink-aio`](https://github.com/starkillerOG/reolink_aio) and the
official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
Every tool carries `readOnlyHint`/`destructiveHint` annotations so MCP
clients can gate approvals — Observe tools are read-only, Control tools
mutate camera state, and `set_siren` is the only tool marked destructive.

| Tool | Type | Purpose | Destructive |
|------|------|---------|-------------|
| `list_cameras` | Observe | Enumerate every configured camera with live connection status, model, and host | No |
| `get_device_info` | Observe | Model, firmware, hardware details for a camera | No |
| `get_capabilities` | Observe | What a camera supports — PTZ, siren, spotlight, IR/white LED, zoom, AI detection types — in neutral feature vocabulary | No |
| `get_states` | Observe | Current device state: day/night mode, white LED/spotlight, IR lights, siren capability, and motion | No |
| `get_recent_events` | Observe | Current AI detection state (person/vehicle/pet plus any camera-reported extras) from an on-demand poll, plus the plain motion flag | No |
| `get_snapshot` | Observe | Live still image from a camera, returned as an MCP image content block | No |
| `set_siren` | Control | Sound or stop a camera's siren — ~5s safe default, 60s hard cap, over-cap requests refused not clamped | **Yes** |
| `set_audio_alarm` | Control | Enable or disable a camera's siren/audio-alarm feature — needed when `set_siren` is accepted by the firmware but produces no sound | No |
| `set_spotlight` | Control | Turn a camera's spotlight on or off | No |
| `set_ir_lights` | Control | Set a camera's IR lights to `auto`, `on`, or `off` | No |
| `set_white_led` | Control | Turn a camera's white LED on or off, with optional brightness | No |
| `set_zoom` | Control | Zoom a camera to an absolute position (0-100) or a relative in/out step | No |
| `list_presets` | Control | List a camera's named PTZ presets | No |
| `ptz_move_to_preset` | Control | Move a camera to a named (or numeric) PTZ preset | No |
| `ptz_position` | Control | Read a camera's current pan/tilt/zoom position, naming the nearest saved preset when close | No |
| `ptz_guard` | Control | Configure a camera's PTZ guard point and auto-return (`set`/`goto`/`enable`/`disable`) | No |

## Safety

- **`RMCP_READ_ONLY=true`** starts the server with all 10 control tools
  stripped at startup — they are simply never registered, not hidden. A
  one-line notice is printed to stderr; the client sees a clean,
  observe-only server with no control tools in its registry at all.
- **`set_siren`** defaults to a ~5s burst when no duration is given, and
  refuses (never silently clamps) any request over its 60-second hard cap —
  a clear error asks for a shorter duration instead.
- **Capability gating**: every control tool checks the target camera's
  actual capabilities before sending any command, and refuses cleanly (e.g.
  "camera 'front_door' has no siren") instead of letting a raw API error
  from an unsupported feature reach the client.

## Camera compatibility & session limits

Validated live during development on:

| Camera | Validated capabilities |
|--------|-------------------------|
| P437 | Siren, spotlight, zoom |
| P320 | IR lights |

PTZ tools (`list_presets`, `ptz_move_to_preset`, `ptz_position`,
`ptz_guard`) are mock-validated only — real PTZ hardware validation is
pending.

**Concurrent-session limit:** `reolink-aio` maintains one long-lived session
per camera for the lifetime of the server process. Reolink cameras cap the
number of simultaneous sessions a camera will accept; running `reolink-mcp`
alongside another system that independently holds sessions on the same
cameras (an NVR, Reolink's own app, a second automation stack) can hit that
cap. This project's own development setup runs against cameras shared with a
second, independent system and has been exercised under that exact
coexistence condition — if you see login/session errors, check what else
currently holds a session on the camera first.

## License

MIT
