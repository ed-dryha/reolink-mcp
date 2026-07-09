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

from reolink_mcp.tools.observe import (
    get_capabilities,
    get_device_info,
    get_recent_events,
    get_snapshot,
    get_states,
    list_cameras,
)


def register_all(mcp: FastMCP) -> None:
    """Register every tool on `mcp`. Call once, after `mcp = FastMCP(...)`."""
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(list_cameras)
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(get_device_info)
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(get_capabilities)
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(get_states)
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(get_recent_events)
    # get_snapshot returns tuple[str, Image]. The installed mcp==1.28.1
    # SDK's default structured-output path tries to build a pydantic schema
    # for the return annotation, and mcp's own Image helper is not a
    # pydantic-schemable type (verified empirically — registering without
    # the flag below raises PydanticSchemaGenerationError at import time).
    # Disabling structured output keeps the tuple's unstructured
    # content-block conversion (str -> TextContent, Image -> ImageContent)
    # that D-01 depends on, without attempting schema generation. This is
    # exclusive to get_snapshot's non-schemable return — the two plain
    # dict[str, Any]-returning tools above must NOT carry this flag.
    mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True), structured_output=False
    )(get_snapshot)
