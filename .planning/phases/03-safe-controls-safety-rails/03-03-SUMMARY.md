---
phase: 03-safe-controls-safety-rails
plan: 03
subsystem: control-tools
tags: [mcp, fastmcp, reolink-aio, ptz, audio-alarm, live-qa, capability-gating]

# Dependency graph
requires:
  - phase: 03-safe-controls-safety-rails
    provides: "Plan 03-01/03-02's tools/control.py foundation (gate()/classify_control_error()/registration pattern), Settings.read_only, the D-13 annotation matrix, and eight of nine control tools (set_siren, set_spotlight, set_ir_lights, set_white_led, set_zoom, list_presets, ptz_move_to_preset, ptz_position)"
provides:
  - "ptz_guard (CTRL-09) — the ninth planned control tool, with a send_setting() enable/disable bypass that avoids set_ptz_guard()'s forced cmdStr=\"setPos\" position resave (Pitfall 7)"
  - "set_audio_alarm — a tenth control tool, added mid-checkpoint at the operator's direction after live QA found set_siren silently inaudible while the camera's audio-alarm feature is disabled"
  - "A full-registry SAFE-01 annotation-completeness test and SAFE-02 exact-tool-count regression test, proven against the FINAL 16-tool (6 observe + 10 control) registry"
  - "scripts/qa_phase3.py — unattended structural QA for spotlight/ir_lights/white_led/zoom/ptz plus an interactive, stopwatch-timed siren check (D-16)"
  - "HDWR-02 closed live: siren/spotlight/zoom/IR/white LED validated against real P437 (front_door) and P320 (front_left) hardware, plus a live MCP-client sanity pass over every control-tool family including the new set_audio_alarm"
affects: [phase-4-and-beyond, any-future-plan-touching-tools-registry-or-siren-audio-path]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ptz_guard's enable/disable actions bypass set_ptz_guard()'s own wrapper via a hand-built host.send_setting() body containing only channel and benable — never cmdStr/bSaveCurrentPos — to avoid re-saving the current physical position as the guard point"
    - "set_audio_alarm gates on the raw \"siren\" capability string (the schedule/audio-alarm-enable capability reolink-aio's own set_audio_alarm() checks), deliberately distinct from CAPABILITY_MAP's curated \"siren\" -> \"siren_play\" key that set_siren gates on (Pitfall 3) — same word, two different underlying capability flags"
    - "Full-registry regression tests (SAFE-01/SAFE-02) assert against the LITERAL final tool-name set and count, not a derived/computed count — every new tool addition (planned or mid-checkpoint deviation) must touch these two tests by construction, making the safety rails self-enforcing"

key-files:
  created:
    - scripts/qa_phase3.py
  modified:
    - src/reolink_mcp/tools/control.py
    - src/reolink_mcp/tools/__init__.py
    - tests/tools/test_control.py
    - tests/test_server.py

key-decisions:
  - "D-10: ptz_guard is one tool with an action parameter (set/goto/enable/disable), keeping the tool count low per REQUIREMENTS.md's anti-bloat principle"
  - "D-13: the complete annotation matrix (readOnlyHint/destructiveHint/idempotentHint) is proven correct across all 16 registered tools via a hard completeness test, not spot-checks; destructiveHint=True remains exclusive to set_siren"
  - "D-14: ptz_guard and set_audio_alarm both return a fresh read-back confirmation for every call (enabled/return_time_s for ptz_guard; audio_alarm_enabled for set_audio_alarm), reusing send_setting()'s Set*-prefix auto-refetch"
  - "D-15: read-only mode's single logger.warning line is verified to log the final, correct disabled-tool count (10, not 9) after the mid-checkpoint set_audio_alarm addition"
  - "D-16: the audible siren test used the shortest burst (~2s), required an explicit interactive confirmation immediately before it, ran last (after every unattended check), and measured its real-world duration against the requested value rather than leaving Assumption A1 as a documented guess"
  - "User-directed mid-checkpoint deviation: set_audio_alarm added as a tenth control tool (not in the original plan) after live QA on the real P437 found set_siren commands accepted but silent because the camera's audio-alarm feature was disabled at the firmware level — see Deviations section"

patterns-established:
  - "Live QA checkpoints on this project can surface genuine tool-surface gaps (not just bugs) that get folded into the same plan as a scoped, user-approved deviation rather than deferred to a future phase — SAFE-01/SAFE-02's hard regression-test shape made the registry change safe to make same-day"

requirements-completed: [CTRL-09, SAFE-01, SAFE-02, HDWR-02]

# Metrics
duration: ~2h (including operator-driven live hardware QA and mid-checkpoint triage)
completed: 2026-07-11
---

# Phase 3 Plan 3: PTZ Guard, Full-Registry Safety Rails, and Live Hardware QA Summary

**ptz_guard closes the ninth planned control tool; SAFE-01/SAFE-02 proven as hard regression tests over the complete, final 16-tool registry (after a user-directed tenth tool, set_audio_alarm, was added mid-checkpoint to fix a live-discovered silent-siren issue); HDWR-02 closed with real P437/P320 hardware validation across all six control-tool families.**

## Performance

- **Duration:** ~2h (includes operator-driven `scripts/qa_phase3.py` run against real hardware, live MCP-client verification, and same-day triage/fix of a hardware-discovered gap)
- **Completed:** 2026-07-11
- **Tasks:** 3 planned (Task 1 code, Task 2 harness, Task 3 checkpoint) + 1 mid-checkpoint deviation task
- **Files modified:** 5 (1 created: `scripts/qa_phase3.py`; 4 modified across the ptz_guard task and the set_audio_alarm deviation)

## Accomplishments

- `ptz_guard(camera, ctx, action)` implements all four actions (`set`/`goto`/`enable`/`disable`); the `enable`/`disable` actions deliberately bypass `set_ptz_guard()`'s own wrapper via a hand-built `send_setting()` body (`{"cmd": "SetPtzGuard", "action": 0, "param": {"PtzGuard": {"channel": ch, "benable": 0|1}}}`) that omits `cmdStr`/`bSaveCurrentPos` entirely, avoiding the position-resave footgun documented as Pitfall 7 in 03-RESEARCH.md
- SAFE-01 (annotation completeness) and SAFE-02 (exact read-only tool count) are now hard regression tests iterating the **complete** registry — every one of the 16 registered tools carries an explicit, non-default `ToolAnnotations` (`readOnlyHint`/`destructiveHint`/`idempotentHint` all set), and `register_all(mcp, read_only=True)` registers exactly the 6 observe-tool names while `read_only=False` registers exactly the full 16-name set
- `scripts/qa_phase3.py` extends `qa_phase2.py`'s black-box-over-stdio harness pattern with six checks (`check_spotlight`, `check_ir_lights`, `check_white_led`, `check_zoom`, `check_ptz`, `check_siren`) — every mutating call issued via `session.call_tool(...)`, never an in-process function call; the siren check is interactive, stopwatch-timed, gated behind an explicit human confirmation, and unconditionally stops the siren afterward
- **HDWR-02 closed live** against real hardware: the operator ran the harness against the real P437 (`front_door`) and P320 (`front_left`) — every checkable category (spotlight, IR lights, white LED, zoom, siren) passed, with PTZ correctly `[SKIP]`ped as expected (no PTZ-capable camera configured). The siren's real-world duration (D-16/Pitfall 1/Assumption A1) was measured and found within the harness's 2x tolerance, resolving the `duration` units ambiguity against reality
- **Mid-checkpoint deviation, user-directed:** live QA surfaced that `set_siren` was accepted by the P437 firmware but produced no audible sound because the camera's audio-alarm feature was disabled (confirmed via `get_states`'s `audio_alarm_enabled=false`; device volume was not the cause). The operator directed adding a tenth control tool, `set_audio_alarm(camera, enabled)`, gating on the raw `"siren"` capability (not the curated `siren`→`siren_play` key `set_siren` uses, per Pitfall 3) with a fresh D-14 read-back. After enabling it via this new tool, the P437 siren became audible and confirmed by the harness's timed siren check
- Full test suite: 175 tests passing, `ruff check src/ tests/ scripts/` clean, zero real network calls in the automated suite

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): failing tests for ptz_guard + full-registry SAFE-01/SAFE-02** - `f020f72` (test)
2. **Task 1 (GREEN): implement ptz_guard (CTRL-09), close SAFE-01/SAFE-02 over full registry** - `ebe33dd` (feat)
3. **Task 2: scripts/qa_phase3.py QA harness for deterrence/zoom/PTZ** - `76b20fb` (feat)
4. **Deviation (user-directed, mid-checkpoint): set_audio_alarm tool** - `19898c4` (feat)
5. **Task 3: Live hardware validation (HDWR-02)** - checkpoint, no code; resolved PASS by the operator (see below)

**Plan metadata:** (this commit)

_Task 1 was `tdd="true"` in the plan and followed the full RED→GREEN cycle as two separate commits, per the plan's own instruction — unlike Plan 03-01/03-02's bundled-commit convention._

## Files Created/Modified

- `src/reolink_mcp/tools/control.py` - `ptz_guard` (all four actions); `set_audio_alarm` (mid-checkpoint deviation)
- `src/reolink_mcp/tools/__init__.py` - registers `ptz_guard` (15th tool) then `set_audio_alarm` (16th tool); read-only-disabled count literal updated `8` → `9` → `10`
- `tests/tools/test_control.py` - `ptz_guard` unit tests (incl. the Pitfall 7 `cmdStr`/`bSaveCurrentPos`-absence regression guard), the full-registry SAFE-01 completeness sweep, the SAFE-02 exact 15-name/16-name-vs-6-name set assertions (updated twice: once for `ptz_guard`, once for `set_audio_alarm`)
- `tests/test_server.py` - real-import tool-count assertions updated to track the final registered count
- `scripts/qa_phase3.py` - new: `check_spotlight`, `check_ir_lights`, `check_white_led`, `check_zoom`, `check_ptz`, `check_siren` — all driving the real server exclusively through `session.call_tool(...)`

## Decisions Made

- Followed the plan's task split exactly for Tasks 1-2 (TDD RED/GREEN for `ptz_guard` + safety-rail tests, then the harness). Task 3 was the live checkpoint; the operator's mid-checkpoint direction to add `set_audio_alarm` was implemented as an explicit, scoped deviation rather than silently folded into `ptz_guard`'s commit or deferred to a future phase — the registry's hard SAFE-01/SAFE-02 tests meant the new tool could not go live without both regression tests being updated in the same commit, keeping the safety rails intact through the change.
- `set_audio_alarm` deliberately gates on the raw `"siren"` capability string rather than `CAPABILITY_MAP`'s curated `"siren"` key (which maps to `"siren_play"`, the manual-trigger capability `set_siren` uses) — same English word, two distinct underlying reolink-aio capability flags, documented inline per Pitfall 3 to prevent future confusion.

## Deviations from Plan

### Auto-fixed Issues

None — Task 1 and Task 2 executed exactly as planned with no Rule 1/2/3 auto-fixes required.

### User-Directed Deviation (mid-checkpoint)

**1. [Rule 4-equivalent, explicit user direction] Added `set_audio_alarm` as a tenth control tool**

- **Found during:** Task 3 (live hardware validation checkpoint), operator-driven QA against the real P437
- **Issue:** `set_siren` commands were accepted by the camera (no error, HTTP 200) but produced no audible sound, even when triggered from the official Reolink app. Root cause: the camera's audio-alarm feature was disabled at the firmware level (`get_states`'s `audio_alarm_enabled=false`). No existing tool in the registry could enable it — the only path was the vendor app's UI, undermining the project's "no other app needed" value proposition for this control path. Device volume (92) was ruled out as the cause first.
- **Fix:** The operator directed adding `set_audio_alarm(camera, ctx, enabled)`, a new control tool that calls `host.set_audio_alarm(ch, enabled)`, gated on the raw `"siren"` capability (distinct from the curated `siren`→`siren_play` key), with a fresh D-14 read-back (`audio_alarm_enabled`) via `send_setting()`'s `Set*`-prefix auto-refetch. Registry grew from 15 to 16 registered tools (9 → 10 control tools). SAFE-01's completeness sweep and SAFE-02's exact-name-set/count assertions in `tests/tools/test_control.py`, and the real-import tool-count assertion in `tests/test_server.py`, were all updated in the same commit to keep the two full-registry safety-rail tests accurate against the shipped code — no partial-registry blind spot introduced.
- **Files modified:** `src/reolink_mcp/tools/control.py`, `src/reolink_mcp/tools/__init__.py`, `tests/test_server.py`, `tests/tools/test_control.py`
- **Verification:** After enabling the audio alarm via the new tool, the P437 siren became audible; the harness's `check_siren` step subsequently measured a ~3.0s perceived duration for a 2s requested burst (within the harness's 2x tolerance). Full suite (175 tests) and `ruff check` both clean after the change.
- **Committed in:** `19898c4`

---

**Total deviations:** 1 user-directed tool addition (0 auto-fixed bugs/missing-functionality/blocking issues in the planned tasks themselves).
**Impact on plan:** Necessary to close a genuine live-hardware-discovered gap in the control surface — without it, the siren tool set would ship with a documented but unfixable "sometimes silently does nothing" failure mode reachable only by leaving the vendor app as a required companion tool, contradicting the project's core value proposition. No scope creep beyond what live QA required; both hard safety-rail regression tests (SAFE-01, SAFE-02) were updated in the same commit, so the registry never had an unproven window.

## Issues Encountered

None beyond the silent-siren finding documented above as the mid-checkpoint deviation.

## User Setup Required

None - no external service configuration required beyond the existing Phase 1/2 camera config, reused unchanged for this plan's live QA.

## Live Hardware QA Results (HDWR-02, Task 3 checkpoint — RESOLVED PASS)

**Harness run** (`uv run python scripts/qa_phase3.py`, operator-driven, real P437 `front_door` + P320 `front_left`) — **all six categories PASS:**

| Check | Result |
|-------|--------|
| spotlight | PASS `front_door` on→off round-trip; SKIP `front_left` (no `white_led` capability) |
| ir_lights | PASS both cameras auto→on→auto round-trips |
| white_led | PASS `front_door` on(brightness=50)→off; SKIP `front_left` |
| zoom | PASS `front_door` nudge in→out (raw `{'in': 11, 'out': 0}`); SKIP `front_left` |
| ptz | SKIP — no PTZ-capable camera configured (expected; mock-validated only, per project's acknowledged PTZ-hardware gap) |
| siren (interactive, D-16) | PASS `front_door` — requested 2s, operator reported ~3.0s perceived duration, API round-trip 2.61s. Within the harness's 2x tolerance; Pitfall 1/Assumption A1 launch-blocker was **not** triggered |

**Additional live MCP-client verification** (operator + orchestrator, real MCP session): `set_siren` (P320 audible 5s burst; P437 per the silent-siren finding below), `set_ir_lights` all three modes on P437, `set_spotlight` on/off P437, `set_white_led` on(60)/off P437 with matching read-backs, `set_zoom` absolute 50%→0% P437, `set_audio_alarm` enable on P437 with a fresh read-back.

**Silent-siren finding and resolution:** see the Deviations section above — `set_audio_alarm` was added mid-checkpoint and confirmed to resolve the issue.

**All 5 acceptance criteria from the plan's Task 3 confirmed:**
1. `scripts/qa_phase3.py`'s spotlight/IR/white LED/zoom checks all printed PASS on the applicable real camera(s) — confirmed
2. The PTZ check printed a clear `[SKIP]`, never a false failure — confirmed
3. The siren's reported perceived duration (~3.0s vs. 2s requested) was recorded and compared, no launch-blocking mismatch found — confirmed
4. At least one live MCP-client call per control-tool family rendered a sensible read-back response — confirmed (see list above)
5. `surveillance-security-ai` was unaffected throughout — confirmed by the operator

**Also merged during this phase** (wave 1 post-merge fix, commit `d6756ae`, belongs to 03-01 context but noted here for completeness): `tests/conftest.py`'s collection-time stub was extended to pre-import `reolink_mcp.server` with `cwd` pinned to the stub directory, so the gitignored repo-root `.env` (containing real camera passwords on the dev machine) can never abort pytest collection.

## Next Phase Readiness

- All ten control tools (`set_siren`, `set_audio_alarm`, `set_spotlight`, `set_ir_lights`, `set_white_led`, `set_zoom`, `list_presets`, `ptz_move_to_preset`, `ptz_position`, `ptz_guard`) are complete, capability-gated, correctly annotated, and read-only-mode-aware; the full 16-tool registry (6 observe + 10 control) is proven correct by hard SAFE-01/SAFE-02 regression tests
- HDWR-02 and Phase 3 ROADMAP success criterion 1 are proven against real hardware — siren/spotlight/zoom/white LED on the P437, IR lights on both P437 and P320, PTZ correctly documented as a hardware gap (mock-validated only)
- The `duration` units ambiguity (Pitfall 1/Assumption A1) is resolved: a 2s requested siren burst produces a real-world perceived duration in the same order of magnitude (not `times * 5`-style multiplication), no follow-up correction needed
- No regression to Phase 1's HDWR-03 coexistence guarantee — `surveillance-security-ai` remained healthy throughout live QA
- Phase 3's control surface is complete and hardware-validated; ready for Phase 4 planning

---
*Phase: 03-safe-controls-safety-rails*
*Completed: 2026-07-11*

## Self-Check: PASSED

All referenced files verified present on disk (`scripts/qa_phase3.py`, `src/reolink_mcp/tools/control.py`, `src/reolink_mcp/tools/__init__.py`, `tests/tools/test_control.py`, `tests/test_server.py`, this SUMMARY.md). All four task/deviation commits (`f020f72`, `ebe33dd`, `76b20fb`, `19898c4`) verified present in `git log --oneline --all`. Full test suite (175 tests) and `uv run ruff check src/ tests/ scripts/` re-verified passing immediately before writing this SUMMARY.
