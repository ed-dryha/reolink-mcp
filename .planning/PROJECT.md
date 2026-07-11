# reolink-mcp

## What This Is

An open-source MCP (Model Context Protocol) server for Reolink cameras: lets Claude — or any MCP client — see and control Reolink cameras on the local network. Snapshots, device state, AI detection states, PTZ presets, and deterrence controls (spotlight, siren, IR/white LED). Local network only, no cloud. For anyone running an MCP client (Claude Code, Claude Desktop, etc.) with Reolink cameras on their LAN — starting with the author's own setup.

**Market gap (verified 2026-07-08):** no dedicated Reolink MCP server exists on GitHub, npm, or PyPI. Closest prior art requires a middleman: `dedsxc/mcp-frigate` (needs Frigate NVR), `homeassistant-mcp` (needs Home Assistant). This is a first-mover standalone niche.

## Core Value

A user with a Reolink camera adds `reolink-mcp` to their MCP client config, asks "show me the front door camera," and gets a live snapshot — direct to camera, no NVR or home-automation daemon in between.

## Current State

**v1.0 MVP shipped 2026-07-11** — `reolink-mcp` 1.0.0 live on PyPI (`uvx reolink-mcp`), GitHub Releases, and the MCP Registry (`io.github.ed-dryha/reolink-mcp`, active).

- 16 MCP tools: 6 observe + 10 control, all capability-gated, all annotated (`readOnlyHint`/`destructiveHint`), `RMCP_READ_ONLY` observe-only mode
- ~6,700 LOC Python (src + tests + scripts), 186 tests, 9-job CI matrix + 3-OS packaging smoke, tag-driven OIDC publish pipeline
- Validated live on P437 (siren, spotlight, zoom) and P320 (IR); PTZ preset tools mock-validated — live PTZ validation pending hardware
- Session coexistence with `surveillance-security-ai` proven (10 consecutive restarts, zero session-limit errors)

## Next Milestone Goals

Not yet defined — candidates from the v2 requirements backlog (see milestones/v1.0-REQUIREMENTS.md): event push subscriptions (ONVIF/Baichuan) as the headline feature, NVR/Home Hub channel support, VOD/recording search, streamable HTTP transport. Run `/gsd:new-milestone` to scope.

## Requirements

### Validated

**Observe tools** (validated live on the real P437 and P320):
- [x] `list_cameras` — enumerate configured cameras *(Phase 1: Core Snapshot Slice)*
- [x] `get_snapshot` — live still image returned as an MCP image content block *(Phase 1: Core Snapshot Slice)*
- [x] `get_capabilities` — what a camera supports (PTZ, spotlight, siren, …) *(Phase 2: Full Observe Surface)*
- [x] `get_device_info` — model, firmware, hardware details *(Phase 2: Full Observe Surface)*
- [x] `get_states` — current device state (day/night, LED, siren, detection flags) *(Phase 2: Full Observe Surface)*
- [x] `get_recent_events` — current AI detection states via on-demand polling (person/vehicle/animal flags); no background listener in v1 *(Phase 2: Full Observe Surface)*

**Safe control tools** (Phase 3: Safe Controls & Safety Rails):
- [x] `ptz_move_to_preset` / `list_presets` / `ptz_position` / `ptz_guard` *(Phase 3)*
- [x] `set_siren`, `set_spotlight`, `set_ir_lights`, `set_white_led`, `set_zoom`, `set_audio_alarm` *(Phase 3)*
- [x] MCP tool annotations (`readOnlyHint`, `destructiveHint`) + `RMCP_READ_ONLY` observe-only mode *(Phase 3)*

**Configuration & transport:**
- [x] YAML config + env-var secrets (`RMCP_CAMERAS__<camera_name>__PASSWORD`-style nested overrides over a named camera map — never passwords in YAML) *(Phase 1)*
- [x] stdio transport, runnable via `uvx reolink-mcp` *(Phase 1; proven from real PyPI in Phase 4)*

**Release** (Phase 4: Packaging & Release — v1.0.0 live 2026-07-11):
- [x] Published to PyPI (`reolink-mcp` 1.0.0), listed in the MCP Registry (`io.github.ed-dryha/reolink-mcp`, active) *(Phase 4)*
- [x] CI (GitHub Actions) running ruff + pytest (3 OS × 3 Python) + packaging smoke on PRs, proven live on PR #1 *(Phase 4)*
- [x] End-user docs: README quickstart, Claude Desktop/Code config examples, camera compatibility notes, concurrent-session caveat, `config.example.yaml` *(Phase 4)*

### Active

(v1 milestone complete — no active requirements; next milestone will populate this section)

### Out of Scope

- Risky settings (privacy mask, recording config, encoding, network settings) — deferred past v1; mistakes are user-visible and hard to reverse
- Full ~59-setter surface of reolink-aio — v1 is observe + safe controls only
- Event subscription tools / MCP resources (ONVIF SWN push, Baichuan TCP listener) — post-v1; v1 polls AI states on demand instead
- Streamable HTTP transport + auth for remote clients — post-v1; stdio first
- Wider control surface (auto-tracking, two-way audio talk-down, quick replies, day/night & detection-sensitivity tuning) — post-v1 roadmap candidates
- Broader sensor ecosystem (mmWave presence, contact, vibration via Zigbee/Matter) — future sibling servers/extensions, not this package
- Any dependency on Frigate, Home Assistant, or surveillance-security-ai — standalone is the differentiator

## Context

- Seeded from `PROJECT-SEED.md` (2026-07-08), which captured prior research and locked decisions; that file is superseded by this document.
- Development hardware: real Reolink cameras exist on the author's network but are **shared** with another system (surveillance-security-ai) that also holds `reolink-aio` sessions. Reolink cameras have a concurrent-session limit — dev workflow must tolerate this, and end-user docs must document the caveat.
- Sibling project surveillance-security-ai independently uses `reolink-aio`; no code dependency in either direction.
- CI cannot assume camera hardware — tests run against mocks/fixtures of `reolink-aio`.
- `reolink-aio` 0.21.x is battle-tested (Home Assistant's pin), async, and covers the entire v1 surface: PTZ/presets/patrol/guard, siren, spotlight, IR/white LED, day-night, snapshots, AI-event push.

## Constraints

- **Tech stack**: Python ≥ 3.11 — floor forced by `reolink-aio`
- **Dependencies**: official `mcp` Python SDK (`FastMCP` server class); `fastmcp` 2.x is the fallback if SDK ergonomics fall short
- **Dependencies**: `reolink-aio` 0.21.x for all camera communication
- **Tooling**: uv + hatchling + ruff + pytest/pytest-asyncio
- **Security**: secrets via env vars only, never in YAML config
- **Compatibility**: must coexist with other systems holding sessions on the same cameras (document Reolink concurrent-session limit)
- **Distribution**: PyPI package runnable via `uvx reolink-mcp`; MCP registry listing

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| v1 scope: observe + safe controls only | Deterrence toggles are reversible; risky settings (privacy mask, recording/network config) are not — defer them | ✓ Good (v1.0) — clean 16-tool surface, no incident-prone setters shipped |
| Direct-to-camera architecture | Standalone product; the niche exists precisely because prior art requires Frigate/Home Assistant | ✓ Good (v1.0) — zero middleman deps, first-mover listing live |
| Separate OSS project (own repo, PyPI package, license, CI) | No coupling to surveillance-security-ai in either direction | ✓ Good (v1.0) — published independently, coexistence proven at the session level only |
| `get_recent_events` polls current AI states in v1 | No background listener complexity in v1; push subscriptions (ONVIF/Baichuan) deferred to post-v1 | ✓ Good (v1.0) — shipped simple; push remains the headline post-v1 feature |
| MCP tool annotations (`readOnlyHint`/`destructiveHint`) | Lets clients gate approvals on observe vs. control without server-side policy | ✓ Good (v1.0) — enforced by registry-wide regression tests (SAFE-01) |
| Official `mcp` SDK (FastMCP) over `fastmcp` 2.x | Prefer official SDK; fallback documented if ergonomics fall short | ✓ Good (v1.0) — ergonomics sufficed; fallback never needed. ⚠ Revisit at mcp v2 stable (FastMCP→MCPServer rename) |
| v1 milestone ends at published release, ordered local-first | Get everything working locally in early phases; packaging/publish/docs are the closing phases of the same milestone | ✓ Good (v1.0) — empty repo → published package in 4 days |
| Named-map config (`dict[str, CameraConfig]`), not a list | pydantic-settings env/YAML `deep_update` merge only recurses on dict-dict keys; list-index env overrides can't merge (Phase 1 research) | ✓ Good (v1.0) — env-var password overlay works exactly as designed |
| Unconditional server-side snapshot downscale (~1280px, Pillow) | 4K/8MP frames blow client token/size limits; never trust the camera's resolution | ✓ Good (v1.0) — live images from both cameras never exceeded limits |
| Secret redaction: error messages built only from `ValidationError.errors()` loc/type, never `str(e)` | pydantic v2 embeds plaintext field values (`input_value=...`) in `str(ValidationError)` | ✓ Good (v1.0) — two leak paths found and closed (01-05, milestone close); regression-tested |
| Trusted Publishing (OIDC) for PyPI, no stored tokens | No long-lived secrets in GitHub; TestPyPI rehearsal before real publish | ✓ Good (v1.0) — v1.0.0 published cleanly via tag-driven pipeline |
| `set_audio_alarm` added mid-Phase-3 checkpoint (10th control tool) | Live QA discovered the siren fires silently unless the audio alarm is enabled — user-directed addition | ✓ Good (v1.0) — fixed a real field issue before release |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-07-12 after v1.0 milestone completion*
