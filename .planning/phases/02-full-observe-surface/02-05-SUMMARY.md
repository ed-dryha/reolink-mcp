---
phase: 02-full-observe-surface
plan: 05
subsystem: api
tags: [reolink-aio, observe-tools, get_device_info, serial, regression-test, human-uat]

# Dependency graph
requires:
  - phase: 02-full-observe-surface
    provides: get_device_info (Plan 02-01), the qa_phase2.py live harness (Plans 02-03/02-04), and 02-04-SUMMARY.md's live evidence for Human-UAT tests #2-4
provides:
  - "_standalone_channel_fallback helper in observe.py — get_device_info's serial/item_number now resolve for standalone (non-NVR) cameras via the same None-key fallback Host.camera_model() applies internally (02-VERIFICATION.md gap #1 closed)"
  - "_per_channel_getter test helper — channel-argument-sensitive per-channel dict mock replacing the blanket Mock(return_value=...) that hid the original bug"
  - "Live-confirmed check_device_info PASS on both real P437 and P320 with real serial values"
  - "02-HUMAN-UAT.md synced: all 7 checklist items pass, status complete, pending 0 (02-VERIFICATION.md gap #2 closed)"
affects: [phase-2-verification, phase-2-closure, phase-3-control-tools]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Standalone-channel None-key fallback for reolink-aio per-channel getters that lack camera_model()'s internal fallback — gate on `not is_nvr`, never borrow an NVR parent's value onto a channel"
    - "Per-channel dict-backed mock (_per_channel_getter) for any Host getter taking a channel argument — same discipline as _per_string_supported, so channel-argument bugs cannot hide behind blanket Mock(return_value=...)"

key-files:
  created: []
  modified:
    - src/reolink_mcp/tools/observe.py
    - tests/tools/test_observe.py
    - .planning/phases/02-full-observe-surface/02-HUMAN-UAT.md

key-decisions:
  - "Fallback implemented as a module-level helper (_standalone_channel_fallback) taking the bound getter, channel, and is_nvr — mirrors Host.camera_model()'s own `not self.is_nvr` precedent exactly rather than inventing new fallback semantics"
  - "_configure_device_info_mock now uses the real standalone-camera shape ({None: value, 0: None}) for serial/item_number so ALL four pre-existing get_device_info tests exercise the fallback, not just the two new dedicated regression tests"
  - "One `# noqa: E501` on the grep-pinned 89-char regression-test name required verbatim by the plan's acceptance criteria (exceeds ruff's 88-col limit when prefixed with `async def `)"

requirements-completed: [OBSV-02, HDWR-01]

# Metrics
duration: ~20min (Task 1 execution ~5min + live checkpoint verification)
completed: 2026-07-09
---

# Phase 2 Plan 5: get_device_info Serial/Item-Number Fallback Fix + Human-UAT Sync Summary

**Fixed get_device_info's serial/item_number fields (None on every real standalone camera due to reolink-aio 0.21.3's missing per-channel fallback) via a camera_model()-mirroring None-key fallback, live-confirmed on real P437/P320 (serials 141484732456113 / 141484733533330), and synced 02-HUMAN-UAT.md to 7/7 passed — closing both remaining 02-VERIFICATION.md gaps.**

## Performance

- **Duration:** ~20 min (code + tests ~5 min; live-hardware checkpoint verification by operator)
- **Started:** 2026-07-09T20:11:00Z (approx.)
- **Completed:** 2026-07-09T20:32:30Z
- **Tasks:** 3 of 3 completed (Task 2 checkpoint approved with live-hardware evidence — see "Task 2 Live-Hardware Verification" below)
- **Files modified:** 3 (2 code/test, 1 planning doc)

## Accomplishments

- Closed 02-VERIFICATION.md gap #1: `get_device_info`'s `serial`/`item_number` were unconditionally `None` on standalone cameras because `reolink-aio` 0.21.3's `Host.serial()`/`Host.item_number()` only ever populate the `None`-keyed cache for a non-NVR host and lack the numeric-channel fallback `Host.camera_model()` applies internally. Added `_standalone_channel_fallback` mirroring that exact precedent (`not is_nvr` gate included), applied to both reads in `get_device_info`'s info dict.
- Replaced the channel-blind `Mock(return_value=...)` mocks that hid the bug with `_per_channel_getter`, a per-channel dict-backed mock mirroring reolink-aio's real standalone shape (`{None: value, 0: None}`) — all four pre-existing `get_device_info` tests now exercise the fallback, plus two new dedicated regression tests (standalone fallback applies; NVR fallback correctly does NOT apply).
- Live-confirmed the fix on real hardware (Task 2 checkpoint, approved): `check_device_info` PASS on both P437 and P320 with real serials, previously guaranteed FAIL.
- Closed 02-VERIFICATION.md gap #2: `02-HUMAN-UAT.md` updated from stale `0/7 pending` to `7/7 passed`, `status: complete`, with per-test evidence notes (Plan 02-04's live run for tests #2-4; this plan's Task 2 evidence for tests #1, #5-7).

## Task Commits

Each task was committed atomically (Task 1 was TDD — two commits):

1. **Task 1 (RED): failing regression tests + per-channel mocks** - `f795cf2` (test)
2. **Task 1 (GREEN): _standalone_channel_fallback fix** - `8e03d61` (feat)
3. **Task 2: checkpoint:human-verify** - no commit (operator-run live verification, no files)
4. **Task 3: 02-HUMAN-UAT.md sync** - no commit (file is gitignored in the worktree; written at `.planning/phases/02-full-observe-surface/02-HUMAN-UAT.md` for the orchestrator to copy to the main checkout, per orchestrator instruction)

**Plan metadata:** committed with this SUMMARY.

_TDD gate compliance: RED commit (`f795cf2`) precedes GREEN commit (`8e03d61`); tests were run and confirmed failing (2 failed) before the fix, and passing (52 passed) after. No refactor commit needed._

## Files Created/Modified

- `src/reolink_mcp/tools/observe.py` - Added `Callable` import and `_standalone_channel_fallback` helper; `get_device_info`'s `serial`/`item_number` now route through it with `host.is_nvr` gating
- `tests/tools/test_observe.py` - Added `_per_channel_getter`; `_configure_device_info_mock` now uses per-channel dict mocks; two new regression tests (`test_get_device_info_serial_and_item_number_fall_back_when_standalone_channel_key_missing`, `test_get_device_info_serial_does_not_fall_back_for_nvr_channel`)
- `.planning/phases/02-full-observe-surface/02-HUMAN-UAT.md` - All 7 tests `result: pass` with evidence notes; Summary `total: 7, passed: 7, pending: 0`; frontmatter `status: complete` (written in worktree, gitignored — orchestrator copies to main checkout)

## Task 2 Live-Hardware Verification (checkpoint approved)

Operator response: "approved". Evidence gathered 2026-07-09 ~20:26 UTC from this worktree's fixed code against real P437 (front_door, 192.168.1.126) and P320 (front_left, 192.168.1.170), with surveillance-security-ai running normally alongside:

| Check | front_door (P437) | front_left (P320) |
|-------|-------------------|-------------------|
| check_device_info | PASS — serial=141484732456113, fw v3.1.0.3633_2406134133, hw IPC_NT2NA48MP, mac ec:71:db:17:42:64 | PASS — serial=141484733533330, fw v3.1.0.3646_2406143592, hw IPC_NT1NA45MP, mac ec:71:db:e2:16:34 |
| check_capabilities | PASS — zoom/ir/white_led/siren=True, ptz_presets=False | PASS — zoom=False, ir=True, white_led=False, siren=True, ptz_presets=False |
| check_states | PASS — day_night='Auto', white_led={'on': False, 'brightness': 85}, ir_lights=True, siren='supported', motion=False | PASS — day_night='Auto', white_led='unsupported', ir_lights=True, siren='supported', motion=False |
| check_recent_events | PASS — person/vehicle/pet='not_detected', motion=False, age_seconds=0.0 | PASS — same shape |

- **Refresh clock (Human-UAT #6):** get_states called 3× per camera via a real MCP stdio client — first call age_seconds=0.0; cached call +3s kept identical polled_at with age_seconds=3.0; refresh=true advanced polled_at and reset age_seconds=0.0, on both cameras (front_door 20:26:53.734595 → cached same → 20:26:56.811444; front_left 20:27:01.041603 → cached same → 20:27:04.166415).
- **MCP client rendering (Human-UAT #5):** all four observe tools called at least once per camera over a real MCP stdio client session; every response well-formed and sensible.
- **Session coexistence (Human-UAT #7):** surveillance-security-ai ran normally alongside the entire run; operator approved with no coexistence issue.
- **Root-cause confirmation:** a direct reolink-aio probe of both live cameras confirmed the fix's premise exactly — `is_nvr=False`, `_serial={None: '<real serial>'}`, `serial(0)=None`, raw GetDevInfo `itemNo=''` (empty string) on both cameras, `exactType='IPC'`. (An earlier "still failing" report was traced to a run from the main checkout, which lacks this fix until merge — not a defect in the fix.)

## Decisions Made

- Implemented the fallback exactly as 02-05-PLAN.md specified (mirroring `Host.camera_model()`'s `not self.is_nvr` precedent) — no novel design.
- Kept the plan's grep-pinned 89-character regression-test name verbatim and suppressed the resulting single E501 with `# noqa: E501` rather than renaming (the name is an acceptance criterion).

## Deviations from Plan

None - plan executed exactly as written. (The `# noqa: E501` is a formatting accommodation for the plan's own required test name, not a behavioral deviation. Task 3's UAT file could not be committed from the worktree because `.planning/` is gitignored except `*-SUMMARY.md`; per orchestrator instruction it was written in the worktree for post-merge copying instead.)

## Issues Encountered

- Live cameras report raw GetDevInfo `itemNo=''` (empty string), so `item_number` remains empty/None-ish live even post-fix — this is camera firmware behavior, not a code defect; `check_device_info` intentionally does not assert on `item_number`, and the mock regression test proves the fallback logic itself. `serial` (the field that gates PASS/FAIL and is promised by OBSV-02) is fully fixed and live-confirmed.

## Known Stubs

None — no placeholder values, hardcoded empty data, or unwired components introduced by this plan.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or trust-boundary changes beyond the plan's threat model (T-05-01 mitigated via the regression tests; T-05-02 accepted as previously).

## User Setup Required

None beyond what Task 2 already exercised — the operator reused the existing Phase 1 config (`~/.config/reolink-mcp/config.yaml` + `RMCP_CAMERAS__<name>__PASSWORD` env vars); no new setup was introduced.

## Next Phase Readiness

- Both 02-VERIFICATION.md gaps closed: gap #1 (serial/item_number None on real hardware) fixed and live-confirmed; gap #2 (stale 02-HUMAN-UAT.md) synced to 7/7 passed, status complete.
- HDWR-01 and Roadmap Phase 2 success criterion 5 fully proven — Phase 2 is ready for closure/verification re-run.
- Pattern established for Phase 3 control tools: any reolink-aio per-channel getter used on standalone cameras should be checked for the missing None-key fallback (`serial`/`item_number` had it; `camera_model` did not need it).

---
*Phase: 02-full-observe-surface*
*Completed: 2026-07-09*

## Self-Check: PASSED

- FOUND: src/reolink_mcp/tools/observe.py (`_standalone_channel_fallback` x3)
- FOUND: tests/tools/test_observe.py (`_per_channel_getter` + both regression tests)
- FOUND: .planning/phases/02-full-observe-surface/02-HUMAN-UAT.md (0 pending, 7/7 pass, status complete)
- FOUND commit: f795cf2 (Task 1 RED)
- FOUND commit: 8e03d61 (Task 1 GREEN)
- FOUND commit: d4c9692 (SUMMARY)
- `uv run pytest tests/ -q` — 93 passed
- `uv run ruff check src/ tests/` — all checks passed
