"""Observe tools (read-only): `list_cameras` and `get_snapshot` (Phase 1);
`get_device_info` and `get_capabilities` (Phase 2 Plan 1); `get_states` and
`get_recent_events` (Phase 2 Plan 2).

Tool functions here are plain, undecorated `async def`s — registration with
`ToolAnnotations` happens explicitly in `tools/__init__.py`'s
`register_all(mcp)`, not via an `@mcp.tool` decorator in this module. This
module intentionally never imports `mcp` from `server.py`: `server.py`
constructs `mcp` and then imports `reolink_mcp.tools` to register tools
against it, so importing `mcp` here at module scope would be circular.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import Context, Image
from PIL import Image as PILImage
from reolink_aio.api import (
    PERSON_DETECTION_TYPE,
    PET_DETECTION_TYPE,
    VEHICLE_DETECTION_TYPE,
)
from reolink_aio.exceptions import CredentialsInvalidError, LoginError, ReolinkError

from reolink_mcp.capabilities import CAPABILITY_MAP, gate
from reolink_mcp.errors import CameraError, classify_reolink_error

# Baseline: always checked and tri-stated, even if this camera lacks them —
# this is what makes "unsupported" observable (D-06/D-09). The set is
# deliberately not the ceiling of what get_recent_events can report — see
# the dynamic-extension loop in get_recent_events itself (D-07).
BASELINE_AI_TYPES = [PERSON_DETECTION_TYPE, VEHICLE_DETECTION_TYPE, PET_DETECTION_TYPE]

# Single source of truth for the raw-wire-key-to-friendly-name conversion —
# consumed by both get_capabilities and get_recent_events so the output
# vocabulary stays friendly-name-only throughout (WR-02).
RAW_TO_FRIENDLY_AI_TYPES = {
    "people": PERSON_DETECTION_TYPE,
    "dog_cat": PET_DETECTION_TYPE,
}


def _is_auth_or_session_failure(exc: ReolinkError) -> bool:
    """True for exceptions that mean "don't bother retrying the next stream"
    — a fresh login attempt against `main` after one of these would either
    fail identically (bad credentials) or accelerate the account-lockout/
    session-limit condition itself (CR-02 / G2, threat T-05-02). Mirrors
    `errors.py::classify_reolink_error`'s own session-limit substring test
    exactly (same two substrings, same LoginError type check) so the two
    never diverge."""
    if isinstance(exc, CredentialsInvalidError):
        return True
    return isinstance(exc, LoginError) and (
        "max session" in str(exc) or "-5" in str(exc)
    )


async def list_cameras(ctx: Context) -> dict:
    """Probe every configured camera concurrently; return one row per camera
    with name/status/model/host — partial success, one dead camera never
    fails the whole call (D-05 parallel probe, D-07 partial success, D-08
    per-row content)."""
    manager = ctx.request_context.lifespan_context.manager

    async def _probe(name: str) -> dict:
        try:
            # Safety net ABOVE the manager's own Host(timeout=10): real
            # P437/P320 cold connects take >3s, so a tighter budget here
            # preempts the manager's curated timeout error and cancels
            # login mid-flight, leaking the Host's aiohttp session
            # (found in Phase 1 hardware QA). This only fires if
            # manager.get() hangs past its own internal timeout.
            async with asyncio.timeout(12):
                handle = await manager.get(name)
            return {
                "name": name,
                "status": "connected",
                "model": handle.host.model,
                "host": manager.configured_host(name),
            }
        except CameraError as exc:
            # manager.get() (Plan 01-02) already curated this message via
            # classify_reolink_error — str(exc) IS the final text. Reuse it
            # verbatim; do NOT re-classify a CameraError instance, which
            # would match none of classify_reolink_error's isinstance/
            # substring branches and silently collapse to the generic
            # fallback message (the exact regression this branch guards
            # against — 01-03-PLAN.md interfaces section).
            return {
                "name": name,
                "status": str(exc),
                "model": None,
                "host": manager.configured_host(name),
            }
        except Exception as exc:
            # Only reached for exceptions manager.get() itself does not
            # wrap — concretely, this function's own asyncio.timeout(3)
            # firing if manager.get() hangs past the probe budget. This is
            # a raw, not-yet-curated exception, so calling
            # classify_reolink_error here (and only here) is correct.
            return {
                "name": name,
                "status": classify_reolink_error(
                    exc, name, manager.configured_host(name)
                ),
                "model": None,
                "host": manager.configured_host(name),
            }

    results = await asyncio.gather(
        *(_probe(name) for name in manager.configured_names())
    )
    return {"cameras": results}


async def get_snapshot(camera: str, ctx: Context) -> tuple[str, Image]:
    """Return a live snapshot from `camera`: sub-stream attempted first,
    main-stream only as a fallback (D-02), downscaled unconditionally to
    ~1280px long edge / JPEG quality 80 (D-03) regardless of which stream
    produced the bytes, and returned as an image content block plus a short
    text caption naming the camera, capture time, and post-downscale
    resolution (D-01).

    `UnknownCameraError`/`CameraError` raised by `manager.get()` propagate
    uncaught here — same discipline as Plan 01-03's interface note: the
    low-level MCP server converts the raised exception's `str()` into the
    tool's error text, and `CameraError.__str__`/`UnknownCameraError.__str__`
    is already the curated message (D-04 for unknown camera names)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)

    # Sub-stream first, main-stream fallback (D-02) — get_snapshot()'s own
    # default is "main", NOT "sub", so stream must always be passed
    # explicitly (01-RESEARCH.md Pattern 3). `except ReolinkError` catches
    # reolink-aio's own error hierarchy so a failed sub attempt can still
    # fall back to main; `except Exception` is the catch-all guaranteeing a
    # non-ReolinkError transport failure (e.g. a raw aiohttp connection drop
    # mid-session) is translated into a curated CameraError instead of
    # propagating as an unhandled traceback (T-04-01). Every ReolinkError
    # raised by either attempt is retained in `last_exc` and classified via
    # classify_reolink_error's curated taxonomy when both attempts produce
    # no data (CR-02 / G2) — it is never silently discarded. An auth/
    # session-class failure (CredentialsInvalidError, or a session-limit
    # LoginError) on the sub attempt raises immediately without trying
    # main, avoiding a second failed login (threat T-05-02).
    last_exc: Exception | None = None
    try:
        data = await handle.host.get_snapshot(handle.channel, stream="sub")
    except ReolinkError as exc:
        if _is_auth_or_session_failure(exc):
            raise CameraError(
                classify_reolink_error(exc, camera, manager.configured_host(camera))
            ) from exc
        last_exc, data = exc, None
    except Exception as exc:
        raise CameraError(
            classify_reolink_error(exc, camera, manager.configured_host(camera))
        ) from exc

    if not data:
        try:
            data = await handle.host.get_snapshot(handle.channel, stream="main")
        except ReolinkError as exc:
            last_exc, data = exc, None
        except Exception as exc:
            raise CameraError(
                classify_reolink_error(exc, camera, manager.configured_host(camera))
            ) from exc

    if not data:
        if last_exc is not None:
            host = manager.configured_host(camera)
            raise CameraError(
                classify_reolink_error(last_exc, camera, host)
            ) from last_exc
        raise CameraError(
            f"camera '{camera}' returned no image — privacy mode may be "
            "enabled, or the camera is mid-reboot"
        )

    # Unconditional downscale (D-02/D-03) — never skipped, regardless of
    # which stream produced the bytes: prevents oversized payloads
    # (PITFALLS.md Pitfall 3, T-04-02) from poisoning the client session or
    # exceeding token/byte/dimension limits.
    im = PILImage.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((1280, 1280), PILImage.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=80, optimize=True)
    jpeg_bytes = out.getvalue()

    caption = (
        f"{camera} — captured {datetime.now(UTC).isoformat()} — {im.width}x{im.height}"
    )

    return (caption, Image(data=jpeg_bytes, format="jpeg"))


def _standalone_channel_fallback(
    getter: Callable[[int | None], str | None], channel: int | None, is_nvr: bool
) -> str | None:
    """Fall back to the `None`-keyed value when `getter(channel)` returns
    `None` for a standalone (non-NVR) camera — mirrors the installed
    `reolink_aio.api.Host.camera_model()`'s own `not self.is_nvr` None-key
    fallback (0.21.3) exactly. `Host.serial()`/`Host.item_number()` lack
    this fallback: for a standalone host, `get_host_data()`'s `GetDevInfo`
    response only ever populates `self._serial[None]`/`self._item_number
    [None]` — the numeric-channel key is never set, so `host.serial(0)`/
    `host.item_number(0)` are unconditionally `None` without this helper
    (02-VERIFICATION.md gap #1). Never applied when `is_nvr` is `True` —
    an NVR channel that genuinely has no serial must not borrow its
    parent's."""
    value = getter(channel)
    if value is None and channel is not None and not is_nvr:
        return getter(None)
    return value


async def get_device_info(
    camera: str, ctx: Context, full: bool = False
) -> dict[str, Any]:
    """Model, firmware, hardware details for `camera`, read directly off the
    already-connected `Host` — zero additional `reolink-aio` calls beyond
    `manager.get()`'s own connect step, which already fetched every field
    below via `get_host_data()` (RESEARCH.md Pattern 1). `full=True` adds
    `is_nvr`/`is_battery`/`num_channels` (D-02).

    `UnknownCameraError`/`CameraError` raised by `manager.get()` propagate
    uncaught here — same discipline as `get_snapshot` (this function issues
    no additional awaited host calls that can fail)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    host, ch = handle.host, handle.channel

    info: dict[str, Any] = {
        "camera": camera,
        "model": host.model,
        "item_number": _standalone_channel_fallback(host.item_number, ch, host.is_nvr),
        "firmware_version": host.sw_version,
        "hardware_version": host.hardware_version,
        "serial": _standalone_channel_fallback(host.serial, ch, host.is_nvr),
        "mac_address": host.mac_address,
        "manufacturer": host.manufacturer,
        "configured_host": manager.configured_host(camera),
        "channel": ch,
    }
    if full:
        info["is_nvr"] = host.is_nvr
        info["is_battery"] = host.is_battery
        info["num_channels"] = host.num_channels
    return info


async def get_capabilities(
    camera: str, ctx: Context, full: bool = False
) -> dict[str, Any]:
    """What `camera` supports, in neutral hardware-feature vocabulary
    (D-11): one boolean per `CAPABILITY_MAP` key, built via
    `capabilities.gate()` so the vocabulary is defined exactly once, plus
    the dynamic `ai_detection_types` list. `full=True` additionally exposes
    `raw_capabilities` (every raw capability string for the channel) and
    `siren_schedule` (the separate "siren" capability governing the
    out-of-scope `set_audio_alarm` feature — distinct from the curated
    `siren` key's `siren_play` check, informational only, never in the
    curated default).

    Same zero-extra-I/O discipline as `get_device_info` — `manager.get()`'s
    connect step already populated everything `host.supported()` reads."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    host, ch = handle.host, handle.channel

    caps: dict[str, Any] = {
        "camera": camera,
        **{key: gate(handle, key) for key in CAPABILITY_MAP},
        "ai_detection_types": [
            RAW_TO_FRIENDLY_AI_TYPES.get(t, t) for t in host.ai_supported_types(ch)
        ],
    }
    if full:
        caps["raw_capabilities"] = sorted(host.capabilities.get(ch, set()))
        caps["siren_schedule"] = host.supported(ch, "siren")
        caps["raw_ai_types"] = host.ai_supported_types(ch)
    return caps


def states_cmd_list(channel: int) -> dict[str, list[int]]:
    """Narrow `cmd_list` for the roadmap `get_states`/`get_recent_events`
    set — deliberately excludes `GetEnc`/`GetBatteryInfo`/`GetZoomFocus`/etc.
    that `host.get_states(cmd_list=None)` would otherwise also fetch,
    keeping the HTTP payload small (HDWR-03's shared-session-friendliness,
    threat T-02-02). `GetEvents`/`GetMdState`/`GetAiState` (motion + AI) are
    fetched unconditionally by `host.get_states()` regardless of `cmd_list`
    content (verified against reolink_aio 0.21.3's `get_states` source) — no
    separate call is needed for `get_recent_events` (RESEARCH.md Pattern
    3)."""
    return {
        "GetIsp": [channel],
        "GetIrLights": [channel],
        "GetWhiteLed": [channel],
        "GetAudioAlarm": [channel],
    }


async def get_states(
    camera: str, ctx: Context, refresh: bool = False, full: bool = False
) -> dict[str, Any]:
    """Current device state for `camera`: day/night mode, white LED/
    spotlight, IR lights, siren capability, and plain motion flag, plus
    `polled_at`/`age_seconds` staleness metadata (D-05).

    Serves cached state by default (`refresh=False`); pass `refresh=True` to
    force a fresh camera poll. The very first call for a camera in this
    server process ALWAYS forces a poll regardless of `refresh` — a freshly
    connected camera has never had `GetIsp`/`GetIrLights`/`GetWhiteLed`/
    `GetAudioAlarm`/motion/AI fetched (`manager.get()`'s own
    `get_host_data()` call does not include them), so skipping this guard
    would silently report fabricated "off"/"not detected" defaults instead
    of real state (Pitfall 1). `full=True` widens the poll to every
    subsystem the camera reports (`cmd_list=None`) and always forces its own
    refresh, regardless of `refresh` — a full-labeled response must never
    secretly serve a stale narrow-poll cache.

    Hardware-absent fields are marked `"unsupported"` via `capabilities.gate()`
    (D-09) rather than silently omitted or fabricated as `False`. The siren
    field reports only capability (`"supported"`/`"unsupported"`), never a
    live on/off state — no such getter exists in `reolink-aio`; the siren is
    a fire-and-forget trigger, not a queryable device state.

    `UnknownCameraError` from `manager.get()` propagates uncaught (same
    discipline as `get_snapshot`/`get_device_info`); poll failures from
    `host.get_states(cmd_list=...)` are translated into a curated
    `CameraError` via `classify_reolink_error` (T-02-01) instead of leaking
    a raw exception."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    host, ch = handle.host, handle.channel

    if refresh or full or handle.states_polled_at is None:
        cmd_list = None if full else states_cmd_list(ch)
        try:
            await host.get_states(cmd_list=cmd_list)
        except Exception as exc:
            raise CameraError(
                classify_reolink_error(exc, camera, manager.configured_host(camera))
            ) from exc
        handle.states_polled_at = datetime.now(UTC)

    age = (datetime.now(UTC) - handle.states_polled_at).total_seconds()
    result: dict[str, Any] = {
        "camera": camera,
        "day_night": host.daynight_state(ch)
        if gate(handle, "day_night")
        else "unsupported",
        "white_led": (
            {"on": host.whiteled_state(ch), "brightness": host.whiteled_brightness(ch)}
            if gate(handle, "white_led")
            else "unsupported"
        ),
        "ir_lights": host.ir_enabled(ch)
        if gate(handle, "ir_lights")
        else "unsupported",
        "siren": "supported" if gate(handle, "siren") else "unsupported",
        "motion": host.motion_detected(ch),
        "polled_at": handle.states_polled_at.isoformat(),
        "age_seconds": round(age, 1),
    }
    if full:
        result["status_led"] = (
            host.status_led_enabled(ch)
            if host.supported(ch, "status_led")
            else "unsupported"
        )
        result["battery_percentage"] = (
            host.battery_percentage(ch) if host.is_battery else None
        )
        result["audio_alarm_enabled"] = (
            host.audio_alarm_enabled(ch)
            if host.supported(ch, "siren")
            else "unsupported"
        )
    return result


async def get_recent_events(
    camera: str, ctx: Context, refresh: bool = False, full: bool = False
) -> dict[str, Any]:
    """Current AI detection state for `camera` from an on-demand poll — this
    is current state, not event history; it reports whatever the camera's
    live detection flags say right now, at the moment of the poll (D-04).
    Flags may be stale unless `refresh=True` is passed — see
    `polled_at`/`age_seconds`.

    Reports tri-state `"detected"`/`"not_detected"`/`"unsupported"` for the
    baseline person/vehicle/pet trio (D-06), plus any additional AI type
    the camera's firmware dynamically reports (e.g. face/package on richer
    models) with zero code changes needed (D-07). Also includes the plain,
    non-AI `motion` flag, independent of any AI type's support/detection
    state (D-08).

    Shares `get_states`' EXACT same refresh/first-poll gating — both tools
    observe one `CameraHandle.states_polled_at` clock, not two independent
    ones (D-04). `host.get_states()` already fetches motion + AI detection
    unconditionally regardless of `cmd_list` content, so no separate AI-
    state call is needed here.

    The raw wire keys `reolink-aio` returns from `ai_supported_types()` do
    NOT match the friendly constants (`"people"`/`"dog_cat"` vs `"person"`/
    `"pet"`, Pitfall 2) — the baseline trio goes through `ai_supported()`/
    `ai_detected()` (which apply the conversion internally), and the
    dynamic-extras loop below applies the reverse map explicitly so the
    output vocabulary stays friendly-name-only throughout."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)
    host, ch = handle.host, handle.channel

    # SAME refresh call/gate as get_states (D-04) — not a second, divergent
    # refresh mechanism.
    if refresh or full or handle.states_polled_at is None:
        cmd_list = None if full else states_cmd_list(ch)
        try:
            await host.get_states(cmd_list=cmd_list)
        except Exception as exc:
            raise CameraError(
                classify_reolink_error(exc, camera, manager.configured_host(camera))
            ) from exc
        handle.states_polled_at = datetime.now(UTC)

    report: dict[str, str] = {}
    seen: set[str] = set()
    for detect_type in BASELINE_AI_TYPES:
        seen.add(detect_type)
        if host.ai_supported(ch, detect_type):
            report[detect_type] = (
                "detected" if host.ai_detected(ch, detect_type) else "not_detected"
            )
        else:
            report[detect_type] = "unsupported"
    for raw_type in host.ai_supported_types(ch):
        friendly = RAW_TO_FRIENDLY_AI_TYPES.get(raw_type, raw_type)
        if friendly in seen:
            continue
        seen.add(friendly)
        report[friendly] = (
            "detected" if host.ai_detected(ch, raw_type) else "not_detected"
        )

    age = (datetime.now(UTC) - handle.states_polled_at).total_seconds()
    result: dict[str, Any] = {
        "camera": camera,
        **report,
        "motion": host.motion_detected(ch),
        "polled_at": handle.states_polled_at.isoformat(),
        "age_seconds": round(age, 1),
    }
    if full:
        result["raw_ai_types"] = host.ai_supported_types(ch)
    return result
