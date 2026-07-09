"""Tests for capabilities.py: CAPABILITY_MAP -> supported() string mapping,
gate(), and refusal_message() (02-01-PLAN.md Task 1, D-10).

Mock handles build `host.supported` as a per-capability-string dict lookup
(never a single blanket True/False) — a blanket mock cannot catch the
siren/siren_play or ptz/ptz_presets string-mismatch bug class (RESEARCH.md
Pitfalls 3 and 4).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from reolink_mcp.capabilities import CAPABILITY_MAP, gate, refusal_message


def _handle(supported_map: dict[str, bool], channel: int = 0) -> SimpleNamespace:
    """Lightweight mock handle exposing only what gate() reads:
    handle.host.supported and handle.channel."""
    return SimpleNamespace(
        host=SimpleNamespace(
            supported=lambda channel, cap: supported_map.get(cap, False)
        ),
        channel=channel,
    )


def test_gate_siren_uses_siren_play_not_siren():
    # Pitfall 3 regression guard: "siren" gates the unrelated
    # set_audio_alarm feature — using it would silently return False here
    # even though siren_play (the correct gate) is True.
    handle = _handle({"siren_play": True, "siren": False})

    assert gate(handle, "siren") is True


def test_gate_ptz_presets_uses_ptz_presets_not_ptz():
    # Pitfall 4 regression guard: "ptz" is true for any motorized optic
    # (including zoom-only cameras) — using it would silently return True
    # here even though ptz_presets (the correct gate) is False.
    handle = _handle({"ptz_presets": False, "ptz": True})

    assert gate(handle, "ptz_presets") is False


def test_gate_white_led_uses_floodlight():
    handle = _handle({"floodLight": True})

    assert gate(handle, "white_led") is True


@pytest.mark.parametrize(
    ("capability", "raw_string"),
    [
        ("zoom", "zoom"),
        ("ir_lights", "ir_lights"),
        ("day_night", "dayNight"),
        ("motion_detection", "motion_detection"),
    ],
)
def test_gate_maps_curated_key_to_exact_raw_string(capability, raw_string):
    handle = _handle({raw_string: True})

    assert gate(handle, capability) is True


def test_capability_map_has_exactly_seven_entries():
    assert CAPABILITY_MAP == {
        "zoom": "zoom",
        "ir_lights": "ir_lights",
        "white_led": "floodLight",
        "siren": "siren_play",
        "ptz_presets": "ptz_presets",
        "day_night": "dayNight",
        "motion_detection": "motion_detection",
    }


def test_refusal_message_contains_camera_and_capability():
    message = refusal_message("front_door", "siren")

    assert "front_door" in message
    assert "siren" in message


def test_refusal_message_replaces_underscores_with_spaces():
    message = refusal_message("garage", "ptz_presets")

    assert "ptz presets" in message
    assert "ptz_presets" not in message
