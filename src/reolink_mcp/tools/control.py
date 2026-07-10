"""Control tools (state-mutating): `set_siren` (Phase 3 Plan 1); `set_spotlight`,
`set_ir_lights`, `set_white_led` (Phase 3 Plan 1, Task 2).

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

from typing import Any, Literal

from mcp.server.fastmcp import Context

from reolink_mcp.capabilities import gate, refusal_message
from reolink_mcp.errors import CameraError, classify_control_error

# D-01/D-02: a default duration keeps "sound the siren" from producing an
# indefinite blast, and a hard cap refuses (never clamps) any request over
# it — both validated BEFORE any host call.
SIREN_DEFAULT_DURATION_S = 5
SIREN_MAX_DURATION_S = 60


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
    cap, never silently clamped. `action="stop"` silences an active siren
    immediately. Calling `action="sound"` again while already sounding is
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
