# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-11

### Added

- Core snapshot slice: YAML config with env-var-only camera passwords, a
  `CameraManager` connection layer, and the `list_cameras` and `get_snapshot`
  tools — a live, unconditionally downscaled snapshot from a real camera
  through an MCP client, session-safe alongside other systems on the same
  hardware.
- Full observe surface: `get_device_info`, `get_capabilities`, `get_states`,
  and `get_recent_events` — model/firmware details, capability discovery,
  live device state, and on-demand AI detection polling, validated on the
  P437 and P320.
- Safe controls and safety rails: nine control tools (`set_siren`,
  `set_audio_alarm`, `set_spotlight`, `set_ir_lights`, `set_white_led`,
  `set_zoom`, `list_presets`, `ptz_move_to_preset`, `ptz_position`,
  `ptz_guard`), capability gating on every control, `RMCP_READ_ONLY` mode,
  and MCP tool annotations (`readOnlyHint`/`destructiveHint`) so clients can
  gate approvals on observe vs. control.
- Packaging and release: PyPI publish metadata, an MCP Registry manifest
  (`server.json`), and a hardware-free CI pipeline (ruff + pytest across a
  3.11/3.12/3.13 x ubuntu/macos/windows matrix, plus a per-OS packaging
  smoke check).

[Unreleased]: https://github.com/ed-dryha/reolink-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ed-dryha/reolink-mcp/releases/tag/v1.0.0
