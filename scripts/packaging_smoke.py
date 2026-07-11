"""Packaging smoke check (D-09, 04-01-PLAN.md Task 1).

Proves the *installed* `reolink-mcp` console-script entry point fails loudly
and cleanly when run with no config present — never a raw traceback, never a
hang, never anything on stdout (SAFE-03's stdout-purity guarantee must
survive the build/install round-trip, not just `python -m reolink_mcp` in a
dev checkout).

Invoked against a real built wheel via:
    uv build && uv run --isolated --no-project --with dist/*.whl \\
        python scripts/packaging_smoke.py

No pytest dependency and no import of `reolink_mcp` — this script only ever
runs against the installed console-script binary, and must work standalone
inside the throwaway `--isolated` environment `uv run` creates for it.
scripts/* is T201-exempt (see pyproject.toml's ruff per-file-ignores) — this
is a client-side CLI check, printing is the point.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile


def _fail(
    message: str,
    returncode: object = None,
    stdout: object = None,
    stderr: object = None,
) -> None:
    print(f"packaging smoke FAILED: {message}", file=sys.stderr)
    if returncode is not None:
        print(f"  returncode: {returncode!r}", file=sys.stderr)
    if stdout is not None:
        print(f"  stdout: {stdout!r}", file=sys.stderr)
    if stderr is not None:
        print(f"  stderr: {stderr!r}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    entry_point = shutil.which("reolink-mcp")
    if entry_point is None:
        _fail("could not locate the 'reolink-mcp' console-script on PATH")
        return

    isolated_home = tempfile.mkdtemp(prefix="reolink-mcp-packaging-smoke-")
    isolated_env = dict(os.environ)
    isolated_env["HOME"] = isolated_home
    isolated_env["USERPROFILE"] = isolated_home
    isolated_env.pop("RMCP_CONFIG_FILE", None)

    try:
        result = subprocess.run(
            [entry_point],
            capture_output=True,
            text=True,
            timeout=15,
            env=isolated_env,
        )
    except subprocess.TimeoutExpired:
        _fail("entry point hung instead of exiting (no config present)")
        return

    if result.returncode == 0:
        _fail(
            "entry point exited 0 with no config present (expected a "
            "non-zero, curated failure)",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return

    if result.stdout != "":
        _fail(
            "stdout was not empty (stdio JSON-RPC transport purity "
            "violated)",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return

    if "config error:" not in result.stderr:
        _fail(
            "stderr did not contain the curated 'config error:' prefix "
            "(may be a raw traceback instead)",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return

    print("packaging smoke OK: entry point failed loudly and cleanly with no config")
    sys.exit(0)


if __name__ == "__main__":
    main()
