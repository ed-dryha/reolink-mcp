---
phase: 02-full-observe-surface
plan: 04
subsystem: testing
tags: [qa-harness, observe-tools, reolink-aio, pytest, mcp]

# Dependency graph
requires:
  - phase: 02-full-observe-surface
    provides: get_capabilities/get_states/get_recent_events (Plans 02-01/02-02) and the qa_phase2.py structural QA harness (Plan 02-03) this plan repairs
provides:
  - "scripts/qa_phase2.py's check_states validator (STATE_FIELD_VALIDATORS) matching get_states' real per-field return contract — fixes CR-01"
  - "RAW_TO_FRIENDLY_AI_TYPES module constant in observe.py, shared by get_capabilities and get_recent_events (WR-02)"
  - "get_states(full=True)'s status_led capability-gated instead of fabricated False (WR-03)"
  - "tests/test_qa_phase2_validators.py — hardware-free regression proof of the CR-01 fix"
affects: [phase-2-verification, human-uat-test-3]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-field validator dict (STATE_FIELD_VALIDATORS) replacing a blanket type-union check, for QA harnesses validating heterogeneous tri-state fields"
    - "Module-level shared vocabulary-conversion constant (RAW_TO_FRIENDLY_AI_TYPES) consumed by multiple tool functions instead of duplicated inline dict literals"

key-files:
  created:
    - tests/test_qa_phase2_validators.py
  modified:
    - scripts/qa_phase2.py
    - src/reolink_mcp/tools/observe.py
    - tests/tools/test_observe.py

key-decisions:
  - "CR-01 fix implements 02-REVIEW.md's exact suggested STATE_FIELD_VALIDATORS replacement rather than a novel design — the fix was already fully specified"
  - "get_capabilities' ai_detection_types now matches get_recent_events' friendly-name vocabulary (person/pet), with raw wire keys still available under full=True as raw_ai_types, mirroring get_recent_events' own full=True shape exactly"
  - "status_led gates on the raw host.supported(ch, 'status_led') string directly (not capabilities.gate()) since it is a full=True-only diagnostic field with no CAPABILITY_MAP entry — same precedent as the adjacent audio_alarm_enabled field"

requirements-completed: [HDWR-01]

# Metrics
duration: ~25min
completed: 2026-07-09
---

# Phase 2 Plan 4: QA Harness CR-01 Fix + observe.py Vocabulary/Gating Consistency Summary

**Fixed the qa_phase2.py check_states validator that guaranteed FAIL on every camera with day/night or siren support (CR-01), plus made get_capabilities' AI-type vocabulary and get_states(full=True)'s status_led gating consistent with the rest of the observe surface.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-09T20:18:00+03:00 (approx.)
- **Completed:** 2026-07-09T20:24:06+03:00
- **Tasks:** 3 of 3 completed (Task 3 checkpoint approved with live-hardware evidence — see "Task 3 Live-Hardware Verification" below)
- **Files modified:** 3 modified, 1 created

## Accomplishments

- Fixed CR-01: `scripts/qa_phase2.py`'s `check_states` validator now matches `get_states`' actual per-field return contract (`day_night`: non-empty string or `"unsupported"`; `white_led`: dict or `"unsupported"`; `ir_lights`: bool or `"unsupported"`; `siren`: literal `"supported"`/`"unsupported"`) instead of a blanket bool/dict/`"unsupported"` check that rejected the server's own correct output
- Added `tests/test_qa_phase2_validators.py` — a hardware-free regression test that imports `scripts/qa_phase2.py` directly and proves the fix, including the exact 02-VERIFICATION.md repro payload producing zero problems
- Fixed WR-02: `get_capabilities`' `ai_detection_types` now returns friendly names (`"person"`/`"pet"`) matching `get_recent_events`'s vocabulary instead of leaking raw wire keys (`"people"`/`"dog_cat"`); raw values still available under `full=True` as `raw_ai_types`
- Fixed WR-03: `get_states(full=True)`'s `status_led` now reports `"unsupported"` when the camera has no status LED, instead of a fabricated `False`, bringing it into compliance with the tool's own D-09 discipline
- De-duplicated the raw-to-friendly AI-type conversion into a single module constant `RAW_TO_FRIENDLY_AI_TYPES`, consumed by both `get_capabilities` and `get_recent_events` (previously an inline dict literal duplicated only in `get_recent_events`)

## Task Commits

Each task was committed atomically, following RED/GREEN TDD discipline:

1. **Task 1: Fix CR-01 — qa_phase2.py check_states validator**
   - `c90af56` (test) — add failing regression test for CR-01 qa_phase2 state validators (RED — confirmed `ImportError: cannot import name 'STATE_FIELD_VALIDATORS'` against pre-fix code)
   - `c997fed` (fix) — CR-01: qa_phase2 check_states validator matches get_states' real contract (GREEN)
2. **Task 2: Fix WR-02 (friendly AI-type vocabulary) and WR-03 (status_led gating)**
   - `e7ae03a` (test) — add failing tests for WR-02/WR-03 (RED — confirmed 3 of 4 new/updated assertions failed against pre-fix `observe.py`; the 4th, `test_get_states_full_true_status_led_reports_state_when_supported`, verifies preserved passthrough behavior and was expected to pass in both states per the plan's own `<behavior>` spec)
   - `a0ffa5c` (feat) — WR-02/WR-03: friendly AI-type vocabulary and status_led capability gating (GREEN)
3. **Task 3: Re-run scripts/qa_phase2.py against real hardware (checkpoint:human-verify)** — no code commits by design; approved with live-run evidence (see "Task 3 Live-Hardware Verification" below)

**Plan metadata:** SUMMARY.md commit (this plan, worktree mode — orchestrator merges and updates STATE.md/ROADMAP.md centrally)

_Note: Both tasks used `tdd="true"` — each has a `test(...)` RED commit followed by a `feat(...)`/`fix(...)` GREEN commit._

## Files Created/Modified

- `scripts/qa_phase2.py` - `STATE_FIELD_VALIDATORS` dict of per-field validator callables replaces `STATE_TRISTATE_FIELDS`'s blanket check; docstring updated to state the real per-field contract
- `tests/test_qa_phase2_validators.py` - New hardware-free test file: one test per validator's accept/reject behavior, plus the CR-01 regression repro-payload test
- `src/reolink_mcp/tools/observe.py` - `RAW_TO_FRIENDLY_AI_TYPES` module constant added; `get_capabilities` applies it to `ai_detection_types` and adds `raw_ai_types` under `full=True`; `get_recent_events`'s inline dict literal replaced with the shared constant (pure de-dup); `get_states(full=True)`'s `status_led` now capability-gated via `host.supported(ch, "status_led")`
- `tests/tools/test_observe.py` - Updated `test_get_capabilities_maps_curated_keys_and_ai_types`'s assertion to friendly names; added `test_get_capabilities_full_true_includes_raw_ai_types`, `test_get_states_full_true_status_led_unsupported_when_capability_absent`, `test_get_states_full_true_status_led_reports_state_when_supported`

## Decisions Made

- Implemented 02-REVIEW.md's exact suggested `STATE_FIELD_VALIDATORS` replacement for CR-01 rather than designing a new validator scheme — the fix was already fully specified by the code reviewer and independently re-confirmed by 02-VERIFICATION.md's hardware-free repro
- Kept `status_led`'s gating on the raw `host.supported(ch, "status_led")` string (not `capabilities.gate()`) since it has no `CAPABILITY_MAP` entry and is a `full=True`-only diagnostic field — matches the existing `audio_alarm_enabled` field's own gating style exactly, per the plan's interfaces section
- `get_recent_events`'s conversion-loop change is a pure de-duplication (swapping an inline dict literal for the new shared module constant) with no behavior change, verified by the full existing test suite passing unchanged

## Task 3 Live-Hardware Verification (checkpoint approved)

The operator restored camera network connectivity and the orchestrator ran `uv run python scripts/qa_phase2.py` from this plan's worktree (i.e. with the CR-01 fix applied) against the real P437 (`front_door`, 192.168.1.126) and P320 (`front_left`, 192.168.1.170). Results:

| Check | Result | Evidence |
|-------|--------|----------|
| 1. Device info | FAIL (both) | `serial=None` on both cameras; all other fields populated (model=`'P437'`/`'P320'`, firmware, hardware, mac). See "Discovered During Live QA" below — out of this plan's scope |
| 2. Capabilities | PASS (both) | Contrast table correct: P437 zoom/white_led/siren=True; P320 zoom/white_led=False, siren=True; both ptz_presets=False; day_night/motion True |
| 3. States | PASS (both) | front_door: `day_night='Auto'`, `white_led={'on': False, 'brightness': 85}`, `ir_lights=True`, `siren='supported'`, `motion=False`, sane polled_at/age_seconds. front_left: `day_night='Auto'`, `white_led='unsupported'`, `ir_lights=True`, `siren='supported'`, `motion=False` |
| 4. Recent events | PASS (both) | person/vehicle/pet=`'not_detected'`, `motion=False`, sane polled_at/age_seconds |

Exit code 1 — solely due to the device-info `serial=None` finding, which is unrelated to anything this plan touched.

**Acceptance outcome:** the plan's acceptance criterion is met. `check_states` now PASSes on BOTH cameras with the server's own legitimate `day_night` string and `siren='supported'` values — this exact output was guaranteed-FAIL before the `STATE_FIELD_VALIDATORS` fix. CR-01 is closed with real-hardware evidence, and Human-UAT test #3's signal is now trustworthy. `check_capabilities` and `check_recent_events` show no regression from Task 2's `observe.py` changes.

### Discovered During Live QA (out of scope — open item for verification)

- **`serial=None` on both real cameras (P437 and P320):** `check_device_info` FAILs its non-empty-serial requirement on real hardware because `get_device_info`'s current path returns `serial=None` for both cameras. This plan (02-04) never touched `get_device_info` or `check_device_info` — the issue predates this plan and was only surfaced now because this is the first live harness run since connectivity was restored. **Not fixed here by design.** Needs a decision in phase verification: fetch the serial differently (different reolink-aio accessor / poll) vs. relax the harness's non-empty-serial requirement (e.g. treat `None` as acceptable on models that don't expose it).

## Deviations from Plan

None - plan executed exactly as written. Tasks 1 and 2 were implemented and mock-tested; Task 3's live verification was performed by the operator/orchestrator against real hardware and approved. The `serial=None` device-info finding discovered during the live run is outside this plan's scope (Rule: scope boundary — pre-existing issue in files this plan never touched) and is logged above as an open item rather than fixed here.

## Issues Encountered

- Ruff flagged the initial single-line `RAW_TO_FRIENDLY_AI_TYPES = {...}` definition as `E501 line too long`. Reformatted to a multi-line dict literal; re-ran `ruff check` and the full test suite to confirm no other regressions. This was a cosmetic formatting fix within the same task, not tracked as a separate deviation.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

All three tasks are complete:
- `uv run pytest tests/test_qa_phase2_validators.py -q` — 9 passed
- `uv run pytest tests/tools/test_observe.py -q` — 50 passed
- `uv run pytest tests/ -q` — 91 passed, zero regressions
- `uv run ruff check src/ tests/ scripts/` — clean
- Task 3 live-hardware run: `check_states`/`check_capabilities`/`check_recent_events` PASS on both real P437 and P320 — CR-01 closed with live evidence, Human-UAT test #3's signal now trustworthy

**Open item carried to verification:** `serial=None` on both real cameras causes `check_device_info` to FAIL its non-empty-serial requirement (harness exit code 1). Pre-existing issue in `get_device_info`/`check_device_info` — neither touched by this plan. Needs a verification-phase decision: fetch the serial via a different reolink-aio path, or relax the harness requirement for models that don't expose a serial.

---
*Phase: 02-full-observe-surface*
*Completed: 2026-07-09 (all 3 tasks; Task 3 checkpoint approved with live-hardware evidence)*

## Self-Check: PASSED

All created/modified files verified present on disk; all 5 commit hashes (`c90af56`, `c997fed`, `e7ae03a`, `a0ffa5c`, `8e42443`) verified present in `git log --oneline --all`.
