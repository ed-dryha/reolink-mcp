"""Shared pytest fixtures for reolink-mcp tests."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, create_autospec

import pytest
from pydantic import SecretStr
from reolink_aio.api import Host
from reolink_aio.baichuan import Baichuan
from reolink_aio.exceptions import ReolinkConnectionError

# Collection-time hermeticity stub (BLOCKER 1, 03-01-PLAN.md Task 1):
# `server.py`'s module-scope `settings = load_settings()` makes any bare
# `import reolink_mcp.server` config-dependent — including
# `tests/test_server.py`'s own module-top `import reolink_mcp.server`,
# which pytest executes during COLLECTION, before any fixture (including
# `tmp_config` below) has run. On a host with no
# `~/.config/reolink-mcp/config.yaml` and no `RMCP_CONFIG_FILE` set, that
# collection-time call would raise `SystemExit` and abort the entire test
# run. This block guarantees collection can never depend on the host
# machine's state: it points RMCP_CONFIG_FILE at a throwaway, always-valid
# stub config before any `reolink_mcp` import happens (including this
# file's own imports below). Every actual test still drives its own real
# config via the `tmp_config` fixture, which monkeypatches
# RMCP_CONFIG_FILE/CONFIG_PATH at function scope and fully overrides this
# stub for the duration of that test.
_collection_stub_dir = tempfile.mkdtemp(prefix="reolink-mcp-collection-stub-")
_collection_stub_config = Path(_collection_stub_dir) / "config.yaml"
_collection_stub_config.write_text("cameras: {}\n")
os.environ["RMCP_CONFIG_FILE"] = str(_collection_stub_config)

# The stub YAML alone is not enough: Settings also merges a repo-root `.env`
# (env_file=".env", resolved against cwd — the dev-loop convenience noted in
# `tmp_config` below). On a developer machine whose `.env` holds real
# `RMCP_CAMERAS__<name>__PASSWORD` entries, those merge into the stub's empty
# `cameras: {}` as orphan passwords and `load_settings()` exits with
# "no camera named '<name>' in YAML" — during collection. Trigger the one
# module-scope `load_settings()` (in `reolink_mcp.server`) now, with cwd
# pointed at the stub dir where no `.env` exists; the module cache makes every
# later collection-time import of `reolink_mcp.server` a no-op.
_prev_cwd = os.getcwd()
os.chdir(_collection_stub_dir)
try:
    import reolink_mcp.server  # noqa: F401
finally:
    os.chdir(_prev_cwd)

from reolink_mcp.config import CameraConfig  # noqa: E402
from reolink_mcp.manager import CameraManager  # noqa: E402


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
    # Isolate from the developer's repo-root .env: Settings has the dev-loop
    # convenience env_file=".env" (resolved against cwd), which would merge
    # local camera passwords into the test's YAML as phantom half-configured
    # cameras (password present, host/username missing).
    monkeypatch.chdir(tmp_path)
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
        # create_autospec(Host, instance=True) alone does not expose
        # `.baichuan` at all (confirmed empirically — raises AttributeError):
        # `baichuan` is a plain instance attribute assigned inside
        # `Host.__init__`, never visible to create_autospec's class-level
        # introspection (03-RESEARCH.md Pattern 5). Required before any
        # PTZ-position test (Phase 3 Plan 2) can run.
        mock.baichuan = create_autospec(Baichuan, instance=True)
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
