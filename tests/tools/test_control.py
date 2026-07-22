"""Tests for control tools: `set_siren` (Phase 3 Plan 1, Task 1);
`set_spotlight`/`set_ir_lights`/`set_white_led` (Phase 3 Plan 1, Task 2);
registration + RMCP_READ_ONLY-driven tool-count/annotation checks (CTRL-01,
CTRL-02, CTRL-03, CTRL-04, CTRL-10, SAFE-02, D-01..D-07, D-13).

Mirrors `tests/tools/test_observe.py`'s fixture/mocking conventions exactly:
`_fake_ctx` is duplicated here (not cross-imported), and `host.supported` is
always a per-capability-string dict lookup, never a single blanket bool —
catches the exact siren/siren_play string-mismatch bug class Phase 2's
Pitfall 3/4 discipline guards against.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP
from reolink_aio.exceptions import InvalidParameterError, ReolinkConnectionError

from reolink_mcp.capabilities import refusal_message
from reolink_mcp.errors import CameraError
from reolink_mcp.manager import CameraManager
from reolink_mcp.tools import register_all
from reolink_mcp.tools.control import (
    PTZ_MOVE_DEFAULT_DURATION_S,
    PTZ_SETTLE_WAIT_S,
    list_presets,
    ptz_guard,
    ptz_move,
    ptz_move_to_preset,
    ptz_position,
    set_audio_alarm,
    set_ir_lights,
    set_siren,
    set_spotlight,
    set_white_led,
    set_zoom,
)

# Plan 03-03 Task 1 (+ checkpoint deviation): the full, final 17-tool
# registry (6 observe + 11 control) — the literal name sets SAFE-01/SAFE-02's
# hard regression tests assert against, defined once here to avoid drift
# between the two tests. `set_audio_alarm` was added during the Plan 03-03
# hardware checkpoint after live P437 QA found `set_siren` silently
# suppressed while the camera's audio-alarm feature is disabled. `ptz_move`
# (raw directional pan/tilt, distinct from the fixed-target
# `ptz_move_to_preset`) was added later as a locally-maintained fork addition.
_ALL_SEVENTEEN_TOOL_NAMES = {
    "list_cameras",
    "get_snapshot",
    "get_device_info",
    "get_capabilities",
    "get_states",
    "get_recent_events",
    "set_siren",
    "set_audio_alarm",
    "set_spotlight",
    "set_ir_lights",
    "set_white_led",
    "set_zoom",
    "list_presets",
    "ptz_move_to_preset",
    "ptz_move",
    "ptz_position",
    "ptz_guard",
}
_SIX_OBSERVE_TOOL_NAMES = {
    "list_cameras",
    "get_snapshot",
    "get_device_info",
    "get_capabilities",
    "get_states",
    "get_recent_events",
}


def _fake_ctx(manager: CameraManager) -> SimpleNamespace:
    """Minimal stand-in for a FastMCP `Context`, exposing only the nested
    attribute path control tools actually read:
    `ctx.request_context.lifespan_context.manager`."""
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(manager=manager)
        )
    )


def _per_string_supported(mapping: dict[str, bool]):
    return lambda channel, cap: mapping.get(cap, False)


def _configure_siren_capable(host) -> None:
    host.supported = _per_string_supported({"siren_play": True})
    host.set_siren = AsyncMock()


def _configure_white_led_capable(
    host, *, whiteled_on: bool = True, brightness: int = 80
) -> None:
    host.supported = _per_string_supported({"floodLight": True})
    host.set_spotlight = AsyncMock()
    host.set_whiteled = AsyncMock()
    host.whiteled_state = lambda channel: whiteled_on
    host.whiteled_brightness = lambda channel: brightness


def _configure_ir_lights_capable(host, *, raw_state: str = "Auto") -> None:
    host.supported = _per_string_supported({"ir_lights": True})
    host.set_ir_lights = AsyncMock()
    host.send_setting = AsyncMock()
    host._ir_settings = {0: {"state": raw_state}}


def _configure_zoom_capable(
    host, *, zmin: int = 0, zmax: int = 30, current: int = 10
) -> None:
    host.supported = _per_string_supported({"zoom": True})
    host.zoom_range = lambda channel: {"zoom": {"min": zmin, "max": zmax}}
    host.get_zoom = lambda channel: current
    host.set_zoom = AsyncMock()


def _configure_ptz_presets_capable(host, presets: dict[str, int]) -> None:
    host.supported = _per_string_supported({"ptz_presets": True})
    host.ptz_presets = lambda channel: presets
    host.set_ptz_command = AsyncMock()
    host.ptz_pan_position = lambda channel: None
    host.ptz_tilt_position = lambda channel: None


def _configure_pan_tilt_capable(
    host,
    *,
    extra_supported: dict[str, bool] | None = None,
    pan: int | None = None,
    tilt: int | None = None,
) -> None:
    caps = {"pan_tilt": True}
    if extra_supported:
        caps.update(extra_supported)
    host.supported = _per_string_supported(caps)
    host.ptz_pan_position = lambda channel: pan
    host.ptz_tilt_position = lambda channel: tilt


def _configure_ptz_move_capable(
    host,
    *,
    pan: int | None = None,
    tilt: int | None = None,
) -> None:
    host.supported = _per_string_supported({"pan_tilt": True})
    host.set_ptz_command = AsyncMock()
    host.ptz_pan_position = lambda channel: pan
    host.ptz_tilt_position = lambda channel: tilt


def _configure_ptz_guard_capable(
    host, *, enabled: bool = True, return_time_s: int = 60
) -> None:
    host.supported = _per_string_supported({"ptz_guard": True})
    host.set_ptz_guard = AsyncMock()
    host.send_setting = AsyncMock()
    host.ptz_guard_enabled = lambda channel: enabled
    host.ptz_guard_time = lambda channel: return_time_s


# ---------------------------------------------------------------------------
# set_siren (D-01..D-04)
# ---------------------------------------------------------------------------


async def test_set_siren_sound_no_duration_uses_default(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_siren_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_siren("front_door", _fake_ctx(manager), action="sound")

    host.set_siren.assert_awaited_once_with(0, enable=True, duration=5)
    assert result["duration"] == 5


async def test_set_siren_sound_duration_60_is_allowed_boundary(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_siren_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_siren("front_door", _fake_ctx(manager), action="sound", duration=60)

    host.set_siren.assert_awaited_once_with(0, enable=True, duration=60)


async def test_set_siren_sound_duration_61_refused_never_clamped(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_siren_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_siren("front_door", _fake_ctx(manager), action="sound", duration=61)

    assert "60" in str(exc_info.value)
    host.set_siren.assert_not_awaited()


@pytest.mark.parametrize("duration", [0, -1])
async def test_set_siren_sound_duration_below_one_refused_never_sent(
    mock_host_factory, camera_config_factory, manager_factory, duration
):
    """WR-01: the lower bound is refused, not clamped — reolink-aio performs
    no range check of its own and would hand the firmware a raw `times: 0`
    or negative value with undefined, model-dependent behavior."""
    host = mock_host_factory()
    _configure_siren_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_siren(
            "front_door", _fake_ctx(manager), action="sound", duration=duration
        )

    assert "at least 1" in str(exc_info.value)
    host.set_siren.assert_not_awaited()


async def test_set_siren_stop_calls_enable_false_and_duration_none(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_siren_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_siren("front_door", _fake_ctx(manager), action="stop")

    host.set_siren.assert_awaited_once_with(0, enable=False)
    assert result["duration"] is None


async def test_set_siren_gate_failure_refuses_without_awaiting(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"siren_play": False})
    host.set_siren = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_siren("front_door", _fake_ctx(manager), action="sound")

    assert refusal_message("front_door", "siren") in str(exc_info.value)
    host.set_siren.assert_not_awaited()


async def test_set_siren_sound_success_returns_read_back_dict(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_siren_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_siren(
        "front_door", _fake_ctx(manager), action="sound", duration=5
    )

    assert result["camera"] == "front_door"
    assert result["action"] == "sound"
    assert result["duration"] == 5
    assert "no live siren-state getter" in result["note"]


async def test_set_siren_host_error_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_siren_capable(host)
    host.set_siren = AsyncMock(side_effect=ReolinkConnectionError("refused"))
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_siren("front_door", _fake_ctx(manager), action="sound")

    assert "refused" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# set_audio_alarm (Plan 03-03 checkpoint deviation)
# ---------------------------------------------------------------------------


def _configure_audio_alarm_capable(host, *, enabled_readback: bool = True) -> None:
    # Raw "siren" capability — deliberately NOT "siren_play" (Pitfall 3):
    # SetAudioAlarm gates on the schedule capability, and the per-string
    # dict lookup ensures a curated-key/raw-string mix-up in the tool would
    # fail loudly here instead of passing on a blanket-True mock.
    host.supported = _per_string_supported({"siren": True})
    host.set_audio_alarm = AsyncMock()
    host.audio_alarm_enabled = lambda channel: enabled_readback


async def test_set_audio_alarm_enable_calls_host_and_returns_read_back(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_audio_alarm_capable(host, enabled_readback=True)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_audio_alarm("front_door", _fake_ctx(manager), enabled=True)

    host.set_audio_alarm.assert_awaited_once_with(0, True)
    assert result == {"camera": "front_door", "audio_alarm_enabled": True}


async def test_set_audio_alarm_disable_calls_host_and_returns_read_back(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_audio_alarm_capable(host, enabled_readback=False)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_audio_alarm("front_door", _fake_ctx(manager), enabled=False)

    host.set_audio_alarm.assert_awaited_once_with(0, False)
    assert result == {"camera": "front_door", "audio_alarm_enabled": False}


async def test_set_audio_alarm_gates_on_raw_siren_not_siren_play(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    # siren_play=True alone must NOT satisfy set_audio_alarm's gate — the
    # tool gates on the raw "siren" (schedule) capability, mirroring
    # reolink-aio's own set_audio_alarm() NotSupportedError check.
    host.supported = _per_string_supported({"siren_play": True})
    host.set_audio_alarm = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_audio_alarm("front_door", _fake_ctx(manager), enabled=True)

    assert "audio alarm" in str(exc_info.value)
    host.set_audio_alarm.assert_not_awaited()


async def test_set_audio_alarm_host_error_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_audio_alarm_capable(host)
    host.set_audio_alarm = AsyncMock(side_effect=ReolinkConnectionError("refused"))
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_audio_alarm("front_door", _fake_ctx(manager), enabled=True)

    assert "refused" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# set_spotlight (D-05)
# ---------------------------------------------------------------------------


async def test_set_spotlight_on_calls_host_and_returns_state(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_white_led_capable(host, whiteled_on=True)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_spotlight("front_door", _fake_ctx(manager), on=True)

    host.set_spotlight.assert_awaited_once_with(0, True)
    assert result == {"camera": "front_door", "spotlight": {"on": True}}


async def test_set_spotlight_off_calls_host_with_false(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_white_led_capable(host, whiteled_on=False)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_spotlight("front_door", _fake_ctx(manager), on=False)

    host.set_spotlight.assert_awaited_once_with(0, False)


async def test_set_spotlight_gate_failure_shares_white_led_capability_key(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"floodLight": False})
    host.set_spotlight = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_spotlight("front_door", _fake_ctx(manager), on=True)

    assert refusal_message("front_door", "white_led") in str(exc_info.value)
    host.set_spotlight.assert_not_awaited()


# ---------------------------------------------------------------------------
# set_white_led (D-07)
# ---------------------------------------------------------------------------


async def test_set_white_led_on_with_brightness(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_white_led_capable(host, whiteled_on=True, brightness=75)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_white_led(
        "front_door", _fake_ctx(manager), on=True, brightness=75
    )

    host.set_whiteled.assert_awaited_once_with(0, state=True, brightness=75, mode=None)
    assert result == {
        "camera": "front_door",
        "white_led": {"on": True, "brightness": 75},
    }


async def test_set_white_led_omitted_brightness_passes_through_none(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_white_led_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_white_led("front_door", _fake_ctx(manager), on=True)

    host.set_whiteled.assert_awaited_once_with(
        0, state=True, brightness=None, mode=None
    )


async def test_set_white_led_gate_failure_refuses_without_awaiting(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"floodLight": False})
    host.set_whiteled = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_white_led("front_door", _fake_ctx(manager), on=True)

    assert refusal_message("front_door", "white_led") in str(exc_info.value)
    host.set_whiteled.assert_not_awaited()


# ---------------------------------------------------------------------------
# set_ir_lights (D-06, Pitfall 2/3)
# ---------------------------------------------------------------------------


async def test_set_ir_lights_mode_on_uses_send_setting_literal_bypass(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ir_lights_capable(host, raw_state="On")
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_ir_lights("front_door", _fake_ctx(manager), mode="on")

    host.send_setting.assert_awaited_once_with(
        [
            {
                "cmd": "SetIrLights",
                "action": 0,
                "param": {"IrLights": {"channel": 0, "state": "On"}},
            }
        ]
    )
    host.set_ir_lights.assert_not_awaited()


async def test_set_ir_lights_mode_auto_calls_set_ir_lights_enable_true(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ir_lights_capable(host, raw_state="Auto")
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_ir_lights("front_door", _fake_ctx(manager), mode="auto")

    host.set_ir_lights.assert_awaited_once_with(0, enable=True)


async def test_set_ir_lights_mode_off_calls_set_ir_lights_enable_false(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ir_lights_capable(host, raw_state="Off")
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_ir_lights("front_door", _fake_ctx(manager), mode="off")

    host.set_ir_lights.assert_awaited_once_with(0, enable=False)


@pytest.mark.parametrize(
    "raw_state,expected",
    [("On", "on"), ("Auto", "auto"), ("Off", "off")],
)
async def test_set_ir_lights_read_back_maps_capitalized_wire_values(
    raw_state, expected, mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ir_lights_capable(host, raw_state=raw_state)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_ir_lights("front_door", _fake_ctx(manager), mode="auto")

    assert result == {"camera": "front_door", "ir_lights": expected}


async def test_set_ir_lights_gate_failure_calls_neither_setter(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"ir_lights": False})
    host.set_ir_lights = AsyncMock()
    host.send_setting = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_ir_lights("front_door", _fake_ctx(manager), mode="auto")

    assert refusal_message("front_door", "ir_lights") in str(exc_info.value)
    host.set_ir_lights.assert_not_awaited()
    host.send_setting.assert_not_awaited()


# ---------------------------------------------------------------------------
# set_zoom (D-08, Pattern 3)
# ---------------------------------------------------------------------------


async def test_set_zoom_neither_position_nor_step_raises_without_host_call(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager))

    assert "exactly one of position or step" in str(exc_info.value)
    host.set_zoom.assert_not_awaited()


async def test_set_zoom_both_position_and_step_raises_without_host_call(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager), position=50, step=1)

    assert "exactly one of position or step" in str(exc_info.value)
    host.set_zoom.assert_not_awaited()


async def test_set_zoom_absolute_position_maps_into_raw_range(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=10)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_zoom("front_door", _fake_ctx(manager), position=50)

    host.set_zoom.assert_awaited_once_with(0, 15)


@pytest.mark.parametrize(("position", "expected_raw"), [(0, 0), (100, 30)])
async def test_set_zoom_absolute_position_boundaries(
    position, expected_raw, mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=10)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_zoom("front_door", _fake_ctx(manager), position=position)

    host.set_zoom.assert_awaited_once_with(0, expected_raw)


async def test_set_zoom_position_over_100_refused_without_host_call(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=10)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager), position=101)

    assert "0..100" in str(exc_info.value)
    host.set_zoom.assert_not_awaited()


@pytest.mark.parametrize(("step", "expected_raw"), [(1, 13), (-1, 7)])
async def test_set_zoom_relative_step_computed_from_range_pct(
    step, expected_raw, mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=10)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_zoom("front_door", _fake_ctx(manager), step=step)

    host.set_zoom.assert_awaited_once_with(0, expected_raw)


async def test_set_zoom_relative_step_clamped_to_max_not_refused(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=29)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await set_zoom("front_door", _fake_ctx(manager), step=1)

    host.set_zoom.assert_awaited_once_with(0, 30)


async def test_set_zoom_gate_failure_refuses_without_awaiting(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"zoom": False})
    host.set_zoom = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager), position=50)

    assert refusal_message("front_door", "zoom") in str(exc_info.value)
    host.set_zoom.assert_not_awaited()


async def test_set_zoom_success_returns_read_back_dict(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=15)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await set_zoom("front_door", _fake_ctx(manager), position=50)

    assert result == {
        "camera": "front_door",
        "zoom": {"raw": 15, "position_pct": 50, "range": {"min": 0, "max": 30}},
    }


async def test_set_zoom_host_error_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_zoom_capable(host, zmin=0, zmax=30, current=10)
    host.set_zoom = AsyncMock(
        side_effect=InvalidParameterError("set_zoom: zoom value 15 not in range 0..30")
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager), position=50)

    msg = str(exc_info.value)
    assert "rejected" in msg
    assert "set_zoom:" not in msg  # func_name prefix must be stripped


async def test_set_zoom_unpopulated_range_read_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    """WR-02: zoom_range() is a bare dict index in reolink-aio — it raises
    KeyError when _zoom_focus_settings was never populated, a condition the
    zoom gate does not preclude. The raw KeyError must never escape."""
    host = mock_host_factory()
    _configure_zoom_capable(host)

    def unpopulated_zoom_range(channel):
        raise KeyError(channel)

    host.zoom_range = unpopulated_zoom_range
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager), position=50)

    assert "KeyError" not in str(exc_info.value)
    assert "front_door" in str(exc_info.value)
    host.set_zoom.assert_not_awaited()


async def test_set_zoom_step_current_read_error_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    """WR-02: the relative-step path's get_zoom() read raises
    InvalidParameterError when the settings cache is empty — must surface as
    a curated, prefix-stripped CameraError, never the raw library text."""
    host = mock_host_factory()
    _configure_zoom_capable(host)

    def failing_get_zoom(channel):
        raise InvalidParameterError("get_zoom: no ZoomFocus data for channel 0")

    host.get_zoom = failing_get_zoom
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await set_zoom("front_door", _fake_ctx(manager), step=1)

    msg = str(exc_info.value)
    assert "rejected" in msg
    assert "get_zoom:" not in msg  # func_name prefix must be stripped
    host.set_zoom.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_presets (CTRL-06, Pattern 1)
# ---------------------------------------------------------------------------


async def test_list_presets_returns_presets_with_zero_extra_io(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_presets_capable(host, {"driveway": 1, "gate": 2})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await list_presets("front_door", _fake_ctx(manager))

    assert result == {
        "camera": "front_door",
        "presets": {"driveway": 1, "gate": 2},
    }
    host.set_ptz_command.assert_not_awaited()
    host.baichuan.get_ptz_position.assert_not_awaited()


async def test_list_presets_gate_failure_refuses(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"ptz_presets": False})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await list_presets("front_door", _fake_ctx(manager))

    assert refusal_message("front_door", "ptz_presets") in str(exc_info.value)


# ---------------------------------------------------------------------------
# ptz_move_to_preset (D-09, D-12, Pattern 4/5)
# ---------------------------------------------------------------------------


async def test_ptz_move_to_preset_by_name_resolves_id_and_repolls_position(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_presets_capable(host, {"driveway": 1, "gate": 2})
    host.ptz_pan_position = lambda channel: 100
    host.ptz_tilt_position = lambda channel: 200
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await ptz_move_to_preset(
        "front_door", _fake_ctx(manager), preset="driveway"
    )

    host.set_ptz_command.assert_awaited_once_with(0, preset=1)
    host.baichuan.get_ptz_position.assert_awaited_once_with(0)
    assert result == {
        "camera": "front_door",
        "preset": "driveway",
        "pan": 100,
        "tilt": 200,
    }
    handle = await manager.get("front_door")
    assert handle.preset_positions[1] == (100, 200)


async def test_ptz_move_to_preset_unknown_name_lists_available_presets(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_presets_capable(host, {"driveway": 1, "gate": 2})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_move_to_preset(
            "front_door", _fake_ctx(manager), preset="nonexistent"
        )

    assert "driveway" in str(exc_info.value)
    assert "gate" in str(exc_info.value)
    host.set_ptz_command.assert_not_awaited()


async def test_ptz_move_to_preset_by_numeric_id_skips_name_resolution(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_presets_capable(host, {"driveway": 1, "gate": 2})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await ptz_move_to_preset("front_door", _fake_ctx(manager), preset=2)

    host.set_ptz_command.assert_awaited_once_with(0, preset=2)


async def test_ptz_move_to_preset_settle_wait_before_position_repoll(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_presets_capable(host, {"driveway": 1})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    call_order: list[str] = []
    sleep_mock = AsyncMock(side_effect=lambda *_a, **_k: call_order.append("sleep"))
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", sleep_mock)
    original_get_ptz_position = host.baichuan.get_ptz_position

    async def recording_get_ptz_position(*args, **kwargs):
        call_order.append("get_ptz_position")
        return await original_get_ptz_position(*args, **kwargs)

    host.baichuan.get_ptz_position = AsyncMock(side_effect=recording_get_ptz_position)

    await ptz_move_to_preset("front_door", _fake_ctx(manager), preset="driveway")

    sleep_mock.assert_awaited_once_with(2)
    assert call_order == ["sleep", "get_ptz_position"]


async def test_ptz_move_to_preset_repoll_failure_degrades_never_leaks_raw_text(
    mock_host_factory, camera_config_factory, manager_factory
):
    """CR-01: the move itself already succeeded, so a failed Baichuan re-poll
    must degrade to pan/tilt=None with a note — never fail the call, never
    surface the raw Baichuan exception text (which embeds wire hex dumps)."""
    host = mock_host_factory()
    _configure_ptz_presets_capable(host, {"driveway": 1})
    host.baichuan.get_ptz_position = AsyncMock(
        side_effect=ReolinkConnectionError("baichuan header: deadbeefcafe")
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await ptz_move_to_preset(
        "front_door", _fake_ctx(manager), preset="driveway"
    )

    host.set_ptz_command.assert_awaited_once_with(0, preset=1)
    assert result["preset"] == "driveway"
    assert result["pan"] is None
    assert result["tilt"] is None
    assert "re-poll" in result["note"]
    assert "deadbeefcafe" not in str(result)
    handle = await manager.get("front_door")
    assert handle.preset_positions == {}


async def test_ptz_move_to_preset_gate_failure_refuses_without_awaiting(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"ptz_presets": False})
    host.set_ptz_command = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_move_to_preset("front_door", _fake_ctx(manager), preset="driveway")

    assert refusal_message("front_door", "ptz_presets") in str(exc_info.value)
    host.set_ptz_command.assert_not_awaited()


# ---------------------------------------------------------------------------
# ptz_move (raw directional PTZ, locally-maintained fork addition)
# ---------------------------------------------------------------------------


async def test_ptz_move_sends_direction_then_stop_after_duration(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host, pan=50, tilt=60)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", sleep_mock)

    result = await ptz_move("front_door", _fake_ctx(manager), direction="right")

    assert host.set_ptz_command.await_args_list[0].args == (0,)
    assert host.set_ptz_command.await_args_list[0].kwargs == {
        "command": "Right",
        "speed": None,
    }
    assert host.set_ptz_command.await_args_list[1].args == (0,)
    assert host.set_ptz_command.await_args_list[1].kwargs == {"command": "Stop"}
    assert result == {
        "camera": "front_door",
        "direction": "right",
        "pan": 50,
        "tilt": 60,
    }


async def test_ptz_move_default_duration_used_when_omitted(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", sleep_mock)

    await ptz_move("front_door", _fake_ctx(manager), direction="up")

    sleep_mock.assert_any_await(1.0)


async def test_ptz_move_custom_duration_within_cap_used(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", sleep_mock)

    await ptz_move("front_door", _fake_ctx(manager), direction="down", duration=3.5)

    sleep_mock.assert_any_await(3.5)


async def test_ptz_move_duration_over_cap_refused_never_sent(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_move("front_door", _fake_ctx(manager), direction="left", duration=8.1)

    assert "8" in str(exc_info.value)
    host.set_ptz_command.assert_not_awaited()


@pytest.mark.parametrize("duration", [0, -1])
async def test_ptz_move_duration_zero_or_negative_refused(
    mock_host_factory, camera_config_factory, manager_factory, duration
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_move(
            "front_door", _fake_ctx(manager), direction="left", duration=duration
        )

    assert "greater than 0" in str(exc_info.value)
    host.set_ptz_command.assert_not_awaited()


async def test_ptz_move_stop_direction_sends_stop_only_no_duration_wait(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", sleep_mock)

    result = await ptz_move("front_door", _fake_ctx(manager), direction="stop")

    host.set_ptz_command.assert_awaited_once_with(0, command="Stop")
    # the only sleep should be the post-move settle-wait, never a duration wait
    sleep_mock.assert_awaited_once_with(PTZ_SETTLE_WAIT_S)
    assert result["direction"] == "stop"


async def test_ptz_move_stop_after_move_always_sent_even_if_wait_raises(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    """The trailing Stop must fire even when the duration wait itself blows
    up (e.g. the call is cancelled mid-move) — a continuous PTZ command has
    no camera-side timeout, so skipping the stop here would leave the head
    panning unattended."""
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    async def failing_sleep(seconds):
        if seconds == PTZ_MOVE_DEFAULT_DURATION_S:
            raise asyncio.CancelledError()

    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", failing_sleep)

    with pytest.raises(asyncio.CancelledError):
        await ptz_move("front_door", _fake_ctx(manager), direction="right")

    assert host.set_ptz_command.await_args_list[-1].kwargs == {"command": "Stop"}


async def test_ptz_move_speed_passed_through_unvalidated(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    monkeypatch.setattr(
        "reolink_mcp.tools.control.asyncio.sleep", AsyncMock()
    )

    await ptz_move("front_door", _fake_ctx(manager), direction="up", speed=32)

    assert host.set_ptz_command.await_args_list[0].kwargs == {
        "command": "Up",
        "speed": 32,
    }


async def test_ptz_move_gate_failure_refuses_without_awaiting(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"pan_tilt": False})
    host.set_ptz_command = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_move("front_door", _fake_ctx(manager), direction="left")

    assert refusal_message("front_door", "pan_tilt") in str(exc_info.value)
    host.set_ptz_command.assert_not_awaited()


async def test_ptz_move_repoll_failure_degrades_never_leaks_raw_text(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_ptz_move_capable(host)
    host.baichuan.get_ptz_position = AsyncMock(
        side_effect=ReolinkConnectionError("baichuan header: deadbeefcafe")
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", AsyncMock())

    result = await ptz_move("front_door", _fake_ctx(manager), direction="left")

    assert result["pan"] is None
    assert result["tilt"] is None
    assert "re-poll" in result["note"]
    assert "deadbeefcafe" not in str(result)


async def test_ptz_move_trailing_stop_failure_surfaces_note(
    mock_host_factory, camera_config_factory, manager_factory, monkeypatch
):
    """If the Stop call itself fails, the operator must be told explicitly —
    a silently-swallowed Stop failure could leave the camera moving with no
    indication anything went wrong."""
    host = mock_host_factory()
    _configure_ptz_move_capable(host, pan=10, tilt=20)

    async def move_then_fail_stop(channel, command, speed=None):
        if command == "Stop":
            raise ReolinkConnectionError("baichuan header: deadbeefcafe")

    host.set_ptz_command = AsyncMock(side_effect=move_then_fail_stop)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    monkeypatch.setattr("reolink_mcp.tools.control.asyncio.sleep", AsyncMock())

    result = await ptz_move("front_door", _fake_ctx(manager), direction="left")

    assert "Stop" in result["note"]
    assert "deadbeefcafe" not in str(result)


# ---------------------------------------------------------------------------
# ptz_position (D-11, Pattern 5)
# ---------------------------------------------------------------------------


async def test_ptz_position_forces_repoll_and_returns_pan_tilt_zoom(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_pan_tilt_capable(
        host, extra_supported={"zoom": True}, pan=105, tilt=195
    )
    host.get_zoom = lambda channel: 15
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await ptz_position("front_door", _fake_ctx(manager))

    host.baichuan.get_ptz_position.assert_awaited_once_with(0)
    assert result == {
        "camera": "front_door",
        "pan": 105,
        "tilt": 195,
        "zoom": 15,
        "at_preset": None,
    }


async def test_ptz_position_at_preset_matches_within_tolerance(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_pan_tilt_capable(host, pan=105, tilt=195)
    host.ptz_presets = lambda channel: {"driveway": 1}
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    handle = await manager.get("front_door")
    handle.preset_positions = {1: (100, 200)}

    result = await ptz_position("front_door", _fake_ctx(manager))

    assert result["at_preset"] == "driveway"


async def test_ptz_position_at_preset_none_when_outside_tolerance(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_pan_tilt_capable(host, pan=500, tilt=500)
    host.ptz_presets = lambda channel: {"driveway": 1}
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    handle = await manager.get("front_door")
    handle.preset_positions = {1: (100, 200)}

    result = await ptz_position("front_door", _fake_ctx(manager))

    assert result["at_preset"] is None


async def test_ptz_position_zoom_field_is_unsupported_when_zoom_not_gated(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_pan_tilt_capable(host, extra_supported={"zoom": False}, pan=1, tilt=2)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await ptz_position("front_door", _fake_ctx(manager))

    assert result["zoom"] == "unsupported"


async def test_ptz_position_zoom_read_failure_degrades_to_unavailable(
    mock_host_factory, camera_config_factory, manager_factory
):
    """WR-02: a gated-supported but unpopulated zoom read degrades to
    "unavailable" — the pan/tilt answer is already in hand, and the raw
    library text must never escape to the client."""
    host = mock_host_factory()
    _configure_pan_tilt_capable(host, extra_supported={"zoom": True}, pan=1, tilt=2)

    def failing_get_zoom(channel):
        raise InvalidParameterError("get_zoom: no ZoomFocus data for channel 0")

    host.get_zoom = failing_get_zoom
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await ptz_position("front_door", _fake_ctx(manager))

    assert result["zoom"] == "unavailable"
    assert result["pan"] == 1
    assert result["tilt"] == 2


async def test_ptz_position_gate_failure_refuses_without_awaiting(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"pan_tilt": False})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_position("front_door", _fake_ctx(manager))

    assert refusal_message("front_door", "pan_tilt") in str(exc_info.value)
    host.baichuan.get_ptz_position.assert_not_awaited()


# ---------------------------------------------------------------------------
# ptz_guard (CTRL-09, D-10, D-14, Pitfall 7)
# ---------------------------------------------------------------------------


async def test_ptz_guard_set_calls_set_ptz_guard_with_setpos(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_guard_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await ptz_guard("front_door", _fake_ctx(manager), action="set")

    host.set_ptz_guard.assert_awaited_once_with(0, command="setPos")
    host.send_setting.assert_not_awaited()


async def test_ptz_guard_goto_calls_set_ptz_guard_with_topos_and_repolls_position(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_guard_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await ptz_guard("front_door", _fake_ctx(manager), action="goto")

    host.set_ptz_guard.assert_awaited_once_with(0, command="toPos")
    host.baichuan.get_ptz_position.assert_awaited_once_with(0)


async def test_ptz_guard_enable_uses_send_setting_bypass_avoiding_position_resave(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_guard_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await ptz_guard("front_door", _fake_ctx(manager), action="enable")

    host.send_setting.assert_awaited_once_with(
        [
            {
                "cmd": "SetPtzGuard",
                "action": 0,
                "param": {"PtzGuard": {"channel": 0, "benable": 1}},
            }
        ]
    )
    host.set_ptz_guard.assert_not_awaited()
    # Pitfall 7 regression guard: the hand-built body must never resave the
    # current physical position as the guard point.
    body = host.send_setting.await_args.args[0]
    serialized = str(body)
    assert "cmdStr" not in serialized
    assert "bSaveCurrentPos" not in serialized


async def test_ptz_guard_disable_uses_send_setting_bypass(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_guard_capable(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await ptz_guard("front_door", _fake_ctx(manager), action="disable")

    host.send_setting.assert_awaited_once_with(
        [
            {
                "cmd": "SetPtzGuard",
                "action": 0,
                "param": {"PtzGuard": {"channel": 0, "benable": 0}},
            }
        ]
    )
    host.set_ptz_guard.assert_not_awaited()
    body = host.send_setting.await_args.args[0]
    serialized = str(body)
    assert "cmdStr" not in serialized
    assert "bSaveCurrentPos" not in serialized


async def test_ptz_guard_gate_failure_refuses_without_any_host_call(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"ptz_guard": False})
    host.set_ptz_guard = AsyncMock()
    host.send_setting = AsyncMock()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_guard("front_door", _fake_ctx(manager), action="set")

    assert refusal_message("front_door", "ptz_guard") in str(exc_info.value)
    host.set_ptz_guard.assert_not_awaited()
    host.send_setting.assert_not_awaited()
    host.baichuan.get_ptz_position.assert_not_awaited()


@pytest.mark.parametrize("action", ["set", "goto", "enable", "disable"])
async def test_ptz_guard_success_returns_read_back_dict(
    action, mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_guard_capable(host, enabled=True, return_time_s=45)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await ptz_guard("front_door", _fake_ctx(manager), action=action)

    assert result == {
        "camera": "front_door",
        "ptz_guard": {"enabled": True, "return_time_s": 45},
    }


async def test_ptz_guard_host_error_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_ptz_guard_capable(host)
    host.set_ptz_guard = AsyncMock(
        side_effect=InvalidParameterError("set_ptz_guard: invalid channel")
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await ptz_guard("front_door", _fake_ctx(manager), action="set")

    msg = str(exc_info.value)
    assert "rejected" in msg
    assert "set_ptz_guard:" not in msg  # func_name prefix must be stripped


# ---------------------------------------------------------------------------
# Registration + RMCP_READ_ONLY (SAFE-02, D-13)
# ---------------------------------------------------------------------------


async def test_register_all_not_read_only_registers_seventeen_tools():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()

    assert len(tools) == 17
    names = {t.name for t in tools}
    assert {
        "set_siren",
        "set_audio_alarm",
        "set_spotlight",
        "set_ir_lights",
        "set_white_led",
        "set_zoom",
        "list_presets",
        "ptz_move_to_preset",
        "ptz_move",
        "ptz_position",
        "ptz_guard",
    } <= names


async def test_register_all_read_only_registers_six_tools_no_control_tools():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=True)

    tools = await test_mcp.list_tools()

    assert len(tools) == 6
    names = {t.name for t in tools}
    assert not {
        "set_siren",
        "set_audio_alarm",
        "set_spotlight",
        "set_ir_lights",
        "set_white_led",
        "set_zoom",
        "list_presets",
        "ptz_move_to_preset",
        "ptz_move",
        "ptz_position",
        "ptz_guard",
    } & names


async def test_register_all_exact_tool_name_sets_for_both_modes():
    """SAFE-02 hard regression guard: the registered tool-NAME sets must
    equal the literal full/observe-only sets exactly — a single missed
    `if not read_only:` indentation mistake would be caught here even if
    the plain tool-count assertions above happened to still pass."""
    full_mcp = FastMCP("probe-full")
    register_all(full_mcp, read_only=False)
    full_names = {t.name for t in await full_mcp.list_tools()}

    read_only_mcp = FastMCP("probe-read-only")
    register_all(read_only_mcp, read_only=True)
    read_only_names = {t.name for t in await read_only_mcp.list_tools()}

    assert full_names == _ALL_SEVENTEEN_TOOL_NAMES
    assert read_only_names == _SIX_OBSERVE_TOOL_NAMES


async def test_full_registry_annotation_completeness_and_d13_matrix():
    """SAFE-01 hard regression guard: every one of the 17 registered tools
    (not a spot-check subset) must carry explicit, non-None
    readOnlyHint/destructiveHint/idempotentHint values, plus the D-13
    matrix invariants (destructiveHint True on set_siren only; readOnlyHint
    True for the 6 observe tools, False for the 11 control tools)."""
    test_mcp = FastMCP("probe-completeness")
    register_all(test_mcp, read_only=False)
    tools = await test_mcp.list_tools()

    assert {t.name for t in tools} == _ALL_SEVENTEEN_TOOL_NAMES

    for tool in tools:
        assert tool.annotations is not None, f"{tool.name} missing annotations"
        assert tool.annotations.readOnlyHint is not None, tool.name
        assert tool.annotations.destructiveHint is not None, tool.name
        assert tool.annotations.idempotentHint is not None, tool.name

    for tool in tools:
        if tool.name == "set_siren":
            assert tool.annotations.destructiveHint is True, tool.name
        else:
            assert tool.annotations.destructiveHint is False, tool.name

        if tool.name in _SIX_OBSERVE_TOOL_NAMES:
            assert tool.annotations.readOnlyHint is True, tool.name
        else:
            assert tool.annotations.readOnlyHint is False, tool.name


async def test_observe_tools_carry_full_d13_annotation_matrix():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()
    observe_names = {
        "list_cameras",
        "get_device_info",
        "get_capabilities",
        "get_states",
        "get_recent_events",
        "get_snapshot",
    }
    observe_tools = [t for t in tools if t.name in observe_names]

    assert len(observe_tools) == 6
    for tool in observe_tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True


async def test_set_siren_registered_with_destructive_hint_true():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == "set_siren")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is False


@pytest.mark.parametrize(
    "tool_name",
    ["set_audio_alarm", "set_spotlight", "set_ir_lights", "set_white_led", "ptz_guard"],
)
async def test_low_friction_control_tools_registered_with_destructive_hint_false(
    tool_name,
):
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == tool_name)

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True


async def test_set_zoom_registered_with_idempotent_hint_false():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == "set_zoom")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is False


async def test_ptz_move_registered_with_idempotent_hint_false():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == "ptz_move")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is False


@pytest.mark.parametrize(
    "tool_name", ["list_presets", "ptz_move_to_preset", "ptz_position"]
)
async def test_ptz_tools_registered_with_control_annotations_despite_being_getters(
    tool_name,
):
    # D-13/RESEARCH.md Pattern 5b design note: list_presets/ptz_position are
    # pure getters but CONTEXT.md's Phase Boundary explicitly lists all nine
    # as "control tools" — read-only mode strips them too.
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == tool_name)

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
