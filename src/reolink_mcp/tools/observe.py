"""Observe tools (read-only): `list_cameras` (Phase 1); more land in Phase 2.

Tool functions here are plain, undecorated `async def`s — registration with
`ToolAnnotations` happens explicitly in `tools/__init__.py`'s
`register_all(mcp)`, not via an `@mcp.tool` decorator in this module. This
module intentionally never imports `mcp` from `server.py`: `server.py`
constructs `mcp` and then imports `reolink_mcp.tools` to register tools
against it, so importing `mcp` here at module scope would be circular.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import Context

from reolink_mcp.errors import CameraError, classify_reolink_error


async def list_cameras(ctx: Context) -> dict:
    """Probe every configured camera concurrently; return one row per camera
    with name/status/model/host — partial success, one dead camera never
    fails the whole call (D-05 parallel probe, D-07 partial success, D-08
    per-row content)."""
    manager = ctx.request_context.lifespan_context.manager

    async def _probe(name: str) -> dict:
        try:
            async with asyncio.timeout(3):
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
