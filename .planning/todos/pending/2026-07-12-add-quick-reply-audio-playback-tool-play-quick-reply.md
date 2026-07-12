---
created: 2026-07-12T05:50:08.779Z
title: Add quick-reply audio playback tool (play_quick_reply)
area: tools
files:
  - src/reolink_mcp/tools/control.py
  - src/reolink_mcp/capabilities.py
---

## Problem

User asked whether the server can "feed the camera with sounds" (play audio through the camera speaker). v1 has no audio-output tool beyond `set_audio_alarm` (siren buzzer). TOOL-V2-04 in milestones/v1.0-REQUIREMENTS.md already lists "two-way audio talk-down, quick replies" as a v2 candidate.

API reality verified 2026-07-12 against reolink-aio 0.21.3:
- **Supported:** `play_quick_reply` (plays a pre-recorded quick-reply audio file stored on the camera), `quick_reply_dict` (enumerate stored replies), `set_quick_reply`, plus volume controls (`set_volume`, `volume_speak`, `alarm_volume`, `message_volume`).
- **NOT supported:** arbitrary/live audio push (true two-way talk-down). That requires the Baichuan/ONVIF audio backchannel — Home Assistant does it via go2rtc, not reolink-aio. Out of reach without a major new dependency.
- Quick replies are primarily a doorbell feature — whether P437/P320 support them needs a live capability check (`api.supported(ch, "quick_reply")`) before committing scope.

## Solution

v2 tool sketch: `list_quick_replies` (read-only) + `play_quick_reply(camera, reply_id)` (capability-gated, annotated) + optional `set_volume`. Defer live talk-down unless/until a backchannel approach is chosen. Validate P437/P320 quick-reply capability first.
