"""Shared pytest fixtures for reolink-mcp tests."""

from pathlib import Path
from unittest.mock import AsyncMock, create_autospec

import pytest
from pydantic import SecretStr
from reolink_aio.api import Host
from reolink_aio.exceptions import ReolinkConnectionError

from reolink_mcp.config import CameraConfig
from reolink_mcp.manager import CameraManager


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point reolink_mcp.config.CONFIG_PATH at a fresh path under tmp_path.

    Also sets RMCP_CONFIG_FILE for documentation/consistency, but the load-
    bearing mechanism is the direct monkeypatch.setattr on the module-level
    CONFIG_PATH — module-level constants are computed once at import time,
    so re-triggering resolve_config_path() via the env var alone would be
    fragile across test runs (see 01-01-PLAN.md Task 2).

    Returns the path the test should write its YAML config to (the file
    itself is NOT created here — tests write whatever content, or none,
    that their scenario requires).
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("RMCP_CONFIG_FILE", str(config_path))
    monkeypatch.setattr("reolink_mcp.config.CONFIG_PATH", config_path)
    return config_path


@pytest.fixture
def camera_config_factory():
    """Returns a factory building a `CameraConfig` with sane defaults."""

    def make_camera_config(
        host: str = "192.168.1.10",
        username: str = "admin",
        password: str = "secret",
    ) -> CameraConfig:
        return CameraConfig(host=host, username=username, password=SecretStr(password))

    return make_camera_config


@pytest.fixture
def mock_host_factory():
    """Returns a factory building a `create_autospec`'d `Host` (RESEARCH.md's
    verified mock pattern — prevents mock drift via AttributeError on typo'd
    method names, PITFALLS.md #7).

    `fail_with`, if given, makes `get_host_data()` raise that exception.
    Otherwise `online=False` defaults to a generic `ReolinkConnectionError`;
    `online=True` (default) succeeds with no return value.
    """

    def make_mock_host(online: bool = True, fail_with: Exception | None = None) -> Host:
        mock = create_autospec(Host, instance=True)
        if fail_with is not None:
            mock.get_host_data = AsyncMock(side_effect=fail_with)
        elif not online:
            mock.get_host_data = AsyncMock(
                side_effect=ReolinkConnectionError("offline")
            )
        else:
            mock.get_host_data = AsyncMock(return_value=None)
        mock.model = "RLC-810A"
        mock.host = "192.168.1.44"
        mock.logout = AsyncMock()
        return mock

    return make_mock_host


@pytest.fixture
def manager_factory(monkeypatch: pytest.MonkeyPatch):
    """Returns a factory building a `CameraManager` whose internal `Host(...)`
    construction is monkeypatched to always resolve to the given mock host
    (RESEARCH.md's verified mock-seam pattern)."""

    def make_manager(
        cameras: dict[str, CameraConfig], mock_host: Host
    ) -> CameraManager:
        monkeypatch.setattr("reolink_mcp.manager.Host", lambda **kwargs: mock_host)
        return CameraManager(cameras)

    return make_manager
