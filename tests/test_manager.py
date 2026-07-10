"""Tests for CameraManager: lazy connect, caching, guaranteed logout.

Six behaviors (CONN-03, CONN-04, CONN-05, D-06):
1. Construction performs zero I/O — no Host is constructed until first get().
2. First get() connects; a second get() for the same name reuses the cache.
3. A connect failure raises CameraError with classify_reolink_error's message.
4. An unknown camera name raises UnknownCameraError listing configured names.
5. close_all() is exception-tolerant — one failing logout never blocks others.
6. Concurrent get() calls for the same camera connect exactly once (lock).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from reolink_aio.exceptions import ReolinkConnectionError

from reolink_mcp.errors import CameraError, UnknownCameraError, classify_reolink_error
from reolink_mcp.manager import CameraManager


async def test_constructor_performs_zero_io(
    mock_host_factory, monkeypatch, camera_config_factory
):
    mock_host = mock_host_factory()
    host_calls = []

    def spy_host(**kwargs):
        host_calls.append(kwargs)
        return mock_host

    monkeypatch.setattr("reolink_mcp.manager.Host", spy_host)
    cameras = {"front_door": camera_config_factory()}

    manager = CameraManager(cameras)

    assert host_calls == []
    mock_host.get_host_data.assert_not_called()
    assert manager.configured_names() == ["front_door"]


async def test_get_caches_handle_after_first_connect(
    manager_factory, mock_host_factory, camera_config_factory
):
    mock_host = mock_host_factory()
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, mock_host)

    handle1 = await manager.get("front_door")
    handle2 = await manager.get("front_door")

    assert mock_host.get_host_data.await_count == 1
    assert handle1 is handle2
    assert handle1.name == "front_door"
    assert handle1.channel == 0
    assert handle1.connected is True
    # Phase 2 Plan 2 (Pitfall 1 guard): connect must NOT set states_polled_at
    # — only get_states/get_recent_events do, on their own first call.
    assert handle1.states_polled_at is None
    # Phase 3 Plan 2 (D-11, Pitfall 6): preset_positions starts empty — only
    # ptz_move_to_preset populates it, never the connect path.
    assert handle1.preset_positions == {}


async def test_get_raises_camera_error_matching_classifier(
    manager_factory, mock_host_factory, camera_config_factory
):
    connect_error = ReolinkConnectionError("refused")
    mock_host = mock_host_factory(fail_with=connect_error)
    cameras = {"garage": camera_config_factory(host="192.168.1.44")}
    manager = manager_factory(cameras, mock_host)

    with pytest.raises(CameraError) as exc_info:
        await manager.get("garage")

    expected = classify_reolink_error(connect_error, "garage", "192.168.1.44")
    assert str(exc_info.value) == expected


async def test_get_unknown_camera_lists_configured_names(
    manager_factory, mock_host_factory, camera_config_factory
):
    mock_host = mock_host_factory()
    cameras = {
        "front_door": camera_config_factory(),
        "garage": camera_config_factory(host="192.168.1.44"),
    }
    manager = manager_factory(cameras, mock_host)

    with pytest.raises(UnknownCameraError, match=r"front_door, garage"):
        await manager.get("nope")


async def test_close_all_is_exception_tolerant(
    monkeypatch, mock_host_factory, camera_config_factory
):
    host_a = mock_host_factory()
    host_b = mock_host_factory()
    host_a.logout = AsyncMock(side_effect=RuntimeError("logout failed"))

    hosts_by_host = {"192.168.1.10": host_a, "192.168.1.11": host_b}
    monkeypatch.setattr(
        "reolink_mcp.manager.Host", lambda **kwargs: hosts_by_host[kwargs["host"]]
    )

    cameras = {
        "front_door": camera_config_factory(host="192.168.1.10"),
        "garage": camera_config_factory(host="192.168.1.11"),
    }
    manager = CameraManager(cameras)
    await manager.get("front_door")
    await manager.get("garage")

    await manager.close_all()

    host_a.logout.assert_awaited_once()
    host_b.logout.assert_awaited_once()


async def test_concurrent_get_calls_connect_exactly_once(
    manager_factory, mock_host_factory, camera_config_factory
):
    call_count = 0

    async def slow_get_host_data(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)

    mock_host = mock_host_factory()
    mock_host.get_host_data = AsyncMock(side_effect=slow_get_host_data)
    cameras = {"front_door": camera_config_factory()}
    manager = manager_factory(cameras, mock_host)

    await asyncio.gather(manager.get("front_door"), manager.get("front_door"))

    assert call_count == 1
