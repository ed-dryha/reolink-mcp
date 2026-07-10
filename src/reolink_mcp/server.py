"""FastMCP server instance: typed lifespan owning the `CameraManager`.

Module-scope settings are loaded once, before `mcp` is constructed, so
`register_all(mcp, read_only=settings.read_only)` (also at the bottom of
this module, after `mcp` is constructed) can know at registration time
whether control tools should be registered (SAFE-02) — see
`tools/__init__.py` for why tool modules define plain, undecorated functions
instead of using `@mcp.tool` decorators directly (avoids a circular import
between this module and `tools/observe.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from reolink_mcp.config import Settings, load_settings
from reolink_mcp.manager import CameraManager
from reolink_mcp.tools import register_all


@dataclass
class AppContext:
    """Typed lifespan context, exposed to every tool via
    `ctx.request_context.lifespan_context` (verified field — `mcp` v1.28.1
    `shared/context.py`, 01-RESEARCH.md)."""

    manager: CameraManager
    settings: Settings


# Loaded once at module scope, before `mcp` is constructed and before
# `register_all()` runs — `register_all()` needs `settings.read_only` at
# registration time (SAFE-02), which `app_lifespan` alone cannot provide
# since it only runs per-session, after registration has already happened.
# `load_settings()` raises `SystemExit` loudly on any config problem
# (CONN-01/02); that failure is intentional and left to propagate, not
# caught here. `app_lifespan` below reuses this exact object — never calls
# `load_settings()` a second time.
settings = load_settings()


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Startup: config-only validation, no camera I/O (D-06) — reuses the
    module-scope `settings` loaded above. Shutdown: guaranteed, exception-
    tolerant logout of every connected camera (Pitfall 5, PITFALLS.md)."""
    manager = CameraManager(settings.cameras)
    try:
        yield AppContext(manager=manager, settings=settings)
    finally:
        await manager.close_all()


mcp = FastMCP("reolink-mcp", lifespan=app_lifespan)

register_all(mcp, read_only=settings.read_only)
