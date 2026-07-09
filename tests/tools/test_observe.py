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
from reolink_mcp.tools.observe import (
    get_capabilities,
    get_device_info,
    get_recent_events,
    get_snapshot,
    get_states,
)


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


def _per_channel_getter(mapping: dict[int | None, str | None]):
    """Per-channel dict-backed getter mirroring reolink-aio's own
    `Host.serial()`/`Host.item_number()` shape (`self._serial.get(channel)`)
    — a real standalone camera only ever populates the `None` key, never a
    numeric-channel key. Mirrors `_per_string_supported`'s per-argument
    mocking discipline (above) so a channel-argument-sensitive bug cannot
    hide behind a blanket `Mock(return_value=...)` (02-VERIFICATION.md
    gap #1)."""
    return lambda channel=None: mapping.get(channel)


def _configure_device_info_mock(host) -> None:
    """Set every Host attribute get_device_info reads, mirroring a real
    GetDevInfo response (RESEARCH.md Pattern 1's accessor table). `serial`/
    `item_number` use the real standalone-camera shape — only the `None` key
    populated — proving get_device_info's `_standalone_channel_fallback`
    across every test in this section, not just the two dedicated
    regression tests below."""
    host.model = "RLC-810A"
    host.sw_version = "v3.1.0.123"
    host.hardware_version = "IPC_3816M"
    host.mac_address = "AA:BB:CC:DD:EE:FF"
    host.manufacturer = "Reolink"
    host.is_nvr = False
    host.is_battery = False
    host.num_channels = 1
    host.item_number = _per_channel_getter({None: "P437", 0: None})
    host.serial = _per_channel_getter({None: "00000000ABCDEF", 0: None})


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


async def test_get_device_info_serial_and_item_number_fall_back_when_standalone_channel_key_missing(  # noqa: E501
    mock_host_factory, camera_config_factory, manager_factory
):
    """02-VERIFICATION.md gap #1 repro: a real standalone camera's
    GetDevInfo response only ever populates the `None`-keyed cache — the
    numeric-channel key (`0`) is never set. `host.serial(0)`/
    `host.item_number(0)` must fall back to the `None` key, exactly like
    `Host.camera_model()`'s own `not self.is_nvr` fallback precedent."""
    host = mock_host_factory()
    _configure_device_info_mock(host)
    host.serial = _per_channel_getter({None: "ABC123SERIAL", 0: None})
    host.item_number = _per_channel_getter({None: "P437-ITEM", 0: None})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    info = await get_device_info("front_door", _fake_ctx(manager))

    assert info["serial"] == "ABC123SERIAL"
    assert info["item_number"] == "P437-ITEM"


async def test_get_device_info_serial_does_not_fall_back_for_nvr_channel(
    mock_host_factory, camera_config_factory, manager_factory
):
    """The standalone-camera fallback must never borrow an NVR parent's
    serial onto a channel that genuinely has none — mirrors
    `Host.camera_model()`'s own `not self.is_nvr` gate exactly."""
    host = mock_host_factory()
    _configure_device_info_mock(host)
    host.is_nvr = True
    host.serial = _per_channel_getter({None: "PARENT_NVR_SERIAL", 0: None})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    info = await get_device_info("front_door", _fake_ctx(manager))

    assert info["serial"] is None


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
    assert caps["ai_detection_types"] == ["person", "vehicle"]


async def test_get_capabilities_full_true_includes_raw_ai_types(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    host.supported = _per_string_supported({})
    host.ai_supported_types = Mock(return_value=["people", "vehicle", "dog_cat"])
    host.capabilities = {0: set()}
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    caps = await get_capabilities("front_door", _fake_ctx(manager), full=True)

    assert caps["ai_detection_types"] == ["person", "vehicle", "pet"]
    assert caps["raw_ai_types"] == ["people", "vehicle", "dog_cat"]


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
# get_states (Plan 02-02, Task 1) — CameraHandle.states_polled_at, the
# mandatory-first-poll guard (Pitfall 1), the narrow curated cmd_list
# (Pattern 3), tri-state capability gating via capabilities.gate() (D-09),
# and polled_at/age_seconds staleness metadata (D-05).
#
# host.supported is ALWAYS a per-capability-string dict lookup (never a
# blanket bool) — matches get_capabilities' own Pitfall 3/4 regression-guard
# discipline.
# ---------------------------------------------------------------------------


def _configure_states_mock(host, *, all_supported: bool = True) -> None:
    """Set every Host attribute get_states reads. `all_supported=False`
    produces a P320-like fixture where day_night/white_led/ir_lights/siren
    are all capability-absent (D-09's tri-state 'unsupported' path)."""
    host.supported = _per_string_supported(
        {
            "dayNight": all_supported,
            "floodLight": all_supported,
            "ir_lights": all_supported,
            "siren_play": all_supported,
        }
    )
    host.get_states = AsyncMock(return_value=None)
    host.daynight_state = Mock(return_value="Black&White")
    host.whiteled_state = Mock(return_value=True)
    host.whiteled_brightness = Mock(return_value=80)
    host.ir_enabled = Mock(return_value=True)
    host.motion_detected = Mock(return_value=True)


async def test_get_states_first_call_forces_poll_despite_refresh_false(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)
    handle = await manager.get("front_door")
    assert handle.states_polled_at is None

    await get_states("front_door", _fake_ctx(manager), refresh=False)

    host.get_states.assert_awaited_once()
    assert handle.states_polled_at is not None


async def test_get_states_second_call_reuses_cache_without_refresh(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await get_states("front_door", _fake_ctx(manager), refresh=False)
    await get_states("front_door", _fake_ctx(manager), refresh=False)

    host.get_states.assert_awaited_once()


async def test_get_states_refresh_true_always_repolls(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await get_states("front_door", _fake_ctx(manager), refresh=False)
    await get_states("front_door", _fake_ctx(manager), refresh=True)

    assert host.get_states.await_count == 2


async def test_get_states_non_full_poll_uses_narrow_cmd_list(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await get_states("front_door", _fake_ctx(manager))

    host.get_states.assert_awaited_once_with(
        cmd_list={
            "GetIsp": [0],
            "GetIrLights": [0],
            "GetWhiteLed": [0],
            "GetAudioAlarm": [0],
        }
    )


async def test_get_states_full_true_passes_cmd_list_none_and_forces_poll(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await get_states("front_door", _fake_ctx(manager), refresh=False)
    await get_states("front_door", _fake_ctx(manager), refresh=False, full=True)

    assert host.get_states.await_count == 2
    calls = host.get_states.await_args_list
    assert calls[1] == call(cmd_list=None)


async def test_get_states_full_true_status_led_unsupported_when_capability_absent(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    host.supported = _per_string_supported(
        {
            "dayNight": True,
            "floodLight": True,
            "ir_lights": True,
            "siren_play": True,
            "status_led": False,
        }
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_states("front_door", _fake_ctx(manager), full=True)

    assert result["status_led"] == "unsupported"


async def test_get_states_full_true_status_led_reports_state_when_supported(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    host.supported = _per_string_supported(
        {
            "dayNight": True,
            "floodLight": True,
            "ir_lights": True,
            "siren_play": True,
            "status_led": True,
        }
    )
    host.status_led_enabled = Mock(return_value=True)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_states("front_door", _fake_ctx(manager), full=True)

    assert result["status_led"] is True


async def test_get_states_returns_curated_fields_when_supported(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host, all_supported=True)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_states("front_door", _fake_ctx(manager))

    assert result["day_night"] == "Black&White"
    assert result["white_led"] == {"on": True, "brightness": 80}
    assert result["ir_lights"] is True
    assert result["siren"] == "supported"
    assert result["motion"] is True


async def test_get_states_returns_unsupported_markers_when_hardware_absent(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host, all_supported=False)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_states("front_door", _fake_ctx(manager))

    assert result["day_night"] == "unsupported"
    assert result["white_led"] == "unsupported"
    assert result["ir_lights"] == "unsupported"
    assert result["siren"] == "unsupported"


async def test_get_states_includes_polled_at_and_age_seconds(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_states("front_door", _fake_ctx(manager))

    datetime.fromisoformat(result["polled_at"])
    assert result["age_seconds"] >= 0


async def test_get_states_poll_failure_raises_curated_camera_error(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_states_mock(host)
    raw_exc = ConnectionResetError("raw socket reset mid-session")
    host.get_states = AsyncMock(side_effect=raw_exc)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = manager_factory(cameras, host)
    expected_message = classify_reolink_error(raw_exc, "front_door", "192.168.1.10")

    with pytest.raises(CameraError) as exc_info:
        await get_states("front_door", _fake_ctx(manager))

    assert str(exc_info.value) == expected_message
    assert "raw socket reset" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_recent_events (Plan 02-02, Task 2) — tri-state AI detection (baseline
# trio + dynamic extras), raw-wire-key-vs-friendly-constant mapping
# (Pitfall 2), plain motion flag, and the exact same refresh/first-poll
# clock as get_states (D-04 — one CameraHandle.states_polled_at, not two).
# ---------------------------------------------------------------------------


def _per_type_ai(mapping: dict[str, bool]):
    """Per-detect-type lookup for host.ai_supported/host.ai_detected — never
    a single blanket bool (mirrors get_capabilities' host.supported
    discipline; catches the exact "person" vs "people" mismatch class,
    Pitfall 2)."""
    return lambda channel, detect_type: mapping.get(detect_type, False)


def _configure_recent_events_mock(host) -> None:
    """Baseline defaults: everything unsupported/no extras/no motion —
    individual tests override the specific attrs they exercise."""
    host.get_states = AsyncMock(return_value=None)
    host.ai_supported = _per_type_ai({})
    host.ai_detected = _per_type_ai({})
    host.ai_supported_types = Mock(return_value=[])
    host.motion_detected = Mock(return_value=False)


async def test_get_recent_events_baseline_trio_tri_state(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    host.ai_supported = _per_type_ai({"person": True, "vehicle": True, "pet": False})
    host.ai_detected = _per_type_ai({"person": True, "vehicle": False})
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_recent_events("front_door", _fake_ctx(manager))

    assert result["person"] == "detected"
    assert result["vehicle"] == "not_detected"
    assert result["pet"] == "unsupported"


async def test_get_recent_events_maps_raw_wire_keys_to_friendly_names(
    mock_host_factory, camera_config_factory, manager_factory
):
    """Pitfall 2 regression guard: the raw-list fixture uses the camera's
    actual wire keys ("people"/"dog_cat"), NOT the friendly constants — a
    friendly-form fixture would not catch a "person" vs "people" bug."""
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    host.ai_supported = _per_type_ai({"person": True, "vehicle": True, "pet": True})
    host.ai_detected = _per_type_ai({"person": True, "vehicle": False, "pet": False})
    host.ai_supported_types = Mock(return_value=["people", "vehicle", "dog_cat"])
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_recent_events("front_door", _fake_ctx(manager))

    assert "person" in result
    assert "vehicle" in result
    assert "pet" in result
    assert "people" not in result
    assert "dog_cat" not in result


async def test_get_recent_events_dynamic_extra_type_appears_with_zero_code_changes(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    host.ai_supported = _per_type_ai({"person": True, "vehicle": True, "pet": True})
    host.ai_detected = _per_type_ai(
        {"person": False, "vehicle": False, "pet": False, "face": True}
    )
    host.ai_supported_types = Mock(
        return_value=["people", "vehicle", "dog_cat", "face"]
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_recent_events("front_door", _fake_ctx(manager))

    assert result["face"] == "detected"


async def test_get_recent_events_includes_motion_flag_independent_of_ai_state(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    host.motion_detected = Mock(return_value=True)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_recent_events("front_door", _fake_ctx(manager))

    assert result["motion"] is True
    assert result["person"] == "unsupported"


async def test_get_recent_events_and_get_states_share_one_poll_clock(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    await get_recent_events("front_door", _fake_ctx(manager))
    await get_states("front_door", _fake_ctx(manager))

    host.get_states.assert_awaited_once()


async def test_get_recent_events_includes_polled_at_and_age_seconds(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_recent_events("front_door", _fake_ctx(manager))

    datetime.fromisoformat(result["polled_at"])
    assert result["age_seconds"] >= 0


async def test_get_recent_events_full_true_includes_raw_ai_types(
    mock_host_factory, camera_config_factory, manager_factory
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    host.ai_supported_types = Mock(
        return_value=["people", "vehicle", "dog_cat", "face"]
    )
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, host)

    result = await get_recent_events("front_door", _fake_ctx(manager), full=True)

    assert result["raw_ai_types"] == ["people", "vehicle", "dog_cat", "face"]


# ---------------------------------------------------------------------------
# get_device_info / get_capabilities / get_states / get_recent_events —
# registration (readOnlyHint=True) and structured-output verification via
# the real MCP protocol path (proves dict[str, Any] actually populates
# structuredContent, RESEARCH.md Pattern 6 — a regression list_cameras's
# bare-dict annotation can't catch).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["get_device_info", "get_capabilities", "get_states", "get_recent_events"],
)
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


async def test_get_states_populates_structured_content(
    mock_host_factory, camera_config_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_states_mock(host)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = _manager_with_per_camera_hosts(
        cameras, {"192.168.1.10": host}, monkeypatch
    )
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool("get_states", {"camera": "front_door"})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["camera"] == "front_door"


async def test_get_recent_events_populates_structured_content(
    mock_host_factory, camera_config_factory, monkeypatch
):
    host = mock_host_factory()
    _configure_recent_events_mock(host)
    cameras = {"front_door": camera_config_factory(host="192.168.1.10")}
    manager = _manager_with_per_camera_hosts(
        cameras, {"192.168.1.10": host}, monkeypatch
    )
    test_mcp = _build_test_mcp(manager)

    async with create_connected_server_and_client_session(test_mcp) as session:
        result = await session.call_tool(
            "get_recent_events", {"camera": "front_door"}
        )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["camera"] == "front_door"
