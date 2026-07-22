"""Integration test proving RMCP_READ_ONLY is wired through server.py's real
module-scope `settings = load_settings()` -> `register_all(mcp,
read_only=settings.read_only)` at actual import time (SAFE-02) — not just
inside a mocked unit test.

The module-level `import reolink_mcp.server` below runs during pytest's
COLLECTION phase, before `tmp_config` exists — thanks to
`tests/conftest.py`'s collection-time hermeticity stub (BLOCKER 1), it
resolves against a throwaway, always-valid stub config instead of crashing
collection on a host with no real camera config anywhere. That first,
discarded `settings`/`mcp` state is never asserted on by either test below
— both reload the module fresh under `tmp_config`.
"""

import importlib

import reolink_mcp.server


async def test_read_only_true_strips_control_tools_at_real_import(
    tmp_config, monkeypatch
):
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__front_door__PASSWORD", "secret1")
    monkeypatch.setenv("RMCP_READ_ONLY", "true")

    importlib.reload(reolink_mcp.server)
    tools = await reolink_mcp.server.mcp.list_tools()

    assert len(tools) == 6


async def test_read_only_unset_registers_control_tools_at_real_import(
    tmp_config, monkeypatch
):
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__front_door__PASSWORD", "secret1")
    monkeypatch.delenv("RMCP_READ_ONLY", raising=False)

    importlib.reload(reolink_mcp.server)
    tools = await reolink_mcp.server.mcp.list_tools()

    # 6 observe tools + set_siren/set_spotlight/set_ir_lights/set_white_led
    # (Phase 3 Plan 1) + set_zoom/list_presets/ptz_move_to_preset/
    # ptz_position (Phase 3 Plan 2) + ptz_guard (Phase 3 Plan 3) +
    # set_audio_alarm (Plan 03-03 checkpoint deviation) + save_preset
    # (locally-maintained fork addition) — all 11 control tools registered,
    # the complete 17-tool registry.
    assert len(tools) == 17
