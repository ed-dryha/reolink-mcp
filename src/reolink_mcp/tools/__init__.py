"""Tool registration: attaches every tool function to a `FastMCP` instance.

Tool modules (e.g. `observe.py`) define plain, undecorated functions and
never import `mcp` from `server.py` at module scope. `register_all(mcp)`
performs the explicit `mcp.tool(annotations=...)(fn)` registration here
instead — this breaks what would otherwise be a circular import, since
`server.py` constructs `mcp` first and then imports this package to
register tools against it.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from reolink_mcp.tools.observe import list_cameras


def register_all(mcp: FastMCP) -> None:
    """Register every tool on `mcp`. Call once, after `mcp = FastMCP(...)`."""
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(list_cameras)
