"""Tool registration: attaches every tool function to a `FastMCP` instance.

Tool modules (e.g. `observe.py`, `control.py`) define plain, undecorated
functions and never import `mcp` from `server.py` at module scope.
`register_all(mcp, read_only)` performs the explicit
`mcp.tool(annotations=...)(fn)` registration here instead — this breaks what
would otherwise be a circular import, since `server.py` constructs `mcp`
first and then imports this package to register tools against it.

Observe tools are always registered (readOnlyHint=True, destructiveHint=
False, idempotentHint=True). Control tools are only registered when
`read_only` is False (SAFE-02) — when it is True, they are simply never
added to the tool registry; a single stderr-bound warning is the only
signal, nothing is surfaced to the LLM (D-15).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from reolink_mcp.tools.control import (
    list_presets,
    ptz_move_to_preset,
    ptz_position,
    set_ir_lights,
    set_siren,
    set_spotlight,
    set_white_led,
    set_zoom,
)
from reolink_mcp.tools.observe import (
    get_capabilities,
    get_device_info,
    get_recent_events,
    get_snapshot,
    get_states,
    list_cameras,
)

logger = logging.getLogger(__name__)


def register_all(mcp: FastMCP, read_only: bool = False) -> None:
    """Register every tool on `mcp`. Call once, after `mcp = FastMCP(...)`."""
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        )
    )(list_cameras)
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        )
    )(get_device_info)
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        )
    )(get_capabilities)
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        )
    )(get_states)
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        )
    )(get_recent_events)
    # get_snapshot returns tuple[str, Image]. The installed mcp==1.28.1
    # SDK's default structured-output path tries to build a pydantic schema
    # for the return annotation, and mcp's own Image helper is not a
    # pydantic-schemable type (verified empirically — registering without
    # the flag below raises PydanticSchemaGenerationError at import time).
    # Disabling structured output keeps the tuple's unstructured
    # content-block conversion (str -> TextContent, Image -> ImageContent)
    # that D-01 depends on, without attempting schema generation. This is
    # exclusive to get_snapshot's non-schemable return — the other observe
    # tools above must NOT carry this flag.
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        ),
        structured_output=False,
    )(get_snapshot)

    if not read_only:
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=False
            )
        )(set_siren)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True
            )
        )(set_spotlight)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True
            )
        )(set_ir_lights)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True
            )
        )(set_white_led)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=False
            )
        )(set_zoom)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True
            )
        )(list_presets)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True
            )
        )(ptz_move_to_preset)
        mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True
            )
        )(ptz_position)
    else:
        logger.warning("read-only mode: %d control tools disabled", 8)
