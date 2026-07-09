"""Shared pytest fixtures for reolink-mcp tests."""

from pathlib import Path

import pytest


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
