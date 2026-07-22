"""Control tools (state-mutating): `set_siren` (Phase 3 Plan 1); `set_spotlight`,
`set_ir_lights`, `set_white_led` (Phase 3 Plan 1, Task 2); `set_zoom` (Phase 3
Plan 2, Task 1); `list_presets`, `ptz_move_to_preset`, `ptz_position`
(Phase 3 Plan 2, Task 2); `ptz_guard` (Phase 3 Plan 3, Task 1 — the ninth
planned control tool); `set_audio_alarm` (Phase 3 Plan 3, user-directed
checkpoint deviation — live P437 QA found `set_siren` is silently suppressed
by the camera while its audio-alarm feature is disabled, so enabling it must
be reachable from MCP too).

Tool functions here are plain, undecorated `async def`s — registration with
`ToolAnnotations` happens explicitly in `tools/__init__.py`'s
`register_all(mcp, read_only)`, not via an `@mcp.tool` decorator in this
module. This module intentionally never imports `mcp` from `server.py`:
`server.py` constructs `mcp` and then imports `reolink_mcp.tools` to
register tools against it, so importing `mcp` here at module scope would be
circular (same convention as `tools/observe.py`).

Every tool is capability-gated via `capabilities.gate()`/`refusal_message()`
before any host mutation call (CTRL-10) and returns a read-back confirmation
dict — the resulting state, read from the camera after the command, not
just an echo of what was requested (D-14). `set_siren` is the one documented
exception: no live siren-state getter exists in reolink-aio, so it echoes
the accepted command with an explicit note instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from mcp.server.fastmcp import Context

from reolink_mcp.capabilities import gate, refusal_message
from reolink_mcp.errors import CameraError, classify_control_error

logger = logging.getLogger(__name__)

# D-01/D-02: a default duration keeps "sound the siren" from producing an
# indefinite blast, and a hard cap refuses (never clamps) any request over
# it — both validated BEFORE any host call.
SIREN_DEFAULT_DURATION_S = 5
SIREN_MAX_DURATION_S = 60

# D-08/Pattern 3: relative zoom steps are computed as ~10% of the camera's
# raw zoom range per step (read-then-absolute-set, never the continuous
# ZoomInc/ZoomDec PTZ commands) — a reasonable conversational-nudge default,
# adjustable if live P437 QA (Plan 03-03) finds it too coarse/fine.
ZOOM_RELATIVE_STEP_PCT = 10

# D-12/Pattern 4: set_ptz_command's "PtzCtrl" body does not start with "Set",
# so send_setting()'s auto-refetch never fires for a preset move — an
# explicit settle-wait + host.baichuan.get_ptz_position() re-poll is
# required. No PTZ hardware exists yet to calibrate this against (flagged as
# an assumption pending live confirmation, RESEARCH.md Pattern 4); anchored
# to reolink-aio's own wait_before_get=3 convention for lights/zoom.
PTZ_SETTLE_WAIT_S = 2

# D-11/Assumption A5: raw pan/tilt units of "close enough" to a cached
# preset position to report ptz_position's "at_preset" match.
PTZ_POSITION_TOLERANCE = 40

# ptz_move: a continuous PtzCtrl command (Left/Right/Up/... ) keeps the motor
# running until an explicit "Stop" is sent — there is no camera-side auto-
# timeout. A short default duration keeps "pan right" from spinning the head
# indefinitely if the caller never follows up, mirroring set_siren's
# refuse-not-clamp duration discipline (D-01/D-02) rather than the
# relative-zoom's clamp-don't-refuse one, because an unbounded PTZ sweep can
# walk the head into a mechanical limit or off the area of interest for far
# longer than a clamped zoom step ever could.
PTZ_MOVE_DEFAULT_DURATION_S = 1.0
PTZ_MOVE_MAX_DURATION_S = 8.0

_PTZ_MOVE_DIRECTIONS = {
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "leftup": "LeftUp",
    "leftdown": "LeftDown",
    "rightup": "RightUp",
    "rightdown": "RightDown",
}


async def set_siren(
    camera: str,
    ctx: Context,
    action: Literal["sound", "stop"] = "sound",
    duration: int | None = None,
) -> dict[str, Any]:
    """Sound or stop `camera`'s siren (D-01..D-04).

    `action="sound"` with no `duration` produces a short ~5s burst
    (`SIREN_DEFAULT_DURATION_S`); an explicit `duration` over
    `SIREN_MAX_DURATION_S` (60s) is refused with a clear error naming the
    cap, never silently clamped. Zero/negative durations are likewise
    refused before any host call — reolink-aio passes the value straight
    through to the firmware as a raw `times` parameter, and what a camera
    does with `times: 0` or a negative value is undefined and
    model-dependent (refuse-not-clamp, both bounds). `action="stop"`
    silences an active siren immediately.
    Calling `action="sound"` again while already sounding is
    allowed (no refusal) — the camera's own firmware restarts the auto-off
    timer, no server-side code needed for that behavior (D-04).

    `duration=None` is NEVER passed to `host.set_siren` when
    `action="sound"` — that triggers reolink-aio's own indefinite "manual"
    mode, which D-01 forbids. No live siren-state getter exists in
    reolink-aio, so the returned dict echoes the accepted command rather
    than an observed state (D-14's documented exception)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "siren"):
        raise CameraError(refusal_message(camera, "siren"))
    host, ch = handle.host, handle.channel

    note = (
        "no live siren-state getter exists in reolink-aio; this reflects "
        "the accepted command, not an observed state"
    )

    if action == "stop":
        try:
            await host.set_siren(ch, enable=False)
        except Exception as exc:
            raise CameraError(
                classify_control_error(exc, camera, manager.configured_host(camera))
            ) from exc
        return {"camera": camera, "action": "stop", "duration": None, "note": note}

    resolved_duration = duration if duration is not None else SIREN_DEFAULT_DURATION_S
    if resolved_duration < 1:
        raise CameraError(
            f"camera '{camera}' siren duration {resolved_duration}s must be "
            f"at least 1s — zero/negative durations are never sent to the "
            f"firmware"
        )
    if resolved_duration > SIREN_MAX_DURATION_S:
        raise CameraError(
            f"camera '{camera}' siren duration {resolved_duration}s exceeds the "
            f"{SIREN_MAX_DURATION_S}s safety cap — request a duration of "
            f"{SIREN_MAX_DURATION_S}s or less"
        )

    try:
        await host.set_siren(ch, enable=True, duration=resolved_duration)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
    return {
        "camera": camera,
        "action": "sound",
        "duration": resolved_duration,
        "note": note,
    }


async def set_audio_alarm(camera: str, ctx: Context, enabled: bool) -> dict[str, Any]:
    """Enable or disable `camera`'s siren/audio-alarm feature.

    When this feature is disabled on the camera, `set_siren` commands are
    accepted by the firmware but produce NO audible sound (confirmed on a
    live P437 during Phase 3 hardware QA) — so a silent siren should be
    triaged by checking `get_states`'s `audio_alarm_enabled` field and, if
    `false`, calling this tool with `enabled=true` before retrying.

    Gates on the raw `"siren"` capability string directly — NOT the curated
    `"siren"` key, which `CAPABILITY_MAP` deliberately maps to `"siren_play"`
    (the manual-trigger capability `set_siren` wraps; Pitfall 3). The raw
    `"siren"` capability is what reolink-aio's own `set_audio_alarm()` gates
    on, and the same string `get_states`/`get_capabilities(full=True)` read
    for `audio_alarm_enabled`/`siren_schedule`.

    Read-back is fresh (D-14): `SetAudioAlarm`/`SetAudioAlarmV20` goes
    through `send_setting()`, whose `Set*` auto-refetch re-polls the
    corresponding `GetAudioAlarm` state before returning."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not handle.host.supported(handle.channel, "siren"):
        raise CameraError(refusal_message(camera, "audio_alarm"))
    host, ch = handle.host, handle.channel

    try:
        await host.set_audio_alarm(ch, enabled)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
    return {"camera": camera, "audio_alarm_enabled": host.audio_alarm_enabled(ch)}


async def set_spotlight(camera: str, ctx: Context, on: bool) -> dict[str, Any]:
    """Turn `camera`'s spotlight on or off (D-05).

    Gates on the `"white_led"` capability — one physical light, two
    ergonomics (Pattern 2): `set_spotlight` is a full-brightness, always-on-
    schedule convenience wrapper reolink-aio itself implements on top of
    `set_whiteled`/`set_spotlight_lighting_schedule`, so both this tool and
    `set_white_led` gate on and read back the same underlying state."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "white_led"):
        raise CameraError(refusal_message(camera, "white_led"))
    host, ch = handle.host, handle.channel

    try:
        await host.set_spotlight(ch, on)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
    # set_spotlight() internally calls set_whiteled(), which uses
    # send_setting(body, wait_before_get=3) — state is already fresh, no
    # extra poll needed (D-14).
    return {"camera": camera, "spotlight": {"on": host.whiteled_state(ch)}}


async def set_ir_lights(
    camera: str, ctx: Context, mode: Literal["auto", "on", "off"]
) -> dict[str, Any]:
    """Set `camera`'s IR lights to one of the three native modes: `auto`
    (factory default), `on` (always on), `off` (D-06).

    `host.set_ir_lights()`'s own convenience wrapper can only ever send
    `"Auto"`/`"Off"` to the camera (reolink-aio 0.21.3, verified against
    installed source) — reaching the always-on `"On"` state requires
    building the raw `SetIrLights` body directly via `send_setting()`
    (Pitfall 2). The channel value in that body is server-derived from the
    already-gated handle, never user-supplied — no free-form string reaches
    the wire body (T-03-04)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "ir_lights"):
        raise CameraError(refusal_message(camera, "ir_lights"))
    host, ch = handle.host, handle.channel

    try:
        if mode == "on":
            await host.send_setting(
                [
                    {
                        "cmd": "SetIrLights",
                        "action": 0,
                        "param": {"IrLights": {"channel": ch, "state": "On"}},
                    }
                ]
            )
        else:
            await host.set_ir_lights(ch, enable=(mode == "auto"))
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc

    # No public tri-state IR getter exists in reolink-aio — ir_enabled()
    # only distinguishes Auto from not-Auto, collapsing On and Off together
    # (Pitfall 3). host._ir_settings is the only place the raw tri-state
    # wire value ("Auto"/"On"/"Off") is cached, so reading it here is a
    # narrow, explicitly-documented exception to the "never read private
    # attributes" convention.
    raw_state = host._ir_settings.get(ch, {}).get("state")  # noqa: SLF001
    return {
        "camera": camera,
        "ir_lights": {"Auto": "auto", "On": "on", "Off": "off"}.get(
            raw_state, raw_state
        ),
    }


async def set_white_led(
    camera: str, ctx: Context, on: bool, brightness: int | None = None
) -> dict[str, Any]:
    """Turn `camera`'s white LED on/off with optional brightness (0-100)
    (D-07).

    Passing `mode=None` to `host.set_whiteled()` leaves the camera's
    current mode untouched — never derived or guessed here — which is what
    satisfies D-07's "no scheduling/night-auto surface introduced"
    requirement for free. Omitted `brightness` passes through as `None`,
    never a fabricated default."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "white_led"):
        raise CameraError(refusal_message(camera, "white_led"))
    host, ch = handle.host, handle.channel

    try:
        await host.set_whiteled(ch, state=on, brightness=brightness, mode=None)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
    return {
        "camera": camera,
        "white_led": {
            "on": host.whiteled_state(ch),
            "brightness": host.whiteled_brightness(ch),
        },
    }


async def set_zoom(
    camera: str,
    ctx: Context,
    position: int | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    """Zoom `camera` via an absolute normalized position (0-100, 0=widest)
    or a relative in/out step (D-08).

    Exactly one of `position`/`step` must be given. Both modes resolve to
    one deterministic read-then-absolute-`host.set_zoom()` call — never the
    continuous `ZoomInc`/`ZoomDec` PTZ commands (Pattern 3) — so zoom control
    stays on the same bounded, already-validated code path regardless of
    which parameter the caller used. A relative step that would exceed the
    camera's raw range is silently clamped (a low-friction, reversible
    control, unlike the siren's refuse-not-clamp rule); an out-of-range
    absolute `position` is refused before any host call."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "zoom"):
        raise CameraError(refusal_message(camera, "zoom"))
    host, ch = handle.host, handle.channel

    if (position is None) == (step is None):
        raise CameraError(
            f"camera '{camera}' set_zoom requires exactly one of position or step"
        )

    # zoom_range()/get_zoom() are synchronous cache reads, but they RAISE
    # (bare KeyError / InvalidParameterError) when _zoom_focus_settings was
    # never populated — a condition distinct from supported(ch, "zoom"), so
    # the gate() above does not preclude it. Wrapped like every other host
    # interaction so no raw library text escapes to the client (T-02-01).
    try:
        zrange = host.zoom_range(ch)["zoom"]
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
    zmin, zmax = zrange["min"], zrange["max"]

    if position is not None:
        if position < 0 or position > 100:
            raise CameraError(
                f"camera '{camera}' zoom position {position} not in range 0..100"
            )
        raw = round(zmin + (zmax - zmin) * position / 100)
    else:
        try:
            current = host.get_zoom(ch)
        except Exception as exc:
            raise CameraError(
                classify_control_error(exc, camera, manager.configured_host(camera))
            ) from exc
        raw_step = round((zmax - zmin) * ZOOM_RELATIVE_STEP_PCT / 100)
        raw = min(max(current + step * raw_step, zmin), zmax)

    try:
        await host.set_zoom(ch, raw)
        # host.set_zoom() itself calls send_setting(body, getcmd="GetZoomFocus",
        # wait_before_get=3) — the state is already fresh, no extra poll needed
        # (D-14, same discipline as the lights read-backs above).
        final_raw = host.get_zoom(ch)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc
    return {
        "camera": camera,
        "zoom": {
            "raw": final_raw,
            "position_pct": round((final_raw - zmin) / (zmax - zmin) * 100)
            if zmax > zmin
            else 0,
            "range": {"min": zmin, "max": zmax},
        },
    }


async def list_presets(camera: str, ctx: Context) -> dict[str, Any]:
    """List `camera`'s named PTZ presets (CTRL-06).

    `host.ptz_presets(ch)` is already populated at connect time (Pattern 1 —
    `get_host_data()`'s second internal round-trip includes `GetPtzPreset`
    whenever `ptz_preset_basic` is supported) — a pure, synchronous read,
    zero extra host I/O beyond the manager's own connect."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "ptz_presets"):
        raise CameraError(refusal_message(camera, "ptz_presets"))
    host, ch = handle.host, handle.channel

    return {"camera": camera, "presets": host.ptz_presets(ch)}


async def ptz_move_to_preset(
    camera: str, ctx: Context, preset: str | int
) -> dict[str, Any]:
    """Move `camera` to a named PTZ preset, or a numeric ID for unnamed ones
    (D-09).

    An unknown preset name is refused BEFORE any host call, with a curated
    error listing the available preset names (self-correcting error style,
    mirroring the unknown-camera error). `set_ptz_command`'s `"PtzCtrl"` body
    does not auto-refresh position (Pattern 4), so this tool explicitly
    waits `PTZ_SETTLE_WAIT_S` for the camera to settle, then force-repolls
    pan/tilt via `host.baichuan.get_ptz_position()` (Pattern 5) and writes
    the observed position into the session-scoped `preset_positions` cache
    (D-11's later `ptz_position` lookup, Pitfall 6). A failed re-poll never
    fails the call — the physical move already succeeded — it degrades to
    `pan`/`tilt` of `None` with an explanatory note, and the raw Baichuan
    exception text (which embeds wire hex dumps and nonce material) stays at
    DEBUG on stderr, never reaching the client (T-02-01, SAFE-03)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "ptz_presets"):
        raise CameraError(refusal_message(camera, "ptz_presets"))
    host, ch = handle.host, handle.channel

    presets = host.ptz_presets(ch)
    if isinstance(preset, str) and preset not in presets:
        names = ", ".join(sorted(presets))
        raise CameraError(
            f"camera '{camera}' has no preset '{preset}' — available "
            f"presets: {names}"
        )
    preset_id = presets[preset] if isinstance(preset, str) else preset

    try:
        await host.set_ptz_command(ch, preset=preset_id)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc

    resolved_name = (
        preset
        if isinstance(preset, str)
        else next((n for n, i in presets.items() if i == preset_id), None)
    )

    await asyncio.sleep(PTZ_SETTLE_WAIT_S)
    try:
        await host.baichuan.get_ptz_position(ch)
    except Exception as exc:
        # The physical move already succeeded — failing the whole call here
        # would lie to the operator, and Baichuan exception messages embed
        # raw wire content (header/data hex dumps, nonce material) that must
        # never escape to the client (T-02-01). Degrade instead: raw detail
        # stays at DEBUG on stderr (SAFE-03), the preset_positions cache
        # write is skipped, and the client gets an honest note.
        logger.debug(
            "camera '%s' post-move position re-poll failed: %r", camera, exc
        )
        return {
            "camera": camera,
            "preset": resolved_name,
            "pan": None,
            "tilt": None,
            "note": (
                "preset move succeeded, but the post-move position re-poll "
                "failed — pan/tilt unavailable for this call"
            ),
        }

    pan, tilt = host.ptz_pan_position(ch), host.ptz_tilt_position(ch)
    if pan is not None and tilt is not None:
        handle.preset_positions[preset_id] = (pan, tilt)

    return {"camera": camera, "preset": resolved_name, "pan": pan, "tilt": tilt}


async def ptz_move(
    camera: str,
    ctx: Context,
    direction: Literal[
        "left",
        "right",
        "up",
        "down",
        "leftup",
        "leftdown",
        "rightup",
        "rightdown",
        "stop",
    ],
    duration: float | None = None,
    speed: int | None = None,
) -> dict[str, Any]:
    """Pan/tilt `camera` freehand in one of 8 compass directions, or `stop` an
    already-moving camera immediately.

    Unlike `ptz_move_to_preset` (a fixed, named target), this is a raw
    continuous move — the same PtzCtrl command a joystick app sends while a
    button is held down. `direction="stop"` sends the camera's `Stop` command
    on its own and returns immediately; every other direction sends the move
    command, holds it for `duration` seconds (`PTZ_MOVE_DEFAULT_DURATION_S`
    if omitted, refused above `PTZ_MOVE_MAX_DURATION_S` — never silently
    clamped, same discipline as `set_siren`'s duration bound), then always
    sends `Stop` before returning — including when the wait itself raises
    (e.g. the call is cancelled mid-move). A continuous PTZ command has no
    camera-side timeout of its own, so skipping the stop on an error path
    would leave the head panning unattended.

    `speed`, if given, is passed straight to `host.set_ptz_command()`
    unvalidated here — reolink-aio itself refuses a non-integer speed or one
    sent to a camera that doesn't support variable PTZ speed
    (`NotSupportedError`), which `classify_control_error()` already turns
    into a curated message; no need to duplicate that check.

    Same settle-wait + forced `host.baichuan.get_ptz_position()` re-poll as
    `ptz_move_to_preset` (Pattern 4/5) — `PtzCtrl` never auto-refreshes
    position. If the trailing `Stop` itself fails, the physical move may
    still be ongoing: that failure is never swallowed silently, it is
    surfaced as an explicit `note` on the returned dict so the caller knows
    to check the camera by hand, while the raw exception stays at DEBUG on
    stderr (T-02-01, SAFE-03)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "pan_tilt"):
        raise CameraError(refusal_message(camera, "pan_tilt"))
    host, ch = handle.host, handle.channel

    if direction == "stop":
        try:
            await host.set_ptz_command(ch, command="Stop")
        except Exception as exc:
            raise CameraError(
                classify_control_error(exc, camera, manager.configured_host(camera))
            ) from exc
    else:
        resolved_duration = (
            duration if duration is not None else PTZ_MOVE_DEFAULT_DURATION_S
        )
        if resolved_duration <= 0:
            raise CameraError(
                f"camera '{camera}' ptz_move duration {resolved_duration}s must be "
                f"greater than 0"
            )
        if resolved_duration > PTZ_MOVE_MAX_DURATION_S:
            raise CameraError(
                f"camera '{camera}' ptz_move duration {resolved_duration}s exceeds "
                f"the {PTZ_MOVE_MAX_DURATION_S}s safety cap — issue multiple shorter "
                f"moves instead"
            )

        try:
            await host.set_ptz_command(
                ch, command=_PTZ_MOVE_DIRECTIONS[direction], speed=speed
            )
        except Exception as exc:
            raise CameraError(
                classify_control_error(exc, camera, manager.configured_host(camera))
            ) from exc

        stop_error: Exception | None = None
        try:
            await asyncio.sleep(resolved_duration)
        finally:
            try:
                await host.set_ptz_command(ch, command="Stop")
            except Exception as exc:
                stop_error = exc
                logger.debug(
                    "camera '%s' ptz_move stop-after-move failed: %r", camera, exc
                )

    await asyncio.sleep(PTZ_SETTLE_WAIT_S)
    try:
        await host.baichuan.get_ptz_position(ch)
    except Exception as exc:
        logger.debug(
            "camera '%s' post-move position re-poll failed: %r", camera, exc
        )
        result: dict[str, Any] = {
            "camera": camera,
            "direction": direction,
            "pan": None,
            "tilt": None,
            "note": (
                "move succeeded, but the post-move position re-poll failed — "
                "pan/tilt unavailable for this call"
            ),
        }
        if direction != "stop" and stop_error is not None:
            result["note"] += (
                "; the trailing Stop command also failed — verify the camera "
                "is not still moving"
            )
        return result

    pan, tilt = host.ptz_pan_position(ch), host.ptz_tilt_position(ch)
    result = {"camera": camera, "direction": direction, "pan": pan, "tilt": tilt}
    if direction != "stop" and stop_error is not None:
        result["note"] = (
            "the trailing Stop command failed — verify the camera is not "
            "still moving"
        )
    return result


async def ptz_position(camera: str, ctx: Context) -> dict[str, Any]:
    """Read `camera`'s current pan/tilt/zoom position (D-11).

    Pan/tilt is only reliably available via a forced re-poll —
    `host.baichuan.get_ptz_position()` (Pattern 5) — never the HTTP-side
    `GetPtzCurPos` path, whose capability gate depends on the Baichuan
    subsystem's own discovery having already run. When the current position
    is within `PTZ_POSITION_TOLERANCE` raw units of a previously-visited,
    cached preset position, `"at_preset"` names that preset — raw numbers
    alone are not meaningful in chat (D-11)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "pan_tilt"):
        raise CameraError(refusal_message(camera, "pan_tilt"))
    host, ch = handle.host, handle.channel

    try:
        await host.baichuan.get_ptz_position(ch)
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc

    pan, tilt = host.ptz_pan_position(ch), host.ptz_tilt_position(ch)

    # get_zoom() raises when _zoom_focus_settings was never populated — a
    # condition distinct from supported(ch, "zoom"), so the gate() alone does
    # not preclude it. Zoom is a best-effort extra on this read-only tool:
    # degrade to "unavailable" (raw detail at DEBUG, SAFE-03) rather than
    # letting raw library text escape or failing a call whose pan/tilt
    # answer is already in hand (T-02-01).
    zoom_val: int | str = "unsupported"
    if gate(handle, "zoom"):
        try:
            zoom_val = host.get_zoom(ch)
        except Exception as exc:
            logger.debug("camera '%s' zoom read failed: %r", camera, exc)
            zoom_val = "unavailable"

    at_preset = None
    for preset_id, (cached_pan, cached_tilt) in handle.preset_positions.items():
        if (
            pan is not None
            and tilt is not None
            and abs(pan - cached_pan) <= PTZ_POSITION_TOLERANCE
            and abs(tilt - cached_tilt) <= PTZ_POSITION_TOLERANCE
        ):
            at_preset = next(
                (n for n, i in host.ptz_presets(ch).items() if i == preset_id), None
            )
            break

    return {
        "camera": camera,
        "pan": pan,
        "tilt": tilt,
        "zoom": zoom_val,
        "at_preset": at_preset,
    }


async def ptz_guard(
    camera: str, ctx: Context, action: Literal["set", "goto", "enable", "disable"]
) -> dict[str, Any]:
    """Configure `camera`'s PTZ guard position/auto-return via one `action`
    parameter — `set`/`goto`/`enable`/`disable` (CTRL-09, D-10).

    `action="set"` saves the CURRENT physical position as the guard point
    (`command="setPos"`); `action="goto"` moves the camera to the saved
    guard point (`command="toPos"`), then waits `PTZ_SETTLE_WAIT_S` and
    force-repolls pan/tilt via `host.baichuan.get_ptz_position()` — the same
    settle-wait + re-poll pattern `ptz_move_to_preset` uses, since
    `SetPtzGuard`'s `"toPos"` variant does not refresh position either
    (Pattern 4).

    `action` in (`enable`, `disable`) deliberately BYPASSES
    `host.set_ptz_guard()`'s own wrapper via a hand-built `send_setting()`
    body containing only `channel`/`benable` — the wrapper's `command=None`
    default always forces the position-resave command string internally
    (see 03-RESEARCH.md Pitfall 7 for the exact literal), which re-saves the
    CURRENT physical position as the guard point any time enable/disable is
    called. Omitting that command-string field and the position-resave flag
    entirely means toggling auto-return never touches the saved guard
    position. `channel` is server-derived from the already-gated handle and
    `benable` is a fixed 1/0 from the `Literal["enable", "disable"]`
    argument — no free-form value reaches the wire body (T-03-15)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    if not gate(handle, "ptz_guard"):
        raise CameraError(refusal_message(camera, "ptz_guard"))
    host, ch = handle.host, handle.channel

    try:
        if action == "set":
            await host.set_ptz_guard(ch, command="setPos")
        elif action == "goto":
            await host.set_ptz_guard(ch, command="toPos")
            await asyncio.sleep(PTZ_SETTLE_WAIT_S)
            await host.baichuan.get_ptz_position(ch)
        else:
            await host.send_setting(
                [
                    {
                        "cmd": "SetPtzGuard",
                        "action": 0,
                        "param": {
                            "PtzGuard": {
                                "channel": ch,
                                "benable": 1 if action == "enable" else 0,
                            }
                        },
                    }
                ]
            )
    except Exception as exc:
        raise CameraError(
            classify_control_error(exc, camera, manager.configured_host(camera))
        ) from exc

    return {
        "camera": camera,
        "ptz_guard": {
            "enabled": host.ptz_guard_enabled(ch),
            "return_time_s": host.ptz_guard_time(ch),
        },
    }
