---
phase: 03
reviewed: 2026-07-11T06:15:54Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - src/reolink_mcp/config.py
  - src/reolink_mcp/errors.py
  - src/reolink_mcp/server.py
  - src/reolink_mcp/manager.py
  - src/reolink_mcp/capabilities.py
  - src/reolink_mcp/tools/control.py
  - src/reolink_mcp/tools/__init__.py
  - scripts/qa_phase3.py
  - tests/conftest.py
  - tests/test_server.py
  - tests/tools/test_control.py
findings_count:
  critical: 1
  warning: 4
  info: 6
  total: 11
status: fixes_applied
fixed: 2026-07-11T10:55:28Z
---

# Phase 03: Code Review Report

**Reviewed:** 2026-07-11T06:15:54Z
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

Phase 03's control-tool surface is structurally sound: capability gating precedes every mutation, the siren cap refuses rather than clamps, read-only mode is proven at the real import boundary with hard SAFE-01/SAFE-02 full-registry tests, hand-built `send_setting()` bodies contain only server-derived values (verified byte-for-byte against reolink-aio 0.21.3's own body construction), and no secrets appear in logs, errors, or the QA harness output.

However, the "every host call wrapped in `classify_control_error`" invariant — the phase's own stated pattern and the enforcement mechanism for threat T-02-01 (raw reolink-aio exception text reaching the client) — has holes. I verified against the installed `mcp` 1.28.1 SDK that any unhandled tool exception is returned **verbatim** to the MCP client (`lowlevel/server.py:589` → `_make_error_result(str(e))`), and against installed `reolink_aio/baichuan/baichuan.py` that Baichuan exceptions embed raw response payloads, header/data hex dumps, and nonce material in their messages. One unwrapped Baichuan network call therefore leaks raw wire data to the client (Critical). Several secondary unwrapped reolink-aio-raising reads, a missing siren duration lower bound, a QA harness that cannot fail on the exact silent-siren failure mode it was created in response to, and two vacuous test assertions round out the findings.

## Critical Issues

### CR-01: Unwrapped Baichuan re-poll in `ptz_move_to_preset` leaks raw exception text to the MCP client

**File:** `src/reolink_mcp/tools/control.py:385-386`
**Issue:** `await host.baichuan.get_ptz_position(ch)` at line 386 sits **outside** the `try/except classify_control_error` block (which ends at line 383). This is a real network call over the Baichuan TCP-9000 channel, and its failure modes are not exotic: timeout waiting on payload, connection loss, login-rate refusal, decode errors. Installed `reolink_aio/baichuan/baichuan.py` raise sites embed sensitive/raw wire content directly in exception messages — e.g. `UnexpectedDataError(f"...could not find nonce in response:\n{mess}")` (line 621), `InvalidParameterError(f"...header: {header.hex()}")` (line 417), `InvalidContentTypeError(f"...data: {data.hex()}")` (line 450). Because FastMCP's tool handler returns `str(e)` verbatim on any unhandled exception (`mcp/server/lowlevel/server.py:589` → `_make_error_result(str(e))`, verified in the installed SDK), this raw text — the project's own module docstring in `errors.py` names this exact threat, T-02-01 — reaches the LLM/client unredacted.

Two secondary consequences: (a) the tool reports failure even though the physical PTZ move already **succeeded** — a confusing lie to the operator; (b) the `handle.preset_positions` cache write at line 390 is skipped, degrading later `ptz_position` `at_preset` matching. Note the sibling tool `ptz_position` (line 417) wraps the identical call correctly, and `ptz_guard`'s `goto` re-poll (line 485) is inside its try block — this one call site is the inconsistency.
**Fix:**
```python
    await asyncio.sleep(PTZ_SETTLE_WAIT_S)
    try:
        await host.baichuan.get_ptz_position(ch)
    except Exception as exc:
        # The move itself already succeeded — surface a curated message
        # (or degrade to pan/tilt=None) rather than the raw Baichuan text.
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
```
(Alternatively, catch and log at DEBUG, returning `pan=None, tilt=None` with a note — the mutation succeeded, so failing the whole call is arguably wrong. Either way the raw exception must not escape.)

## Warnings

### WR-01: `set_siren` validates only the upper duration bound — zero/negative durations reach the firmware as a raw `times` parameter

**File:** `src/reolink_mcp/tools/control.py:102-111`
**Issue:** The D-01/D-02 safety envelope refuses `duration > 60` but accepts `duration=0` and negative values. Verified in installed reolink-aio 0.21.3 (`api.py:5383`): `set_siren` performs no range check and passes the value straight through as `{"alarm_mode": "times", "times": duration}` to `baichuan.AudioAlarmPlay`. What the firmware does with `times: 0` or `times: -5` is undefined and model-dependent — the entire point of the refuse-not-clamp rule was to never hand the firmware an unvetted value for the one destructive tool. The tests cover 60 (boundary) and 61 (refusal) but no lower-bound case exists because no lower bound exists.
**Fix:**
```python
    if resolved_duration < 1 or resolved_duration > SIREN_MAX_DURATION_S:
        raise CameraError(
            f"camera '{camera}' siren duration {resolved_duration}s must be "
            f"between 1 and {SIREN_MAX_DURATION_S}s"
        )
```
Add tests for `duration=0` and `duration=-1` asserting `set_siren.assert_not_awaited()`.

### WR-02: Unwrapped reolink-aio-raising reads in `set_zoom` and `ptz_position` escape the curated-error envelope

**File:** `src/reolink_mcp/tools/control.py:296, 306, 320, 424`
**Issue:** `host.zoom_range(ch)` (line 296) is `return self._zoom_focus_settings[channel]` in the installed library — a bare dict index that raises `KeyError` when the settings were never populated. `host.get_zoom(ch)` (lines 306, 320, and 424 in `ptz_position`) raises `InvalidParameterError`/`NotSupportedError` when `_zoom_focus_settings[ch]` is empty — a condition **distinct** from `supported(ch, "zoom")`, so the `gate()` check at line 287/424 does not preclude it (the library's own `get_zoom` checks both separately, `api.py:4425-4431`). All four call sites are outside any `try/except classify_control_error`, so the raw library message (or a bare `KeyError: 0`) goes to the client verbatim via the same FastMCP `str(e)` path as CR-01. Content here is low-sensitivity (function-name prefixes, camera names, ranges) — hence Warning, not Critical — but it breaks the phase's "every host interaction wrapped" invariant and produces uncurated, prefix-bearing errors the whole `classify_control_error` machinery exists to prevent.
**Fix:** Move the `zoom_range`/`get_zoom` reads inside the existing `try` block (or wrap them in their own `try/except` raising `CameraError(classify_control_error(...))`). In `ptz_position`, wrap the line-424 read the same way, degrading to `"unavailable"` on failure.

### WR-03: QA harness siren check passes on a completely silent siren — the exact failure mode that forced the mid-checkpoint `set_audio_alarm` deviation

**File:** `scripts/qa_phase3.py:488-511`
**Issue:** `check_siren` only flags the over-duration direction: `if perceived > 2 * 2:` prints a WARNING (and even then still returns `True` at line 510-511). An operator who hears **nothing** and enters `0` sails through — `0 > 4` is false, `[PASS]` is printed, exit code 0. Phase 03's own live QA discovered precisely this failure mode (siren command accepted, no sound, `audio_alarm_enabled=false`), yet the harness was not updated to catch it: there is no `audio_alarm_enabled` preflight before the burst and no under-duration/zero check after it. A regression to silence (audio alarm disabled again via the app, firmware update, etc.) would pass this harness forever.
**Fix:** (1) Before the burst, call `get_states` and warn/prompt if `audio_alarm_enabled` is false (offering `set_audio_alarm`). (2) After the burst, treat `perceived < 1` as FAIL (siren inaudible), and make the `> 2x` over-duration case return `False` rather than printing a warning and passing.

### WR-04: Vacuous OR-assertions in host-error-translation tests cannot fail for the prefix-leak regression they read as guarding

**File:** `tests/tools/test_control.py:680, 1048-1050`
**Issue:** `assert "set_zoom" not in str(exc_info.value) or "rejected" in str(exc_info.value)`. If prefix-stripping in `classify_control_error` regressed, the message would be `"camera 'front_door' rejected the request — set_zoom: zoom value 15 not in range 0..30"` — it contains **both** `"rejected"` and `"set_zoom"`, so the second disjunct keeps the assertion green. The assertion can only fail if classification is bypassed entirely; it is inert against the prefix-strip bug its shape implies it checks. Same pattern at lines 1048-1050 for `ptz_guard`. The dedicated prefix-strip test in `tests/test_errors.py` mitigates real coverage loss, but these assertions are misleading dead weight that will survive any future refactor unnoticed.
**Fix:**
```python
    msg = str(exc_info.value)
    assert "rejected" in msg
    assert "set_zoom:" not in msg  # func_name prefix must be stripped
```

## Info

### IN-01: Hardcoded disabled-tool count `10` in the read-only log line, plus 16 copy-pasted registration blocks

**File:** `src/reolink_mcp/tools/__init__.py:92-144`
**Issue:** `logger.warning("read-only mode: %d control tools disabled", 10)` uses a bare literal that had to be manually bumped four times during this phase (4→5→8→9→10 per the SUMMARYs) — the next tool addition will make the log lie silently, since no test pins the number inside the message. The 16 near-identical `mcp.tool(annotations=ToolAnnotations(...))(fn)` blocks are the same maintenance hazard in duplicate form.
**Fix:** Register from a data table, e.g. `_CONTROL_TOOLS: list[tuple[Callable, ToolAnnotations]]`, loop over it, and log `len(_CONTROL_TOOLS)`.

### IN-02: Stale comment in `capabilities.py` calls `set_audio_alarm` "out-of-scope"

**File:** `src/reolink_mcp/capabilities.py:22-24`
**Issue:** The `CAPABILITY_MAP` comment says the raw `"siren"` string "gates the unrelated, out-of-scope set_audio_alarm feature" — but `set_audio_alarm` shipped in this very phase (Plan 03-03 deviation) and gates on exactly that raw string by bypassing `gate()`. A maintainer reading this comment would conclude the tool doesn't exist. Related smell: because the curated key `"siren"` shadows the raw string, `gate()`'s documented raw-string fallback is unreachable for `"siren"`, forcing `set_audio_alarm` into a second gating idiom (`handle.host.supported(...)` direct).
**Fix:** Update the comment to note `set_audio_alarm` now wraps the raw `"siren"` capability via a direct `host.supported()` call (control.py documents why).

### IN-03: `set_zoom` relative step silently no-ops when the camera's raw zoom range is under 5 units

**File:** `src/reolink_mcp/tools/control.py:307-308`
**Issue:** `raw_step = round((zmax - zmin) * 10 / 100)` rounds to 0 for any range < 5, so `step=±1` computes `raw = current`, the tool reports success with an unchanged position, and `qa_phase3.py`'s `check_zoom` would then emit a false FAIL ("same position_pct — expected opposite movement"). Real Reolink ranges (0..33) make this unlikely, but the guard is one line.
**Fix:** `raw_step = max(1, round((zmax - zmin) * ZOOM_RELATIVE_STEP_PCT / 100))`.

### IN-04: `importlib.reload` tests leave `reolink_mcp.server` module state polluted for the rest of the session; conftest stub leaks a temp dir and permanently mutates `os.environ`

**File:** `tests/test_server.py:32,50`; `tests/conftest.py:29-48`
**Issue:** After `test_read_only_true_...` runs, `sys.modules["reolink_mcp.server"]` holds a read-only 6-tool `mcp` built from a since-deleted tmp config; monkeypatch restores env vars but nothing reloads the module back. Any future test (or plugin) that touches `reolink_mcp.server` after these tests will see order-dependent state. Today only these two tests consume the module, so this is latent, not live. Separately, conftest's collection stub `tempfile.mkdtemp` is never cleaned up (one leaked dir per run) and `os.environ["RMCP_CONFIG_FILE"]` is overwritten for the whole session without restoration (deliberate for hermeticity, but worth a comment saying so).
**Fix:** Add a fixture/finalizer that re-reloads `reolink_mcp.server` after each reload-based test (with env pointing back at the collection stub), and register `atexit`/`shutil.rmtree` for the stub dir.

### IN-05: `qa_phase3.py` duplicates `resolve_config_path()` from `config.py`

**File:** `scripts/qa_phase3.py:69-73`
**Issue:** Byte-identical copy of `reolink_mcp.config.resolve_config_path()`. If the server's config-resolution rule changes (e.g. XDG fallback), the harness's `preflight()` will silently validate a different file than the server loads.
**Fix:** `from reolink_mcp.config import resolve_config_path` (the script already runs under `uv run` with the package importable).

### IN-06: `classify_control_error`'s first-colon split is brittle against unprefixed messages

**File:** `src/reolink_mcp/errors.py:109-112`
**Issue:** `detail.split(":", 1)[1]` assumes every `InvalidParameterError`/`NotSupportedError` message carries a `func_name: ` prefix. True for every raise site in pinned reolink-aio 0.21.3 (verified), but a future patch release adding an unprefixed message containing a colon (e.g. `"value out of range: 0..30"`) would have its leading half silently amputated. Low risk under the `~=0.21.3` pin; worth a stricter heuristic when the pin moves.
**Fix:** Only strip when the prefix looks like an identifier: `head, _, tail = detail.partition(": "); detail = tail if head.isidentifier() and tail else detail`.

---

## Fix Pass

**Fixed:** 2026-07-11T10:55:28Z
**Scope:** Critical + Warning (CR-01, WR-01..WR-04). Info findings (IN-01..IN-06) deliberately left unfixed per fix-scope instruction.
**Verification:** full suite 181 passed (9 new tests added), `ruff check .` clean, `py_compile scripts/qa_phase3.py` clean.

### CR-01 — fixed (`f47e9b1`)
`src/reolink_mcp/tools/control.py`: the post-move `host.baichuan.get_ptz_position(ch)` re-poll in `ptz_move_to_preset` is now inside its own `try/except`. Chose the reviewer's second (degrade) option — the physical move has already succeeded at that point, so the tool logs the raw exception at DEBUG (SAFE-03), skips the `preset_positions` cache write, and returns `pan=None`/`tilt=None` with an explanatory `note` instead of failing the call. Raw Baichuan text (wire hex dumps, nonce material) can no longer reach the client (T-02-01). Module gained the standard `logger = logging.getLogger(__name__)`. Regression test added asserting the raw text never appears in the result and the cache write is skipped.

### WR-01 — fixed (`e40d840`)
`src/reolink_mcp/tools/control.py`: `set_siren` now refuses `resolved_duration < 1` before any host call with a curated message ("must be at least 1s"), as a separate check so the existing 61s-cap message (and its tests) stay intact — refuse-not-clamp on both bounds. Parametrized tests for `duration=0` and `duration=-1` assert `host.set_siren.assert_not_awaited()`.

### WR-02 — fixed (`d1a1511`)
`src/reolink_mcp/tools/control.py`: all four unwrapped cache reads now inside the curated envelope — `set_zoom`'s `zoom_range` read and relative-step `get_zoom` read each wrapped in `try/except` raising `CameraError(classify_control_error(...))`; the final read-back joined the existing `set_zoom` try block. `ptz_position`'s zoom read degrades to `"unavailable"` (raw detail at DEBUG) instead of failing a call whose pan/tilt answer is already in hand. Tests added for the bare-`KeyError` range read, the relative-step `InvalidParameterError` (asserting the prefix is stripped), and the `ptz_position` degrade.

### WR-03 — fixed (`2e18aa7`)
`scripts/qa_phase3.py` `check_siren`: (1) preflight `get_states(refresh=True, full=True)` before the burst — if `audio_alarm_enabled` is false, warns and offers to enable via `set_audio_alarm` (declining proceeds with an expected-FAIL notice); (2) perceived duration `< 1` now FAILs (silent siren — the exact live-QA failure mode); (3) the `> 2x` over-duration case now returns `False` instead of warning-and-passing. Module docstring's check-6 description updated to match.

### WR-04 — fixed (`796da3b`)
`tests/tools/test_control.py:680, 1048-1050`: both vacuous OR-assertions replaced with the reviewer's two independent assertions — `"rejected" in msg` AND `"set_zoom:"`/`"set_ptz_guard:" not in msg` — so a func_name-prefix-leak regression now actually fails the tests.

---

_Reviewed: 2026-07-11T06:15:54Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Fixed: 2026-07-11T10:55:28Z_
_Fixer: Claude (gsd-code-fixer)_
