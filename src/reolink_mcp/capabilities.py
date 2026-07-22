"""Capability gating: curated hardware-feature vocabulary -> reolink-aio
`supported()` string, plus a refusal-message builder.

Imports NO `reolink_aio` symbols directly (mock-seam invariant mirroring
`manager.py`'s own rule) — `gate()` takes a duck-typed handle exposing
`.host.supported(channel, capability)` and `.channel` (satisfied by
`manager.CameraHandle` without ever importing it here, keeping this module a
dependency-free leaf).

Consumed by `get_states`/`get_recent_events` in this phase's Plan 2, and by
Phase 3's control tools later for CTRL-10 refusals — same function, not
reimplemented (D-10).
"""

from __future__ import annotations

# Curated key -> exact reolink-aio `supported()` capability string, verified
# against the installed reolink-aio 0.21.3 source (02-RESEARCH.md Pattern 2):
#   - "white_led" consolidates the alternate spotlight vocabulary name —
#     Pattern 5 confirms spotlight and white_led are the identical
#     `floodLight` capability; do not add a second drift-prone key for it.
#   - "siren" maps to "siren_play", never "siren" — Pitfall 3: the raw
#     "siren" string gates the unrelated, out-of-scope set_audio_alarm
#     feature, not the set_siren trigger this project's CTRL-01 wraps.
#   - "ptz_presets" maps to "ptz_presets", never "ptz" — Pitfall 4: "ptz" is
#     true for any motorized optic including zoom-only cameras.
#   - "pan_tilt" and "ptz_guard" (Phase 3 Plan 2) are exact 1:1 matches with
#     no alias gotcha, unlike the two cases above — but both are populated
#     exclusively by the Baichuan (TCP port 9000) subsystem's own discovery
#     pass, not the HTTP-side `construct_capabilities()` (03-RESEARCH.md
#     Pattern 5/finding 3), which is worth noting for future maintainers
#     even though it changes no code behavior here.
CAPABILITY_MAP: dict[str, str] = {
    "zoom": "zoom",
    "ir_lights": "ir_lights",
    "white_led": "floodLight",
    "siren": "siren_play",
    "ptz_presets": "ptz_presets",
    "day_night": "dayNight",
    "motion_detection": "motion_detection",
    "pan_tilt": "pan_tilt",
    "ptz_guard": "ptz_guard",
    "ptz_patrol": "ptz_patrol",
}


def gate(handle, capability: str) -> bool:
    """True if `capability` (a `CAPABILITY_MAP` key, or a raw `supported()`
    string as a fallback for callers that already have the raw string) is
    supported on `handle`'s camera/channel."""
    raw = CAPABILITY_MAP.get(capability, capability)
    return handle.host.supported(handle.channel, raw)


def refusal_message(camera_name: str, capability: str) -> str:
    """Curated refusal string for a capability-gated control tool (CTRL-10,
    Phase 3) — e.g. "camera 'front_door' has no siren"."""
    return f"camera '{camera_name}' has no {capability.replace('_', ' ')}"
