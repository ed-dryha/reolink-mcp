"""Tests for classify_reolink_error's matcher table (01-RESEARCH.md Pitfall A)
and classify_control_error's curation of InvalidParameterError/
NotSupportedError (03-RESEARCH.md Pitfall 8).

Ten behaviors (CONN-04, CONN-05):
1. CredentialsInvalidError -> wrong-credentials message naming the env var.
2. LoginError with a raw "max session"/"-5" dict-repr -> session-limit message
   (the exact regression case named in RESEARCH.md Pitfall A).
3. LoginPrivacyModeError -> privacy-mode message.
4. ReolinkConnectionError -> exact unreachable message (CONTEXT.md wording).
5. ReolinkTimeoutError and a bare asyncio.TimeoutError -> same timed-out message.
6. A generic LoginError (no session-limit substring) -> fallback message that
   never leaks the raw exception text (Pitfall 8 regression guard).
7. unknown_camera_message -> self-correcting error listing configured cameras.
8. classify_control_error strips the leading "func_name: " prefix from
   InvalidParameterError.
9. classify_control_error strips the leading "func_name: " prefix from
   NotSupportedError.
10. classify_control_error delegates every other exception type to
    classify_reolink_error unchanged (regression guard: no diverging
    matcher table).
"""

import asyncio

import pytest
from reolink_aio.exceptions import (
    CredentialsInvalidError,
    InvalidParameterError,
    LoginError,
    LoginPrivacyModeError,
    NotSupportedError,
    ReolinkConnectionError,
    ReolinkTimeoutError,
)

from reolink_mcp.errors import (
    classify_control_error,
    classify_reolink_error,
    unknown_camera_message,
)


def test_credentials_invalid_names_camera_and_env_var():
    message = classify_reolink_error(
        CredentialsInvalidError("invalid user"), "front_door", "192.168.1.10"
    )

    assert "wrong credentials for camera 'front_door'" in message
    assert "RMCP_CAMERAS__front_door__PASSWORD" in message


def test_session_limit_detected_from_raw_dict_repr():
    exc = LoginError(
        "Login error, unknown response format from host 1.2.3.4:443: "
        "[{'cmd': 'Login', 'code': 1, 'error': "
        "{'detail': 'max session', 'rspCode': -5}}]"
    )

    message = classify_reolink_error(exc, "front_door", "1.2.3.4")

    assert "session limit reached" in message
    assert "surveillance-security-ai" in message


def test_privacy_mode_enabled():
    message = classify_reolink_error(
        LoginPrivacyModeError("privacy mode is enabled"), "front_door", "192.168.1.10"
    )

    assert "privacy mode enabled" in message


def test_connection_error_exact_wording():
    message = classify_reolink_error(
        ReolinkConnectionError("refused"), "garage", "192.168.1.44"
    )

    assert message == (
        "camera 'garage' unreachable at 192.168.1.44 — check power/network"
    )


@pytest.mark.parametrize(
    "exc",
    [
        ReolinkTimeoutError("timed out"),
        asyncio.TimeoutError(),
    ],
)
def test_timeout_variants_produce_same_message(exc):
    message = classify_reolink_error(exc, "garage", "192.168.1.44")

    assert "timed out at 192.168.1.44" in message


def test_generic_login_error_falls_back_without_leaking_raw_text():
    message = classify_reolink_error(
        LoginError("some other failure"), "front_door", "192.168.1.10"
    )

    assert message == "login failed for camera 'front_door' — see server logs"
    assert "some other failure" not in message


def test_unknown_camera_message_lists_configured_cameras():
    message = unknown_camera_message("font_door", ["front_door", "garage"])

    assert "unknown camera 'font_door'" in message
    assert "front_door, garage" in message


def test_classify_control_error_strips_invalid_parameter_prefix():
    message = classify_control_error(
        InvalidParameterError("set_zoom: zoom value 99 not in range 0..30"),
        "front_door",
        "192.168.1.10",
    )

    assert "zoom value 99 not in range 0..30" in message
    assert "set_zoom:" not in message


def test_classify_control_error_strips_not_supported_prefix():
    message = classify_control_error(
        NotSupportedError("set_ptz_guard: ... is not available"),
        "garage",
        "192.168.1.11",
    )

    assert message
    assert "set_ptz_guard:" not in message


def test_classify_control_error_delegates_other_exceptions_unchanged():
    exc = ReolinkConnectionError("refused")

    assert classify_control_error(exc, "garage", "192.168.1.44") == (
        classify_reolink_error(exc, "garage", "192.168.1.44")
    )
