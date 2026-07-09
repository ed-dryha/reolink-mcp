"""Entrypoint for `reolink-mcp` / `python -m reolink_mcp`.

The real FastMCP server, stderr-only logging setup, and lifespan wiring land
in Plan 01-03. This stub only exists so that `[project.scripts]` resolves to
a real (if incomplete) target during Plan 01-01's project scaffolding.
"""


def main() -> None:
    raise NotImplementedError("server scaffold lands in Plan 01-03")


if __name__ == "__main__":
    main()
