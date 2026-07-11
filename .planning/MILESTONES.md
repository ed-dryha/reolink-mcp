# Milestones

## v1.0 MVP (Shipped: 2026-07-11)

**Delivered:** `reolink-mcp` 1.0.0 — a standalone MCP server for Reolink cameras, live on PyPI (`uvx reolink-mcp`) and the MCP Registry (`io.github.ed-dryha/reolink-mcp`): 16 tools (6 observe, 10 control), capability-gated and annotated, with a read-only startup mode — direct to camera, no NVR or home-automation daemon in between.

**Stats:**
- Phases: 4 (17 plans, 32 tasks)
- Timeline: 4 days (2026-07-08 → 2026-07-11)
- Git: 103 commits, ~6,700 LOC Python (src + tests + scripts)
- Release: v1.0.0 tag → OIDC publish pipeline → PyPI + GitHub Release + MCP Registry

**Key accomplishments:**

- Config + session layer: named-map YAML topology with env-var-only secrets (`RMCP_CAMERAS__<name>__PASSWORD`), lazy-connect `CameraManager` with guaranteed logout — proven to coexist with `surveillance-security-ai` on shared cameras across 10 consecutive restarts with zero session-limit errors (HDWR-03)
- Full observe surface (`list_cameras`, `get_snapshot` with sub→main fallback + unconditional ~1280px downscale, `get_device_info`, `get_capabilities`, `get_states`, `get_recent_events`) validated live on the real P437 and P320 (HDWR-01)
- Ten control tools (siren, spotlight, IR, white LED, audio alarm, optical zoom, PTZ presets/position/guard) with universal capability gating (CTRL-10) and read-back confirmations; deterrence + zoom validated on real hardware (HDWR-02), PTZ mock-validated pending hardware
- Safety rails: complete `readOnlyHint`/`destructiveHint` annotation matrix enforced by registry-wide regression tests (SAFE-01), plus `RMCP_READ_ONLY=true` stripping all control tools at startup (SAFE-02)
- Curated error taxonomy: offline / wrong-credentials / session-limit conditions each produce distinct, actionable messages; secret redaction proven on every config failure path (Phase 1 CR-01 closed at milestone close)
- Release engineering: 9-job OS×Python CI matrix + 3-OS packaging smoke (hardware-free, stdout-purity enforced), tag-driven OIDC publish pipeline with TestPyPI rehearsal, README quickstart with copy-paste client configs and the concurrent-session caveat documented

**Requirements:** 31/31 v1 requirements verified complete (see milestones/v1.0-REQUIREMENTS.md). PTZ tools (CTRL-06..09) mock-validated as scoped — live PTZ validation deferred until hardware arrives.

Known deferred items at close: 1 (live PTZ hardware validation — see STATE.md Deferred Items)

---
