---
phase: 03-safe-controls-safety-rails
plan: 02
subsystem: api
tags: [mcp, reolink-aio, ptz, zoom, capability-gating, baichuan]

# Dependency graph
requires:
  - phase: 03-safe-controls-safety-rails
    provides: "Plan 03-01's tools/control.py foundation (gate()/classify_control_error()/registration pattern), Settings.read_only, the D-13 annotation matrix in tools/__init__.py, and tests/conftest.py's collection-time hermeticity stub"
provides:
  - "set_zoom — absolute (0-100 normalized) or relative (bounded, clamped) zoom control via one read-then-absolute-set path"
  - "list_presets — zero-extra-I/O read of camera-defined PTZ presets"
  - "ptz_move_to_preset — name/ID preset resolution, curated unknown-preset error, settle-wait + Baichuan re-poll, preset->position cache write"
  - "ptz_position — forced Baichuan re-poll of pan/tilt, zoom read-back, preset-match lookup within tolerance"
  - "CameraHandle.preset_positions — session-scoped preset ID -> (pan, tilt) cache (new server-owned state, no reolink-aio equivalent)"
  - "CAPABILITY_MAP extended with pan_tilt, ptz_guard (nine entries total)"
  - "tests/conftest.py's mock_host_factory now exposes a working .baichuan mock (create_autospec(Baichuan, instance=True))"
affects: [03-03-ptz-guard-and-live-qa]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "PTZ moves need an explicit settle-wait + host.baichuan.get_ptz_position() re-poll — set_ptz_command's PtzCtrl body does not start with 'Set', so send_setting's auto-refetch never fires"
    - "Server-owned session cache (CameraHandle.preset_positions) for state reolink-aio has no API for at all"
    - "create_autospec(Host, instance=True) does not expose .baichuan — test fixtures need an explicit mock.baichuan = create_autospec(Baichuan, instance=True)"

key-files:
  created: []
  modified:
    - src/reolink_mcp/tools/control.py
    - src/reolink_mcp/tools/__init__.py
    - src/reolink_mcp/manager.py
    - src/reolink_mcp/capabilities.py
    - tests/conftest.py
    - tests/test_capabilities.py
    - tests/test_manager.py
    - tests/test_server.py
    - tests/tools/test_control.py

key-decisions:
  - "set_zoom's relative step silently clamps at the range boundary rather than refusing (unlike the siren's refuse-not-clamp rule) — a low-friction, reversible control per the phase's asymmetric safety design"
  - "PTZ_SETTLE_WAIT_S=2 and PTZ_POSITION_TOLERANCE=40 raw units are documented assumptions pending live-hardware confirmation (no PTZ hardware exists yet — Plan 03-03 covers live zoom validation only)"
  - "list_presets/ptz_move_to_preset/ptz_position are all readOnlyHint=False despite two being pure getters — CONTEXT.md's Phase Boundary lists all nine as 'control tools', so read-only mode strips them too"

patterns-established:
  - "Zoom/PTZ tools follow the exact gate -> host call (wrapped in classify_control_error) -> read-back dict shape Plan 03-01 established for lights/siren"
  - "Unknown-preset errors list available preset names, mirroring Phase 1's unknown-camera self-correcting error style"

requirements-completed: [CTRL-05, CTRL-06, CTRL-07, CTRL-08, CTRL-10]

# Metrics
duration: 15min
completed: 2026-07-10
---

# Phase 3 Plan 2: Zoom & PTZ Presets Summary

**set_zoom (absolute/relative, read-then-bounded-set), list_presets, ptz_move_to_preset (settle-wait + Baichuan re-poll + preset->position cache write), and ptz_position (forced re-poll + tolerance-based preset match) — 8 of 9 control tools now live, all mock-validated including the corrected `Host.baichuan` test fixture.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2 completed
- **Files modified:** 9

## Accomplishments
- `set_zoom` supports both absolute normalized position (0-100) and relative in/out steps via one deterministic, bounded code path — never the continuous `ZoomInc`/`ZoomDec` commands
- `list_presets`, `ptz_move_to_preset`, `ptz_position` implemented against the corrected `Host.baichuan` mock fixture, closing the gap Plan 03-01 flagged (`create_autospec(Host)` alone does not expose `.baichuan`)
- New server-owned session state (`CameraHandle.preset_positions`) fills a genuine reolink-aio API gap — no library call maps preset ID to a pan/tilt position
- `CAPABILITY_MAP` extended to nine entries (`pan_tilt`, `ptz_guard`) — `get_capabilities` (Phase 2) automatically gains both new boolean fields with zero code changes, verified this doesn't break any existing Phase 2 test
- Unknown PTZ preset names refused with a curated error listing available presets (D-09), before any host call
- Full test suite: 157 tests passing, zero real network calls; `ruff check src/ tests/` clean

## Task Commits

Each task was committed atomically:

1. **Task 1: set_zoom — absolute position + relative step (D-08, CTRL-05)** - `c024e34` (feat)
2. **Task 2: PTZ presets, move-to-preset, and position (D-09, D-11, D-12, CTRL-06..08)** - `803a65a` (feat)

_Both tasks were `tdd="true"` in the plan; tests were written alongside the implementation in the same commit per the established Plan 03-01 convention (test file changes bundled with the feature they verify), not as separate RED/GREEN commits._

## Files Created/Modified
- `src/reolink_mcp/tools/control.py` - `set_zoom`, `list_presets`, `ptz_move_to_preset`, `ptz_position` + module constants (`ZOOM_RELATIVE_STEP_PCT`, `PTZ_SETTLE_WAIT_S`, `PTZ_POSITION_TOLERANCE`)
- `src/reolink_mcp/tools/__init__.py` - registers the four new tools; read-only-disabled count updated 4→5→8
- `src/reolink_mcp/manager.py` - `CameraHandle.preset_positions: dict[int, tuple[int, int]]`
- `src/reolink_mcp/capabilities.py` - `CAPABILITY_MAP` grows to nine entries (`pan_tilt`, `ptz_guard`)
- `tests/conftest.py` - `mock_host_factory` now sets `mock.baichuan = create_autospec(Baichuan, instance=True)`
- `tests/test_capabilities.py`, `tests/test_manager.py`, `tests/tools/test_control.py` - new/extended test coverage for all four tools + registration/annotation checks
- `tests/test_server.py` - real-import tool-count assertion updated to match the growing registry (see Deviations)

## Decisions Made
- Relative zoom step size fixed at `ZOOM_RELATIVE_STEP_PCT = 10` (~10% of raw range per step), documented inline as adjustable per RESEARCH.md's explicit recommendation
- `PTZ_SETTLE_WAIT_S = 2` and `PTZ_POSITION_TOLERANCE = 40` are both flagged assumptions pending live-hardware confirmation — no PTZ hardware exists yet
- Relative zoom clamps silently at range boundaries rather than refusing (asymmetric safety design: only the siren gets refuse-not-clamp treatment)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `tests/test_server.py`'s hardcoded tool-count assertions became stale after each task's new tool registration**
- **Found during:** Task 1 (after adding `set_zoom`) and again during Task 2 (after adding three more tools)
- **Issue:** `test_read_only_unset_registers_control_tools_at_real_import` asserted `len(tools) == 10`, a count fixed by Plan 03-01 before this plan's tools existed; adding `set_zoom` made the real count 11, then adding `list_presets`/`ptz_move_to_preset`/`ptz_position` made it 14. The test's own comment explicitly anticipated this ("Plan 03-02 will need its own count update").
- **Fix:** Updated the assertion and its explanatory comment twice, once per task, to track the actual registered count (11 after Task 1, 14 after Task 2).
- **Files modified:** `tests/test_server.py`
- **Verification:** Full suite (`uv run pytest tests/`) passes after each fix.
- **Committed in:** `c024e34` (Task 1), `803a65a` (Task 2)

---

**Total deviations:** 1 category, 2 instances, both auto-fixed (Rule 1 — direct, anticipated consequence of this plan's own tool additions).
**Impact on plan:** No scope creep — purely keeping a pre-existing regression test in sync with intentional new tool registrations the plan itself specifies.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- 8 of 9 control tools complete (`set_siren`, `set_spotlight`, `set_ir_lights`, `set_white_led` from Plan 03-01; `set_zoom`, `list_presets`, `ptz_move_to_preset`, `ptz_position` from this plan)
- `ptz_guard` (CTRL-09) is the only remaining control tool — Plan 03-03's opening task
- The `Host.baichuan` mock-fixture fix is now in place for any future PTZ test in this codebase
- PTZ tools remain mock-validated only (no PTZ hardware yet) — Plan 03-03 covers live zoom validation on the P437; PTZ live validation stays deferred per project state

---
*Phase: 03-safe-controls-safety-rails*
*Completed: 2026-07-10*

## Self-Check: PASSED

All files created/modified verified present on disk; all three task/docs commits (`c024e34`, `803a65a`, `a185c9f`) verified present in `git log --all`.
