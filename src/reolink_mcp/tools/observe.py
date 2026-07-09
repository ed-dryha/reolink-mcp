"""Observe tools (read-only): `list_cameras` and `get_snapshot` (Phase 1);
more land in Phase 2.

Tool functions here are plain, undecorated `async def`s — registration with
`ToolAnnotations` happens explicitly in `tools/__init__.py`'s
`register_all(mcp)`, not via an `@mcp.tool` decorator in this module. This
module intentionally never imports `mcp` from `server.py`: `server.py`
constructs `mcp` and then imports `reolink_mcp.tools` to register tools
against it, so importing `mcp` here at module scope would be circular.
"""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime

from mcp.server.fastmcp import Context, Image
from PIL import Image as PILImage
from reolink_aio.exceptions import CredentialsInvalidError, LoginError, ReolinkError

from reolink_mcp.errors import CameraError, classify_reolink_error


def _is_auth_or_session_failure(exc: ReolinkError) -> bool:
    """True for exceptions that mean "don't bother retrying the next stream"
    — a fresh login attempt against `main` after one of these would either
    fail identically (bad credentials) or accelerate the account-lockout/
    session-limit condition itself (CR-02 / G2, threat T-05-02). Mirrors
    `errors.py::classify_reolink_error`'s own session-limit substring test
    exactly (same two substrings, same LoginError type check) so the two
    never diverge."""
    if isinstance(exc, CredentialsInvalidError):
        return True
    return isinstance(exc, LoginError) and (
        "max session" in str(exc) or "-5" in str(exc)
    )


async def list_cameras(ctx: Context) -> dict:
    """Probe every configured camera concurrently; return one row per camera
    with name/status/model/host — partial success, one dead camera never
    fails the whole call (D-05 parallel probe, D-07 partial success, D-08
    per-row content)."""
    manager = ctx.request_context.lifespan_context.manager

    async def _probe(name: str) -> dict:
        try:
            # Safety net ABOVE the manager's own Host(timeout=10): real
            # P437/P320 cold connects take >3s, so a tighter budget here
            # preempts the manager's curated timeout error and cancels
            # login mid-flight, leaking the Host's aiohttp session
            # (found in Phase 1 hardware QA). This only fires if
            # manager.get() hangs past its own internal timeout.
            async with asyncio.timeout(12):
                handle = await manager.get(name)
            return {
                "name": name,
                "status": "connected",
                "model": handle.host.model,
                "host": manager.configured_host(name),
            }
        except CameraError as exc:
            # manager.get() (Plan 01-02) already curated this message via
            # classify_reolink_error — str(exc) IS the final text. Reuse it
            # verbatim; do NOT re-classify a CameraError instance, which
            # would match none of classify_reolink_error's isinstance/
            # substring branches and silently collapse to the generic
            # fallback message (the exact regression this branch guards
            # against — 01-03-PLAN.md interfaces section).
            return {
                "name": name,
                "status": str(exc),
                "model": None,
                "host": manager.configured_host(name),
            }
        except Exception as exc:
            # Only reached for exceptions manager.get() itself does not
            # wrap — concretely, this function's own asyncio.timeout(3)
            # firing if manager.get() hangs past the probe budget. This is
            # a raw, not-yet-curated exception, so calling
            # classify_reolink_error here (and only here) is correct.
            return {
                "name": name,
                "status": classify_reolink_error(
                    exc, name, manager.configured_host(name)
                ),
                "model": None,
                "host": manager.configured_host(name),
            }

    results = await asyncio.gather(
        *(_probe(name) for name in manager.configured_names())
    )
    return {"cameras": results}


async def get_snapshot(camera: str, ctx: Context) -> tuple[str, Image]:
    """Return a live snapshot from `camera`: sub-stream attempted first,
    main-stream only as a fallback (D-02), downscaled unconditionally to
    ~1280px long edge / JPEG quality 80 (D-03) regardless of which stream
    produced the bytes, and returned as an image content block plus a short
    text caption naming the camera, capture time, and post-downscale
    resolution (D-01).

    `UnknownCameraError`/`CameraError` raised by `manager.get()` propagate
    uncaught here — same discipline as Plan 01-03's interface note: the
    low-level MCP server converts the raised exception's `str()` into the
    tool's error text, and `CameraError.__str__`/`UnknownCameraError.__str__`
    is already the curated message (D-04 for unknown camera names)."""
    manager = ctx.request_context.lifespan_context.manager
    handle = await manager.get(camera)

    # Sub-stream first, main-stream fallback (D-02) — get_snapshot()'s own
    # default is "main", NOT "sub", so stream must always be passed
    # explicitly (01-RESEARCH.md Pattern 3). `except ReolinkError` catches
    # reolink-aio's own error hierarchy so a failed sub attempt can still
    # fall back to main; `except Exception` is the catch-all guaranteeing a
    # non-ReolinkError transport failure (e.g. a raw aiohttp connection drop
    # mid-session) is translated into a curated CameraError instead of
    # propagating as an unhandled traceback (T-04-01). Every ReolinkError
    # raised by either attempt is retained in `last_exc` and classified via
    # classify_reolink_error's curated taxonomy when both attempts produce
    # no data (CR-02 / G2) — it is never silently discarded. An auth/
    # session-class failure (CredentialsInvalidError, or a session-limit
    # LoginError) on the sub attempt raises immediately without trying
    # main, avoiding a second failed login (threat T-05-02).
    last_exc: Exception | None = None
    try:
        data = await handle.host.get_snapshot(handle.channel, stream="sub")
    except ReolinkError as exc:
        if _is_auth_or_session_failure(exc):
            raise CameraError(
                classify_reolink_error(exc, camera, manager.configured_host(camera))
            ) from exc
        last_exc, data = exc, None
    except Exception as exc:
        raise CameraError(
            classify_reolink_error(exc, camera, manager.configured_host(camera))
        ) from exc

    if not data:
        try:
            data = await handle.host.get_snapshot(handle.channel, stream="main")
        except ReolinkError as exc:
            last_exc, data = exc, None
        except Exception as exc:
            raise CameraError(
                classify_reolink_error(exc, camera, manager.configured_host(camera))
            ) from exc

    if not data:
        if last_exc is not None:
            host = manager.configured_host(camera)
            raise CameraError(
                classify_reolink_error(last_exc, camera, host)
            ) from last_exc
        raise CameraError(
            f"camera '{camera}' returned no image — privacy mode may be "
            "enabled, or the camera is mid-reboot"
        )

    # Unconditional downscale (D-02/D-03) — never skipped, regardless of
    # which stream produced the bytes: prevents oversized payloads
    # (PITFALLS.md Pitfall 3, T-04-02) from poisoning the client session or
    # exceeding token/byte/dimension limits.
    im = PILImage.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((1280, 1280), PILImage.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=80, optimize=True)
    jpeg_bytes = out.getvalue()

    caption = (
        f"{camera} — captured {datetime.now(UTC).isoformat()} — "
        f"{im.width}x{im.height}"
    )

    return (caption, Image(data=jpeg_bytes, format="jpeg"))
