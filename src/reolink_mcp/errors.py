"""Curated, actionable error messages for every reolink-aio failure mode.

Implements the verified matcher table (01-RESEARCH.md Pitfall A): every
reolink-aio exception this server can encounter is translated into a
distinct, camera-named, actionable message — never a raw traceback and
never `str(exc)` verbatim (threat T-02-01, Information Disclosure — raw
reolink-aio exception text can embed request URLs, session tokens, or raw
API response dicts).

The raw exception (`repr(exc)`) is always logged at DEBUG to stderr
(SAFE-03) so the full detail remains available to the operator without ever
reaching the tool response.

`reolink-aio` has no distinct exception for the max-session (`rspCode: -5`)
condition — it surfaces as a generic `LoginError` whose message embeds the
raw dict repr of the failed login response. Detecting it requires a
substring match on `str(exc)`, not a type check (see Pitfall A).
"""

from __future__ import annotations

import asyncio
import logging

from reolink_aio.exceptions import (
    CredentialsInvalidError,
    InvalidParameterError,
    LoginError,
    LoginPrivacyModeError,
    NotSupportedError,
    ReolinkConnectionError,
    ReolinkTimeoutError,
)

logger = logging.getLogger(__name__)


class CameraError(Exception):
    """Raised by CameraManager for any translated connect/operation failure.

    `str(err)` is exactly the curated user-facing message — no extra
    wrapping, so it is safe to surface directly in a tool response.
    """


class UnknownCameraError(CameraError):
    """Raised when a caller-supplied camera name has no exact match in the
    configured camera registry (strict exact match — D-04)."""


def classify_reolink_error(exc: Exception, camera_name: str, host: str) -> str:
    """Translate a reolink-aio exception into a curated, actionable message.

    Matcher priority order (most specific first) mirrors 01-RESEARCH.md's
    Pitfall A table exactly:
      CredentialsInvalidError -> LoginPrivacyModeError ->
      LoginError w/ "max session"/"-5" substring -> ReolinkConnectionError ->
      ReolinkTimeoutError / asyncio.TimeoutError -> any other LoginError ->
      fallback for any other exception type.
    """
    if isinstance(exc, CredentialsInvalidError):
        message = (
            f"wrong credentials for camera '{camera_name}' — check "
            f"RMCP_CAMERAS__{camera_name}__PASSWORD"
        )
    elif isinstance(exc, LoginPrivacyModeError):
        message = f"camera '{camera_name}' has privacy mode enabled"
    elif isinstance(exc, LoginError) and (
        "max session" in str(exc) or "-5" in str(exc)
    ):
        message = (
            f"camera '{camera_name}' session limit reached — another "
            f"client (app/NVR/surveillance-security-ai) may be holding "
            f"sessions; wait for token expiry (~1h) or close the other "
            f"client"
        )
    elif isinstance(exc, ReolinkConnectionError):
        message = f"camera '{camera_name}' unreachable at {host} — check power/network"
    elif isinstance(exc, (ReolinkTimeoutError, asyncio.TimeoutError)):
        message = f"camera '{camera_name}' timed out at {host} — check power/network"
    elif isinstance(exc, LoginError):
        message = f"login failed for camera '{camera_name}' — see server logs"
    else:
        message = f"unexpected error for camera '{camera_name}' — see server logs"

    logger.debug(
        "classify_reolink_error camera=%s host=%s exc=%r -> %s",
        camera_name,
        host,
        exc,
        message,
    )
    return message


def classify_control_error(exc: Exception, camera_name: str, host: str) -> str:
    """Translate a control-tool exception into a curated, actionable message
    (Pitfall 8, 03-RESEARCH.md).

    `InvalidParameterError`/`NotSupportedError` are curated here explicitly:
    their `str(exc)` is safe to surface (built from static strings + non-
    secret values, T-03-05) but always carries a leading `func_name: `
    prefix from reolink-aio's own raise sites — stripped before it reaches
    the tool response. Every other exception type delegates entirely to
    `classify_reolink_error()` — this function never duplicates that
    matcher table.
    """
    if isinstance(exc, (InvalidParameterError, NotSupportedError)):
        detail = str(exc)
        if ":" in detail:
            detail = detail.split(":", 1)[1].strip()
        message = f"camera '{camera_name}' rejected the request — {detail}"
    else:
        message = classify_reolink_error(exc, camera_name, host)

    logger.debug(
        "classify_control_error camera=%s host=%s exc=%r -> %s",
        camera_name,
        host,
        exc,
        message,
    )
    return message


def unknown_camera_message(attempted: str, configured: list[str]) -> str:
    """Self-correcting error listing every configured camera name (D-04)."""
    names = ", ".join(sorted(configured))
    return f"unknown camera '{attempted}' — configured cameras: {names}"
