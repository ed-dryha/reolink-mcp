---
phase: 03-safe-controls-safety-rails
plan: 01
subsystem: control-tools
tags: [mcp, fastmcp, reolink-aio, pydantic-settings, read-only-mode, capability-gating]

# Dependency graph
requires:
  - phase: 02-full-observe-surface
    provides: capabilities.gate()/refusal_message(), CameraManager.get()/configured_host(), classify_reolink_error(), the tools/observe.py plain-undecorated-function + register_all(mcp) registration convention
provides:
  - Settings.read_only (RMCP_READ_ONLY env var, SAFE-02)
  - classify_control_error() — curates InvalidParameterError/NotSupportedError, delegates everything else to classify_reolink_error unchanged
  - server.py module-scope settings load, wired into register_all(mcp, read_only=settings.read_only) at real import time
  - tools/control.py with set_siren, set_spotlight, set_ir_lights, set_white_led — the first 4 of Phase 3's 9 control tools
  - register_all(mcp, read_only) — conditional control-tool registration; six pre-existing observe tools now carry the full destructiveHint/idempotentHint annotation matrix
  - tests/conftest.py collection-time RMCP_CONFIG_FILE stub guaranteeing pytest collection never depends on host camera config state
affects: [03-02-zoom-ptz, 03-03-safety-rail-completion-and-live-qa]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "control tools are plain undecorated async defs in tools/control.py, gated via capabilities.gate() before any host mutation, errors wrapped via classify_control_error(), returning a read-back confirmation dict — same shape as tools/observe.py"
    - "RMCP_READ_ONLY strips control-tool registration at the real module-import boundary (server.py module-scope settings -> register_all(mcp, read_only=...)), not just inside a mocked test"
    - "set_ir_lights' always-on mode reaches the camera via a hand-built host.send_setting() body — the only way to bypass reolink-aio's own Auto/Off-only convenience wrapper"

key-files:
  created:
    - src/reolink_mcp/tools/control.py
    - tests/test_server.py
    - tests/tools/test_control.py
  modified:
    - src/reolink_mcp/config.py
    - src/reolink_mcp/errors.py
    - src/reolink_mcp/server.py
    - src/reolink_mcp/tools/__init__.py
    - tests/conftest.py
    - tests/test_config.py
    - tests/test_errors.py

key-decisions:
  - "D-01/D-02: set_siren defaults to a 5s burst and hard-refuses (never clamps) any duration over 60s, validated before any host call"
  - "D-03/D-04: explicit action=\"stop\" silences the siren immediately; re-sounding while active is allowed with no server-side restart logic needed — the camera's own firmware owns the timer restart"
  - "D-05/D-06/D-07: set_spotlight/set_ir_lights/set_white_led take explicit target-state parameters only (on: bool, mode: Literal[...]), no toggle verbs; set_ir_lights exposes all three native modes including the always-on bypass; set_white_led's mode=None leaves the camera's current mode untouched (no scheduling surface introduced)"
  - "D-13: destructiveHint=True only on set_siren; the other three control tools carry explicit destructiveHint=False/idempotentHint=True; all four are readOnlyHint=False"
  - "D-14: every control tool returns a read-back confirmation dict read from the camera post-call; set_siren is the one documented exception (no live siren-state getter exists in reolink-aio) and echoes the accepted command instead"
  - "D-15: read-only mode omits control-tool registration entirely and logs exactly one logger.warning(...) line — nothing surfaced to the LLM"
  - "BLOCKER 1 completeness fix: the six pre-existing observe tools now carry the full destructiveHint=False/idempotentHint=True matrix, not just readOnlyHint=True, so Plan 03-03's full-registry SAFE-01 test passes against real shipped code"
  - "BLOCKER 1 hermeticity fix: tests/conftest.py sets a throwaway, always-valid RMCP_CONFIG_FILE stub before any reolink_mcp import, so server.py's new module-scope settings load can never crash pytest COLLECTION on a host with no real camera config"

patterns-established:
  - "Control-tool body shape: manager/handle/host,ch three-line fetch -> gate(handle, capability) refusal check -> try/except host mutation wrapped in classify_control_error -> read-back dict return. Plans 03-02/03-03 copy this unchanged."
  - "classify_control_error(exc, camera_name, host) delegates every non-InvalidParameterError/NotSupportedError exception to classify_reolink_error() unchanged — never a duplicated matcher table."

requirements-completed: [CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-10, SAFE-02]

# Metrics
duration: ~40min
completed: 2026-07-10
---

# Phase 3 Plan 1: Read-Only Mode Foundation + Siren/Lighting Controls Summary

**First four of Phase 3's nine control tools (set_siren, set_spotlight, set_ir_lights, set_white_led) with full CTRL-10 capability gating, D-13 annotation matrix, D-14 read-back confirmations, and a real-import-time-proven RMCP_READ_ONLY safety switch.**

## Performance

- **Duration:** ~40 min
- **Completed:** 2026-07-10T08:08:30Z
- **Tasks:** 2 completed
- **Files modified:** 10 (3 created, 7 modified)

## Accomplishments
- `Settings.read_only` (RMCP_READ_ONLY env var) and `server.py`'s module-scope settings refactor wire read-only mode into `register_all(mcp, read_only=settings.read_only)` at real registration time, proven via an `importlib.reload`-based integration test (not just a mocked unit test)
- `classify_control_error()` safely curates `InvalidParameterError`/`NotSupportedError` messages (strips the leading `func_name: ` prefix) and delegates every other exception type to the existing `classify_reolink_error()` matcher unchanged
- `set_siren` implements the full D-01..D-04 safety envelope: 5s default burst, hard 60s cap that refuses (never clamps) over-cap requests, explicit stop, and relies on the camera's own firmware to restart the auto-off timer on a repeat "sound" call
- `set_spotlight`/`set_white_led` share the `"white_led"` capability gate (one physical light, two ergonomics); `set_ir_lights` exposes all three native IR modes including the always-on mode via a hand-built `send_setting()` body that bypasses reolink-aio's Auto/Off-only convenience wrapper
- Six pre-existing observe tools now carry the full `destructiveHint=False`/`idempotentHint=True` annotation matrix (BLOCKER 1 completeness fix), and `tests/conftest.py` gained a collection-time config stub so pytest collection never depends on the host machine's camera config state (BLOCKER 1 hermeticity fix)

## Task Commits

Each task was committed atomically:

1. **Task 1: Read-only mode foundation + error classification + set_siren** - `14a690a` (feat)
2. **Task 2: Spotlight, IR lights, and white LED** - `eb669f5` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/reolink_mcp/tools/control.py` - New module: `set_siren`, `set_spotlight`, `set_ir_lights`, `set_white_led`
- `src/reolink_mcp/config.py` - `Settings.read_only: bool = False`
- `src/reolink_mcp/errors.py` - `classify_control_error()`
- `src/reolink_mcp/server.py` - module-scope `settings = load_settings()`, `register_all(mcp, read_only=settings.read_only)`
- `src/reolink_mcp/tools/__init__.py` - `register_all(mcp, read_only=False)`; full D-13 annotation matrix on all 10 tools registered so far
- `tests/conftest.py` - collection-time `RMCP_CONFIG_FILE` stub (BLOCKER 1 hermeticity fix)
- `tests/test_config.py` - `RMCP_READ_ONLY` default/set tests
- `tests/test_errors.py` - `classify_control_error` prefix-stripping and delegation tests
- `tests/test_server.py` - new: real-import `importlib.reload` test proving RMCP_READ_ONLY strips control tools
- `tests/tools/test_control.py` - new: unit tests for all four control tools plus registration/annotation checks

## Decisions Made
- Followed the plan's exact task split: Task 1 delivers the foundation (read-only mode, error classification, hermeticity fix) plus `set_siren`; Task 2 adds the three lighting tools. Kept `tests/test_server.py`'s tool-count assertion in sync as Task 2 changed the real registered-tool count from 7 to 10 (documented as a deviation below).
- `_ir_settings` private-attribute read in `set_ir_lights` is the one narrow, explicitly `# noqa: SLF001`-commented exception to the "never read private attributes" convention, per Pitfall 3 — no public tri-state IR getter exists in reolink-aio.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated tests/test_server.py's real-import tool-count assertion after Task 2 added three more control tools**
- **Found during:** Task 2 (Spotlight, IR lights, white LED)
- **Issue:** `tests/test_server.py` was written in Task 1 asserting `len(tools) == 7` for the not-read-only case (6 observe + `set_siren` only). Task 2's `register_all()` changes registered three more control tools, so the real, un-mocked registry now returns 10 tools — the Task 1 assertion would fail against the real code after Task 2's changes.
- **Fix:** Updated the assertion from `7` to `10` with a comment explaining the count and noting Plan 03-02 will need its own update when it adds the remaining five control tools.
- **Files modified:** tests/test_server.py
- **Verification:** `uv run pytest tests/test_server.py -v` passes both read-only and not-read-only cases against the real module-scope wiring.
- **Committed in:** `eb669f5` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Necessary to keep the plan's own integration test accurate against the real, shipped registry after Task 2's additions. No scope creep — the plan's `<behavior>` section for Task 2 already specified the 10-tool registration count for `tests/tools/test_control.py`; this deviation only propagated that same fact into the sibling real-import test the plan itself introduced in Task 1.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `tools/control.py`'s gate -> try/except classify_control_error -> read-back-dict shape is proven against four working tools; Plan 03-02 (zoom/PTZ) and Plan 03-03 (remaining safety-rail tools + live QA) copy this pattern directly, no further structural changes needed.
- `RMCP_READ_ONLY` is proven at the real import boundary, not just mocked — Plans 03-02/03-03 only need to update the tool-count literal (10 -> N) as they add tools, and `tests/conftest.py`'s registered-tool-count assertions.
- `classify_control_error()` is ready for Plan 03-02's five remaining tools (`set_zoom`, `list_presets`, `ptz_move_to_preset`, `ptz_position`, `ptz_guard`) to reuse unchanged.
- Full suite (127 tests) passes, including with no real camera config file anywhere on the host (`env -u RMCP_CONFIG_FILE HOME=$(mktemp -d) uv run pytest tests/ -q` exits 0) — BLOCKER 1 fully resolved.
- No PTZ hardware available yet (noted in STATE.md blockers) — out of scope for this plan, relevant to Plan 03-02.

---
*Phase: 03-safe-controls-safety-rails*
*Completed: 2026-07-10*

## Self-Check: PASSED

All created files verified present on disk (src/reolink_mcp/tools/control.py, tests/test_server.py, tests/tools/test_control.py, plus all modified files). Both task commits (`14a690a`, `eb669f5`) verified present in `git log --oneline --all`. Full test suite (127 tests) and `uv run ruff check src/ tests/` re-verified passing immediately before this check.
