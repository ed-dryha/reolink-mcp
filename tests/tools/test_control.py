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

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP
from reolink_aio.exceptions import ReolinkConnectionError

from reolink_mcp.capabilities import refusal_message
from reolink_mcp.errors import CameraError
from reolink_mcp.manager import CameraManager
from reolink_mcp.tools import register_all
from reolink_mcp.tools.control import (
    set_ir_lights,
    set_siren,
    set_spotlight,
    set_white_led,
)


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
# Registration + RMCP_READ_ONLY (SAFE-02, D-13)
# ---------------------------------------------------------------------------


async def test_register_all_not_read_only_registers_ten_tools():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=False)

    tools = await test_mcp.list_tools()

    assert len(tools) == 10
    names = {t.name for t in tools}
    assert {"set_siren", "set_spotlight", "set_ir_lights", "set_white_led"} <= names


async def test_register_all_read_only_registers_six_tools_no_control_tools():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp, read_only=True)

    tools = await test_mcp.list_tools()

    assert len(tools) == 6
    names = {t.name for t in tools}
    assert not {"set_siren", "set_spotlight", "set_ir_lights", "set_white_led"} & names


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
    "tool_name", ["set_spotlight", "set_ir_lights", "set_white_led"]
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
