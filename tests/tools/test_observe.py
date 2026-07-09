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
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from reolink_aio.exceptions import ReolinkConnectionError

from reolink_mcp.errors import classify_reolink_error
from reolink_mcp.manager import CameraManager
from reolink_mcp.tools import register_all


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
