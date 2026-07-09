"""Regression tests for `scripts/qa_phase2.py`'s `STATE_FIELD_VALIDATORS`
(CR-01 fix, 02-04-PLAN.md Task 1).

`scripts/qa_phase2.py` is a standalone QA script, not an installed package
module — it is imported here by inserting `REPO_ROOT / "scripts"` onto
`sys.path` before importing, mirroring how the script itself is invoked
(`uv run python scripts/qa_phase2.py`). Its own top-level imports (`yaml`,
`mcp`) are already project dependencies, so this import performs no network
I/O and needs no camera hardware — the whole point of this test file is to
prove the CR-01 fix algorithmically, without real cameras.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from qa_phase2 import STATE_FIELD_VALIDATORS  # noqa: E402


def test_day_night_accepts_non_empty_strings_including_unsupported():
    valid = STATE_FIELD_VALIDATORS["day_night"]
    assert valid("Auto")
    assert valid("Black&White")
    assert valid("unsupported")  # itself a non-empty string — not special-cased


def test_day_night_rejects_empty_string_and_none():
    valid = STATE_FIELD_VALIDATORS["day_night"]
    assert not valid("")
    assert not valid(None)


def test_white_led_accepts_dict_or_unsupported():
    valid = STATE_FIELD_VALIDATORS["white_led"]
    assert valid({"on": True, "brightness": 80})
    assert valid("unsupported")


def test_white_led_rejects_bare_bool():
    valid = STATE_FIELD_VALIDATORS["white_led"]
    assert not valid(False)
    assert not valid(True)


def test_ir_lights_accepts_bool_or_unsupported():
    valid = STATE_FIELD_VALIDATORS["ir_lights"]
    assert valid(True)
    assert valid(False)
    assert valid("unsupported")


def test_ir_lights_rejects_non_boolean_string():
    valid = STATE_FIELD_VALIDATORS["ir_lights"]
    assert not valid("Auto")


def test_siren_accepts_only_supported_or_unsupported_literals():
    valid = STATE_FIELD_VALIDATORS["siren"]
    assert valid("supported")
    assert valid("unsupported")


def test_siren_rejects_bool():
    valid = STATE_FIELD_VALIDATORS["siren"]
    assert not valid(True)
    assert not valid(False)


def test_cr01_regression_repro_payload_produces_zero_problems():
    """The exact 02-VERIFICATION.md repro payload — pre-fix, day_night and
    siren both produced false failures on this exact payload."""
    payload = {
        "day_night": "Auto",
        "white_led": "unsupported",
        "ir_lights": True,
        "siren": "supported",
    }
    problems = [
        f"{f}={payload.get(f)!r} fails contract check"
        for f, valid in STATE_FIELD_VALIDATORS.items()
        if not valid(payload.get(f))
    ]
    assert problems == []
