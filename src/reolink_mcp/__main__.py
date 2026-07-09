"""Entrypoint for `reolink-mcp` / `python -m reolink_mcp`.

The **first** executable statements in this module configure stderr-only
logging — before importing `reolink_mcp.server` or anything it transitively
pulls in (`mcp`, `reolink_aio`, `aiohttp`), any of which could otherwise
configure their own logging handler first (Anti-Pattern 4, ARCHITECTURE.md;
Pitfall 2, PITFALLS.md). stdout is reserved exclusively for the stdio
JSON-RPC transport (SAFE-03) — a single stray byte there corrupts the
protocol and the client silently drops the server.
"""

import logging
import sys

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from reolink_mcp.server import mcp  # noqa: E402


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
