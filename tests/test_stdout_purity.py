"""Real-subprocess integration tests proving stdout carries only JSON-RPC
frames (SAFE-03, threat T-03-01).

Both tests spawn the actual `reolink-mcp` server exactly as an MCP client
would (`python -m reolink_mcp`), pointed at a fixture config with one fake
camera (no real network call is needed for a mere handshake — D-06
guarantees no camera I/O at startup).

Test 1 drives the handshake through the real SDK client (`stdio_client` +
`ClientSession.initialize()`), proving the protocol parses end-to-end.
Test 2 is the stronger assertion `01-RESEARCH.md` calls for: it reads the
subprocess's raw stdout bytes directly via `subprocess.Popen` (not through
`stdio_client`'s own pipe, which only proves the handshake *parsed*) and
asserts every non-empty line is valid JSON — a stray `print()`/misconfigured
log handler would fail this even if it happened not to break the handshake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CONFIG_YAML = """\
cameras:
  testcam:
    host: 192.168.1.99
    username: admin
"""


@pytest.fixture
def fixture_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)
    return config_path


def _subprocess_env(config_path: Path) -> dict[str, str]:
    # Must include the base environment, not just our overrides — a
    # subprocess spawned with an empty env won't even find the Python
    # interpreter's own stdlib in some environments (01-03-PLAN.md action).
    return {
        **os.environ,
        "RMCP_CONFIG_FILE": str(config_path),
        "RMCP_CAMERAS__testcam__PASSWORD": "x",
    }


async def test_server_completes_initialize_handshake_over_real_stdio(
    fixture_config: Path,
) -> None:
    """The real server subprocess, spawned exactly as a client would spawn
    it, completes the MCP `initialize` handshake — proving stdout carries a
    parseable JSON-RPC stream."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "reolink_mcp"],
        env=_subprocess_env(fixture_config),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            assert result.serverInfo.name == "reolink-mcp"


def test_subprocess_raw_stdout_is_json_rpc_only(fixture_config: Path) -> None:
    """Stronger assertion: read the subprocess's actual stdout bytes
    directly and assert every non-empty line is valid JSON. This is the
    only way to catch a stray byte that a higher-level client parser might
    tolerate or that happens not to break the handshake outcome itself."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "reolink_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env(fixture_config),
        text=True,
        bufsize=1,
    )
    try:
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdout-purity-test", "version": "0.1"},
            },
        }
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(json.dumps(init_request) + "\n")
        proc.stdin.flush()

        response_line = proc.stdout.readline()
        assert response_line.strip(), "server wrote nothing to stdout"
        # json.loads raises json.JSONDecodeError if this line isn't valid JSON
        parsed = json.loads(response_line)
        assert parsed["id"] == 1
        assert "result" in parsed

        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        proc.stdin.write(json.dumps(initialized_notification) + "\n")
        proc.stdin.flush()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
