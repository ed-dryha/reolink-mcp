<!-- GSD:project-start source:PROJECT.md -->
## Project

**reolink-mcp**

An open-source MCP (Model Context Protocol) server for Reolink cameras: lets Claude — or any MCP client — see and control Reolink cameras on the local network. Snapshots, device state, AI detection states, PTZ presets, and deterrence controls (spotlight, siren, IR/white LED). Local network only, no cloud. For anyone running an MCP client (Claude Code, Claude Desktop, etc.) with Reolink cameras on their LAN — starting with the author's own setup.

**Market gap (verified 2026-07-08):** no dedicated Reolink MCP server exists on GitHub, npm, or PyPI. Closest prior art requires a middleman: `dedsxc/mcp-frigate` (needs Frigate NVR), `homeassistant-mcp` (needs Home Assistant). This is a first-mover standalone niche.

**Core Value:** A user with a Reolink camera adds `reolink-mcp` to their MCP client config, asks "show me the front door camera," and gets a live snapshot — direct to camera, no NVR or home-automation daemon in between.

### Constraints

- **Tech stack**: Python ≥ 3.11 — floor forced by `reolink-aio`
- **Dependencies**: official `mcp` Python SDK (`FastMCP` server class); `fastmcp` 2.x is the fallback if SDK ergonomics fall short
- **Dependencies**: `reolink-aio` 0.21.x for all camera communication
- **Tooling**: uv + hatchling + ruff + pytest/pytest-asyncio
- **Security**: secrets via env vars only, never in YAML config
- **Compatibility**: must coexist with other systems holding sessions on the same cameras (document Reolink concurrent-session limit)
- **Distribution**: PyPI package runnable via `uvx reolink-mcp`; MCP registry listing
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Verdict on the Seeded Stack
- **Image content blocks:** `from mcp.server.fastmcp import FastMCP, Image` — the `Image` helper is exported in v1.28.1 and converts to an MCP `ImageContent` block (base64 + MIME type) automatically. `get_snapshot` returning `Image(data=jpeg_bytes, format="jpeg")` is a one-liner. HIGH confidence.
- **Tool annotations:** `FastMCP.tool()` in v1.28.1 accepts `annotations: ToolAnnotations | None`, and `ToolAnnotations` carries `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint` (serialized as camelCase `readOnlyHint` etc. on the wire). The observe/control gating requirement is fully supported. HIGH confidence.
- **stdio transport:** first-class in v1.x (`mcp.run(transport="stdio")` default). HIGH confidence.
## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | >= 3.11 | Runtime | Floor forced by `reolink-aio` (`requires_python >= 3.11`); mcp SDK only needs 3.10, so reolink-aio is the binding constraint |
| `mcp` (official SDK) | 1.28.1, pin `>=1.28,<2` | MCP server framework (`FastMCP` class) | Official reference implementation; v1.x is the stable production line; verified support for tools, ToolAnnotations, Image content, stdio. The `<2` bound is mandated by the SDK's own README while v2 is in beta |
| `reolink-aio` | 0.21.3, pin `~=0.21.3` | All camera communication (login, snapshot, PTZ, siren, spotlight, LEDs, AI states) | Exactly matches Home Assistant's current pin (`reolink-aio==0.21.3` in HA dev, integration quality scale "platinum") — the most battle-tested Reolink library that exists. Async (aiohttp-based), actively released (0.21.3 on 2026-06-26). Covers the entire v1 tool surface |
| `pydantic` | 2.13.4 | Data models (camera config, tool result schemas) | Already a transitive dependency of the mcp SDK; FastMCP uses it for tool schemas — zero added weight |
| `pydantic-settings[yaml]` | 2.14.2 | YAML config + env-var secret overrides | `YamlConfigSettingsSource` (via `[yaml]` extra, pulls `pyyaml>=6.0.1`) handles the YAML file; `env_nested_delimiter="__"` handles `RMCP_CAMERAS__0__PASSWORD`-style nested overrides. Both features verified in current source/docs |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `aiohttp` | (transitive) | HTTP client | Comes with reolink-aio — do not add a second HTTP client (no httpx) |
| `PyYAML` | 6.0.3 (transitive via `pydantic-settings[yaml]`) | YAML parsing | Never import directly; go through `YamlConfigSettingsSource` |
| `anyio` | (transitive via mcp) | Async runtime abstraction | mcp SDK dependency; write plain asyncio code, it interoperates |
### Development Tools
| Tool | Version | Purpose | Notes |
|------|---------|---------|-------|
| uv | 0.11.28 | Package/project manager, lockfile, `uvx` distribution path | The mcp SDK's own docs recommend uv. `uvx reolink-mcp` end-user story requires only a `[project.scripts]` entry point |
| hatchling | 1.31.0 | Build backend | Boring, standard, works with uv. (`uv_build` is a newer alternative backend; hatchling is the safer, more widely documented pick) |
| ruff | 0.15.20 | Lint + format | Replaces black/isort/flake8 entirely; pin loosely (`>=0.15`) in dev deps, CI uses lockfile |
| pytest | 9.1.1 | Test runner | pytest 9 is current; compatible with pytest-asyncio 1.4 (which requires `pytest>=8.4,<10`) |
| pytest-asyncio | 1.4.0 | Async test support | Use `asyncio_mode = "auto"` in config; 1.x removed the old implicit mode, so don't copy pre-1.0 examples |
## Installation
# Project init (uv-managed)
# Core runtime deps
# Dev dependencies
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Official `mcp` SDK (FastMCP, v1.x) | `fastmcp` 3.4.3 (standalone, jlowin) | Only if you need its extras: OpenAPI-to-tools providers, proxying, middleware/transforms, component versioning, Redis background tasks. None of that applies to v1 scope. Third-party dependency with its own major-version churn (2.x → 3.x in Feb 2026) |
| `mcp` 1.28.x | `mcp` 2.0.0b1 | Don't — beta, breaking API (`MCPServer` rename, `mcp-types` split). Revisit at v2 stable as a post-v1 migration task; a migration guide exists in the SDK repo |
| `reolink-aio` | raw Reolink HTTP CGI / Baichuan protocol | Never for this project — reinventing a platinum-quality HA dependency |
| `pydantic-settings[yaml]` | `PyYAML` + hand-rolled env merging | Only if config needs exceed what settings sources model (they don't) |
| hatchling | `uv_build` backend | Fine choice for a new project, but hatchling has broader ecosystem documentation; either works with `uvx` |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `mcp` without a `<2` upper bound | v2 beta is on PyPI now; stable v2 will break `from mcp.server.fastmcp import FastMCP` the moment a resolver picks it up | `mcp>=1.28,<2` |
| `fastmcp` 2.x as the documented fallback | Superseded — 3.x is current (3.0 shipped 2026-02-18); 2.x is in backport-only maintenance | If fallback is ever needed, it's `fastmcp>=3` |
| `httpx` or `requests` | reolink-aio already brings aiohttp; a second HTTP stack adds weight for nothing | aiohttp (transitive) |
| `python-dotenv` directly | pydantic-settings already vendors dotenv support and does typed validation | `pydantic-settings` |
| `asynctest`, `pytest-aiohttp` | Abandoned/unnecessary; stdlib `unittest.mock.AsyncMock` + pytest-asyncio cover mocking reolink-aio | `pytest-asyncio` + `AsyncMock` |
| Passwords in YAML config | Project security constraint | env vars via `env_nested_delimiter="__"` overrides |
## Stack Patterns by Variant
- Stay on v1.x for the v1 release; schedule a post-v1 migration phase (`FastMCP` → `MCPServer`, snake_case fields, `mcp-types` import changes). The rename is mechanical; the decorator API survives.
- This is a runtime/architecture concern, not a stack change — reolink-aio's `Host` object holds one session; keep exactly one `Host` per camera per server process and document the limit for end users.
- The `Image` helper takes raw bytes; downscale/re-encode with `Pillow` before wrapping — add Pillow only if this proves necessary (not in the default dep set).
## Version Compatibility
| Package | Compatible With | Notes |
|-----------|-----------------|-------|
| mcp 1.28.1 | Python >= 3.10 | Project floor 3.11 satisfies it |
| reolink-aio 0.21.3 | Python >= 3.11 | The binding Python floor; deps: aiohttp, aiortsp, orjson, pycryptodomex |
| pydantic-settings 2.14.2 | pydantic >= 2.7 | mcp SDK's pydantic requirement is compatible; single pydantic v2 tree |
| pytest-asyncio 1.4.0 | pytest >= 8.4, < 10 | pytest 9.1.1 is in range — verified from package metadata |
| pydantic-settings[yaml] | pyyaml >= 6.0.1 | `YamlConfigSettingsSource` exported from package root |
## Sources
- PyPI JSON API (2026-07-08) — current versions + `requires_dist`/`requires_python` for mcp, fastmcp, reolink-aio, pydantic, pydantic-settings, uv, hatchling, ruff, pytest, pytest-asyncio — HIGH
- github.com/modelcontextprotocol/python-sdk, tag `v1.28.1` — README (v1 stable/maintenance, `<2` pin guidance), `src/mcp/server/fastmcp/__init__.py` (`FastMCP`, `Image`, `Audio` exports), `server.py` (`tool(annotations=ToolAnnotations)`) — HIGH
- Context7 `/modelcontextprotocol/python-sdk` — `ToolAnnotations` fields (read_only_hint/destructive_hint/idempotent_hint/open_world_hint, camelCase wire aliases), Image → ImageContent conversion; note Context7 indexes main (v2), cross-checked against the v1.28.1 tag — HIGH
- github.com/modelcontextprotocol/python-sdk/releases — v2.0.0a1–b1 timeline (June 2026), v1.28.x dates — HIGH
- github.com/home-assistant/core (dev) `components/reolink/manifest.json` — `reolink-aio==0.21.3`, quality scale platinum — HIGH
- gofastmcp.com/updates — FastMCP 3.0 release (2026-02-18), 2.x maintenance status — MEDIUM (official project site, single source)
- github.com/pydantic/pydantic-settings (main) — `YamlConfigSettingsSource` export, `[yaml]` extra; pydantic.dev docs — `env_nested_delimiter` — HIGH
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
