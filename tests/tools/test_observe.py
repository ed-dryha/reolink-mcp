"""Tests for `list_cameras`: parallel probe, partial success, per-row
name/status/model/host content, and read-only annotation registration
(D-05, D-06, D-07, D-08).

At least one test drives the tool call through the real MCP protocol path
(`mcp.shared.memory.create_connected_server_and_client_session` +
`session.call_tool(...)`), not just by calling the Python function directly
— the fast in-memory integration pattern `01-RESEARCH.md` names for exactly
this purpose.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from PIL import Image as PILImage
from reolink_aio.exceptions import (
    CredentialsInvalidError,
    LoginError,
    ReolinkConnectionError,
)

from reolink_mcp.errors import CameraError, UnknownCameraError, classify_reolink_error
from reolink_mcp.manager import CameraManager
from reolink_mcp.tools import register_all
from reolink_mcp.tools.observe import get_capabilities, get_device_info, get_snapshot


@dataclass
class _TestAppContext:
    """Minimal stand-in for server.py's AppContext — just the manager field
    list_cameras actually reads via ctx.request_context.lifespan_context."""

    manager: CameraManager


def _build_test_mcp(manager: CameraManager) -> FastMCP:
    """A FastMCP instance wired to a test-controlled manager. server.py's
    real lifespan calls load_settings(), which needs a config file on disk
    — too heavy for a unit test, so tests supply their own trivial lifespan
    yielding a manager built directly against mocks."""

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[_TestAppContext]:
        yield _TestAppContext(manager=manager)

    test_mcp = FastMCP("test-reolink-mcp", lifespan=lifespan)
    register_all(test_mcp)
    return test_mcp


def _manager_with_per_camera_hosts(cameras, hosts_by_camera_host, monkeypatch):
    """Build a CameraManager whose Host(...) construction resolves to a
    different mock per configured camera host/IP (mirrors
    test_manager.py::test_close_all_is_exception_tolerant's pattern —
    conftest.py's manager_factory only supports one uniform mock across all
    cameras, insufficient for a mixed online/offline fleet)."""
    monkeypatch.setattr(
        "reolink_mcp.manager.Host",
        lambda **kwargs: hosts_by_camera_host[kwargs["host"]],
    )
    return CameraManager(cameras)


async def test_list_cameras_two_online_returns_full_rows(
    mock_host_factory, camera_config_factory, monkeypatch
):
    front = mock_host_factory()
    front.model = "RLC-810A"
    garage = mock_host_factory()
    garage.model = "RLC-820A"
    cameras = {
        "front_door": camera_config_factory(host="192.168.1.10"),
        "garage": camera_config_factory(host="192.168.1.11"),
    }
    manager = _manager_with_per_camera_hosts(
        cameras, {"192.168.1.10": front, "192.168.1.11": garage}, monkeypatch
    )
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("list_cameras", {})

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    rows = {row["name"]: row for row in payload["cameras"]}
    assert set(rows) == {"front_door", "garage"}
    assert rows["front_door"] == {
        "name": "front_door",
        "status": "connected",
        "model": "RLC-810A",
        "host": "192.168.1.10",
    }
    assert rows["garage"] == {
        "name": "garage",
        "status": "connected",
        "model": "RLC-820A",
        "host": "192.168.1.11",
    }


async def test_list_cameras_partial_failure_reuses_curated_message(
    mock_host_factory, camera_config_factory, monkeypatch
):
    connect_error = ReolinkConnectionError("refused")
    garage = mock_host_factory(fail_with=connect_error)
    front = mock_host_factory()
    cameras = {
        "front_door": camera_config_factory(host="192.168.1.10"),
        "garage": camera_config_factory(host="192.168.1.11"),
    }
    manager = _manager_with_per_camera_hosts(
        cameras, {"192.168.1.10": front, "192.168.1.11": garage}, monkeypatch
    )
    # This is the exact regression guard from 01-03-PLAN.md's interfaces
    # section: _probe must reuse manager.get()'s already-curated message
    # verbatim, not re-run classify_reolink_error against the CameraError
    # instance (which would silently collapse to the generic fallback).
    expected_message = classify_reolink_error(connect_error, "garage", "192.168.1.11")
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("list_cameras", {})

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    rows = {row["name"]: row for row in payload["cameras"]}
    assert set(rows) == {"front_door", "garage"}
    assert rows["front_door"]["status"] == "connected"
    assert rows["garage"]["status"] == expected_message
    assert rows["garage"]["model"] is None
    assert rows["garage"]["host"] == "192.168.1.11"


async def test_list_cameras_probes_concurrently_not_serially(
    mock_host_factory, camera_config_factory, monkeypatch
):
    delay = 0.1
    camera_hosts = {
        "cam_a": "192.168.1.10",
        "cam_b": "192.168.1.11",
        "cam_c": "192.168.1.12",
    }
    hosts_by_ip = {}
    for ip in camera_hosts.values():
        host = mock_host_factory()

        async def slow_get_host_data(*args, **kwargs):
            await asyncio.sleep(delay)

        host.get_host_data = AsyncMock(side_effect=slow_get_host_data)
        hosts_by_ip[ip] = host

    cameras = {
        name: camera_config_factory(host=ip) for name, ip in camera_hosts.items()
    }
    manager = _manager_with_per_camera_hosts(cameras, hosts_by_ip, monkeypatch)
    test_mcp = _build_test_mcp(manager)

    start = time.monotonic()
    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("list_cameras", {})
    elapsed = time.monotonic() - start

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert len(payload["cameras"]) == 3
    # Parallel (asyncio.gather): elapsed close to one camera's delay.
    # Serial would be >= 3 * delay (0.3s); comfortably assert well under
    # that, and comfortably above a single delay (0.1s) plus overhead.
    assert elapsed < delay * 2


async def test_list_cameras_registered_with_read_only_hint():
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == "list_cameras")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# get_snapshot (Plan 01-04) — sub-then-main fallback, unconditional
# downscale, image + caption return, curated error translation.
#
# Most cases call `get_snapshot` directly (not through the protocol layer)
# with a minimal `SimpleNamespace`-based fake `Context` — this surfaces
# raised exceptions with their real type (CameraError/UnknownCameraError)
# for precise assertions, matching the plan's <behavior> wording ("the tool
# raises..."). One case (the downscale test) drives the call through the
# real MCP protocol path instead, proving the `Image` helper's
# base64/ImageContent conversion actually works end-to-end — this is also
# where the `structured_output=False` registration fix (see Deviations)
# gets exercised for real.
# ---------------------------------------------------------------------------


def _fake_ctx(manager: CameraManager) -> SimpleNamespace:
    """Minimal stand-in for a FastMCP `Context`, exposing only the nested
    attribute path `get_snapshot` actually reads:
    `ctx.request_context.lifespan_context.manager`."""
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(manager=manager)
        )
    )


def _make_jpeg_bytes(width: int, height: int) -> bytes:
    """Build a synthetic solid-color JPEG in memory — no binary fixture
    files committed to the repo (per the plan's explicit instruction)."""
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(128, 128, 128)).save(
        buf, format="JPEG"
    )
    return buf.getvalue()


async def test_get_snapshot_sub_stream_success_calls_only_sub(
    mock_host_factory, camera_config_factory, manager_factory
):
    jpeg_bytes = _make_jpeg_bytes(640, 480)
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(return_value=jpeg_bytes)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caption, image = await get_snapshot("front_door", _fake_ctx(manager))

    host.get_snapshot.assert_awaited_once_with(0, stream="sub")
    assert "front_door" in caption
    assert isinstance(image.data, bytes)


async def test_get_snapshot_falls_back_to_main_when_sub_returns_none(
    mock_host_factory, camera_config_factory, manager_factory
):
    jpeg_bytes = _make_jpeg_bytes(640, 480)

    async def snapshot_side_effect(channel, stream=None):
        if stream == "sub":
            return None
        return jpeg_bytes

    host = mock_host_factory()
    host.get_snapshot = AsyncMock(side_effect=snapshot_side_effect)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caption, image = await get_snapshot("front_door", _fake_ctx(manager))

    assert host.get_snapshot.await_count == 2
    calls = host.get_snapshot.await_args_list
    assert calls[0] == call(0, stream="sub")
    assert calls[1] == call(0, stream="main")
    assert isinstance(image.data, bytes)
    assert "front_door" in caption


async def test_get_snapshot_both_streams_none_raises_privacy_mode_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(return_value=None)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(CameraError) as exc_info:
        await get_snapshot("front_door", _fake_ctx(manager))

    message = str(exc_info.value)
    assert "privacy mode" in message or "no image" in message
    assert host.get_snapshot.await_count == 2


async def test_get_snapshot_downscales_oversized_image_via_real_protocol(
    mock_host_factory, camera_config_factory, manager_factory
):
    """Drives the call through the real MCP protocol path (like 01-03's
    list_cameras tests) — proves the `Image` helper's base64/ImageContent
    conversion actually works end-to-end, not just that raw bytes are
    correctly sized."""
    jpeg_bytes = _make_jpeg_bytes(4000, 3000)
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(return_value=jpeg_bytes)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("get_snapshot", {"camera": "front_door"})

    assert result.isError is False
    assert len(result.content) == 2
    text_block, image_block = result.content
    assert text_block.type == "text"
    assert "1280x960" in text_block.text
    assert image_block.type == "image"
    assert image_block.mimeType == "image/jpeg"
    decoded = base64.b64decode(image_block.data)
    with PILImage.open(io.BytesIO(decoded)) as decoded_image:
        assert decoded_image.size[0] <= 1280
        assert decoded_image.size[1] <= 1280
        assert decoded_image.size == (1280, 960)


async def test_get_snapshot_does_not_upscale_small_image(
    mock_host_factory, camera_config_factory, manager_factory
):
    jpeg_bytes = _make_jpeg_bytes(640, 480)
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(return_value=jpeg_bytes)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caption, image = await get_snapshot("front_door", _fake_ctx(manager))

    with PILImage.open(io.BytesIO(image.data)) as decoded_image:
        assert decoded_image.size == (640, 480)
    assert "640x480" in caption


async def test_get_snapshot_caption_contains_camera_name_and_iso_timestamp(
    mock_host_factory, camera_config_factory, manager_factory
):
    jpeg_bytes = _make_jpeg_bytes(640, 480)
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(return_value=jpeg_bytes)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caption, _image = await get_snapshot("front_door", _fake_ctx(manager))

    parts = caption.split(" — ")
    assert parts[0] == "front_door"
    assert parts[1].startswith("captured ")
    timestamp_str = parts[1].removeprefix("captured ")
    datetime.fromisoformat(timestamp_str)


async def test_get_snapshot_unknown_camera_raises_unknown_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(UnknownCameraError) as exc_info:
        await get_snapshot("garage", _fake_ctx(manager))

    message = str(exc_info.value)
    assert "garage" in message
    assert "front_door" in message


async def test_get_snapshot_non_reolink_exception_translated_to_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    raw_exc = ConnectionResetError(
        "raw socket reset mid-session — must never reach the tool response"
    )
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(side_effect=raw_exc)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)
    expected_message = classify_reolink_error(raw_exc, "front_door", "192.168.1.10")

    with pytest.raises(CameraError) as exc_info:
        await get_snapshot("front_door", _fake_ctx(manager))

    assert str(exc_info.value) == expected_message
    assert "raw socket reset" not in str(exc_info.value)
    host.get_snapshot.assert_awaited_once_with(0, stream="sub")


# ---------------------------------------------------------------------------
# get_snapshot (Plan 01-05, CR-02 / G2) — the sub/main stream attempts must
# classify their own ReolinkError failures through classify_reolink_error's
# curated taxonomy instead of collapsing into the generic "privacy mode"
# fallback, and must never retry main after an auth/session-class failure
# on sub.
# ---------------------------------------------------------------------------


async def test_get_snapshot_sub_raises_reolink_error_falls_back_to_main(
    mock_host_factory, camera_config_factory, manager_factory
):
    jpeg_bytes = _make_jpeg_bytes(640, 480)

    async def snapshot_side_effect(channel, stream=None):
        if stream == "sub":
            raise ReolinkConnectionError("sub refused")
        return jpeg_bytes

    host = mock_host_factory()
    host.get_snapshot = AsyncMock(side_effect=snapshot_side_effect)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caption, image = await get_snapshot("front_door", _fake_ctx(manager))

    assert host.get_snapshot.await_count == 2
    calls = host.get_snapshot.await_args_list
    assert calls[0] == call(0, stream="sub")
    assert calls[1] == call(0, stream="main")
    assert isinstance(image.data, bytes)
    assert "front_door" in caption


async def test_get_snapshot_both_streams_raise_reolink_error_classifies_last_exc(
    mock_host_factory, camera_config_factory, manager_factory
):
    main_exc = ReolinkConnectionError("refused")

    async def snapshot_side_effect(channel, stream=None):
        if stream == "sub":
            raise ReolinkConnectionError("refused")
        raise main_exc

    host = mock_host_factory()
    host.get_snapshot = AsyncMock(side_effect=snapshot_side_effect)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)
    expected_message = classify_reolink_error(main_exc, "front_door", "192.168.1.10")

    with pytest.raises(CameraError) as exc_info:
        await get_snapshot("front_door", _fake_ctx(manager))

    assert str(exc_info.value) == expected_message
    assert "privacy mode" not in str(exc_info.value)
    assert "mid-reboot" not in str(exc_info.value)
    assert host.get_snapshot.await_count == 2


async def test_get_snapshot_sub_credentials_invalid_raises_without_retrying_main(
    mock_host_factory, camera_config_factory, manager_factory
):
    exc = CredentialsInvalidError("invalid user")
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(side_effect=exc)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)
    expected_message = classify_reolink_error(exc, "front_door", "192.168.1.10")

    with pytest.raises(CameraError) as exc_info:
        await get_snapshot("front_door", _fake_ctx(manager))

    assert str(exc_info.value) == expected_message
    host.get_snapshot.assert_awaited_once_with(0, stream="sub")


async def test_get_snapshot_sub_session_limit_raises_without_retrying_main(
    mock_host_factory, camera_config_factory, manager_factory
):
    exc = LoginError("login failed: {'rspCode': -5, 'detail': 'max session'}")
    host = mock_host_factory()
    host.get_snapshot = AsyncMock(side_effect=exc)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)
    expected_message = classify_reolink_error(exc, "front_door", "192.168.1.10")

    with pytest.raises(CameraError) as exc_info:
        await get_snapshot("front_door", _fake_ctx(manager))

    assert str(exc_info.value) == expected_message
    host.get_snapshot.assert_awaited_once_with(0, stream="sub")


# ---------------------------------------------------------------------------
# get_device_info (Plan 02-01) — pure read over the already-connected Host,
# zero additional awaited calls beyond manager.get()'s own connect (Pattern
# 1). full=True adds is_nvr/is_battery/num_channels (D-02).
# ---------------------------------------------------------------------------


def _configure_device_info_mock(host) -> None:
    """Set every Host attribute get_device_info reads, mirroring a real
    GetDevInfo response (RESEARCH.md Pattern 1's accessor table)."""
    host.model = "RLC-810A"
    host.sw_version = "v3.1.0.123"
    host.hardware_version = "IPC_3816M"
    host.mac_address = "AA:BB:CC:DD:EE:FF"
    host.manufacturer = "Reolink"
    host.is_nvr = False
    host.is_battery = False
    host.num_channels = 1
    host.item_number = Mock(return_value="P437")
    host.serial = Mock(return_value="00000000ABCDEF")


async def test_get_device_info_returns_mapped_fields(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_device_info_mock(host)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)

    info = await get_device_info("front_door", _fake_ctx(manager))

    assert info["camera"] == "front_door"
    assert info["model"] == "RLC-810A"
    assert info["item_number"] == "P437"
    assert info["firmware_version"] == "v3.1.0.123"
    assert info["hardware_version"] == "IPC_3816M"
    assert info["serial"] == "00000000ABCDEF"
    assert info["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert info["manufacturer"] == "Reolink"
    assert info["configured_host"] == "192.168.1.10"
    assert info["channel"] == 0


async def test_get_device_info_makes_zero_additional_awaited_calls(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_device_info_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await get_device_info("front_door", _fake_ctx(manager))

    # Proves the "zero extra I/O" claim: get_host_data was awaited exactly
    # once, by manager.get()'s own connect — get_device_info issues no
    # additional awaited host calls (RESEARCH.md Pattern 1).
    assert host.get_host_data.await_count == 1


async def test_get_device_info_full_true_adds_hardware_flags(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_device_info_mock(host)
    host.is_nvr = True
    host.is_battery = True
    host.num_channels = 8
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    info = await get_device_info("front_door", _fake_ctx(manager), full=True)

    assert info["is_nvr"] is True
    assert info["is_battery"] is True
    assert info["num_channels"] == 8


async def test_get_device_info_unknown_camera_raises_unknown_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    with pytest.raises(UnknownCameraError) as exc_info:
        await get_device_info("garage", _fake_ctx(manager))

    message = str(exc_info.value)
    assert "garage" in message
    assert "front_door" in message


# ---------------------------------------------------------------------------
# get_capabilities (Plan 02-01) — CAPABILITY_MAP-derived reads via
# capabilities.gate(), plus dynamic ai_detection_types. full=True adds
# raw_capabilities/siren_schedule (D-02, D-11).
#
# Mock host.supported is ALWAYS a per-capability-string dict lookup (never a
# single blanket bool) — a blanket mock cannot catch the siren/siren_play or
# ptz/ptz_presets string-mismatch bug class (RESEARCH.md Pitfalls 3/4).
# ---------------------------------------------------------------------------


def _per_string_supported(mapping: dict[str, bool]):
    return lambda channel, cap: mapping.get(cap, False)


async def test_get_capabilities_maps_curated_keys_and_ai_types(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported(
        {
            "zoom": True,
            "ir_lights": True,
            "floodLight": True,
            "siren_play": True,
            "ptz_presets": False,
            "dayNight": True,
            "motion_detection": True,
        }
    )
    host.ai_supported_types = Mock(return_value=["people", "vehicle"])
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caps = await get_capabilities("front_door", _fake_ctx(manager))

    assert caps["camera"] == "front_door"
    assert caps["zoom"] is True
    assert caps["ir_lights"] is True
    assert caps["white_led"] is True
    assert caps["siren"] is True
    assert caps["ptz_presets"] is False
    assert caps["day_night"] is True
    assert caps["motion_detection"] is True
    assert caps["ai_detection_types"] == ["people", "vehicle"]


async def test_get_capabilities_full_true_adds_raw_capabilities_and_siren_schedule(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    # "siren_schedule" (full=true only) uses the raw "siren" capability
    # string — distinct from the curated "siren" key's "siren_play" check
    # (RESEARCH.md's explicit recommendation for this exact ambiguity).
    host.supported = _per_string_supported({"siren_play": True, "siren": False})
    host.ai_supported_types = Mock(return_value=[])
    host.capabilities = {0: {"zoom", "ir_lights", "floodLight"}}
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caps = await get_capabilities("front_door", _fake_ctx(manager), full=True)

    assert caps["raw_capabilities"] == ["floodLight", "ir_lights", "zoom"]
    assert caps["siren_schedule"] is False


async def test_get_capabilities_p320_like_all_hardware_absent(
    mock_host_factory, camera_config_factory, manager_factory
):
    """P320 tri-state contrast (RESEARCH.md HDWR-01 section): fixed-lens, no
    siren/spotlight/PTZ, but IR/day-night/motion are still supported — the
    strongest live-hardware signal for the tri-state "unsupported" path."""
    host = mock_host_factory()
    host.supported = _per_string_supported(
        {
            "floodLight": False,
            "siren_play": False,
            "zoom": False,
            "ptz_presets": False,
            "ir_lights": True,
            "dayNight": True,
            "motion_detection": True,
        }
    )
    host.ai_supported_types = Mock(return_value=[])
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caps = await get_capabilities("front_door", _fake_ctx(manager))

    assert caps["white_led"] is False
    assert caps["siren"] is False
    assert caps["zoom"] is False
    assert caps["ptz_presets"] is False
    assert caps["ir_lights"] is True
    assert caps["day_night"] is True
    assert caps["motion_detection"] is True


# ---------------------------------------------------------------------------
# get_device_info / get_capabilities — registration (readOnlyHint=True) and
# structured-output verification via the real MCP protocol path (proves
# dict[str, Any] actually populates structuredContent, RESEARCH.md
# Pattern 6 — a regression list_cameras's bare-dict annotation can't catch).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["get_device_info", "get_capabilities"])
async def test_new_observe_tools_registered_with_read_only_hint(tool_name):
    test_mcp = FastMCP("probe-annotations")
    register_all(test_mcp)

    tools = await test_mcp.list_tools()
    tool = next(t for t in tools if t.name == tool_name)

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True


async def test_get_device_info_populates_structured_content(
    mock_host_factory, camera_config_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_device_info_mock(host)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = _manager_with_per_camera_hosts(
        cameras, {"192.168.1.10": host}, monkeypatch
    )
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("get_device_info", {"camera": "front_door"})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["camera"] == "front_door"


async def test_get_capabilities_populates_structured_content(
    mock_host_factory, camera_config_factory, monkeypatch
):
    host = mock_host_factory()
    host.supported = _per_string_supported({"zoom": True})
    host.ai_supported_types = Mock(return_value=[])
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = _manager_with_per_camera_hosts(
        cameras, {"192.168.1.10": host}, monkeypatch
    )
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("get_capabilities", {"camera": "front_door"})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["camera"] == "front_door"
