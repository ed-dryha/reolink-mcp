---
created: 2026-07-12T05:50:08.779Z
title: Add set_anti_flicker tool (upstream reolink-aio first)
area: tools
files:
  - src/reolink_mcp/tools/control.py
  - src/reolink_mcp/capabilities.py
---

## Problem

User asked for anti-flicker (50Hz/60Hz mains-frequency image flicker) enablement. v1 has no such tool — image/ISP settings were deferred to the v2 "low-risk setting controls" bucket (TOOL-V2-02 in milestones/v1.0-REQUIREMENTS.md).

Blocker verified 2026-07-12: `reolink-aio` 0.21.3 has **no antiFlicker wrapper** — `Host` exposes neighboring ISP setters (`set_exposure`, `set_HDR`, `set_binning_mode`, day/night) but not the `antiFlicker` field of Reolink's `SetIsp` CGI command. A raw `SetIsp` HTTP call would violate the project constraint "reolink-aio for all camera communication".

## Solution

1. Contribute a `set_anti_flicker` (or generic ISP) wrapper upstream to reolink-aio (github.com/starkillerOG/reolink_aio) — mirrors how `set_HDR`/`set_binning_mode` are implemented.
2. Once released, add a capability-gated, annotated (`destructiveHint: false`, non-read-only) `set_anti_flicker` tool here following the Phase 3 control-tool pattern (CTRL-10 gating, read-back confirmation, curated errors).
