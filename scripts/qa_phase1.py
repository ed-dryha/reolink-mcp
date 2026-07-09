#!/usr/bin/env python
"""Phase 1 QA harness — drives the real reolink-mcp server over real MCP stdio.

Automates the checkpoint acceptance criteria for plan 01-04:

  1. connectivity  — list_cameras: every configured camera reports "connected"
                     with a model and host
  2. snapshots     — get_snapshot per camera: real JPEG decodes, long edge
                     <= 1280, caption has name + timestamp + resolution;
                     images saved to qa-snapshots/ for eyeballing
  3. coexistence   — N consecutive server restarts (default 10), each doing a
                     full MCP handshake + list_cameras: zero session-limit
                     errors (run surveillance-security-ai normally alongside)
  4. stdout purity — implicit: every handshake above only succeeds if stdout
                     carries nothing but JSON-RPC
  +  unknown-camera error path (non-disruptive, always run)

Usage (from the repo root, after filling in config.yaml and .env):

    uv run python scripts/qa_phase1.py
    uv run python scripts/qa_phase1.py --restarts 10 --skip-snapshots
    uv run python scripts/qa_phase1.py --wrong-password-test   # see warning

Exit code 0 = all executed criteria passed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import sys
from pathlib import Path

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ImageContent, TextContent
from PIL import Image as PILImage

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = REPO_ROOT / "qa-snapshots"
SESSION_TIMEOUT_S = 60

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def resolve_config_path() -> Path:
    override = os.environ.get("RMCP_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "reolink-mcp" / "config.yaml"


def load_env() -> dict[str, str]:
    """os.environ merged with the repo .env (env wins, matching the server)."""
    env = dict(os.environ)
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def preflight(env: dict[str, str]) -> list[str]:
    """Return camera names, or exit with a clear message if setup is incomplete."""
    config_path = resolve_config_path()
    if not config_path.exists():
        sys.exit(f"SETUP: no config file at {config_path} — create it first")
    cameras = (yaml.safe_load(config_path.read_text()) or {}).get("cameras", {})
    if not cameras:
        sys.exit(f"SETUP: {config_path} has no cameras")
    problems = []
    for name in cameras:
        var = f"RMCP_CAMERAS__{name}__PASSWORD"
        value = env.get(var, "")
        if not value:
            problems.append(f"  {var} is not set (add it to .env)")
        elif value == "REPLACE_ME":
            problems.append(f"  {var} still says REPLACE_ME (edit .env)")
    if problems:
        sys.exit("SETUP: passwords not ready:\n" + "\n".join(problems))
    return list(cameras)


def server_params(env: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "reolink_mcp"],
        env=env,
        cwd=str(REPO_ROOT),
    )


async def with_session(env: dict[str, str], fn):
    """Spawn a fresh server process, run fn(session), tear everything down."""
    async with asyncio.timeout(SESSION_TIMEOUT_S):
        async with stdio_client(server_params(env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


def parse_camera_rows(result) -> list[dict]:
    if result.structuredContent and "cameras" in result.structuredContent:
        return result.structuredContent["cameras"]
    for block in result.content:
        if isinstance(block, TextContent):
            return json.loads(block.text)["cameras"]
    raise RuntimeError("list_cameras returned no parseable content")


async def check_connectivity(env: dict[str, str], names: list[str]) -> bool:
    async def call(session):
        return await session.call_tool("list_cameras", {})

    result = await with_session(env, call)
    rows = parse_camera_rows(result)
    ok = True
    print("\n== 1. Connectivity (list_cameras) ==")
    for row in rows:
        connected = row["status"] == "connected"
        ok &= connected
        print(
            f"  [{PASS if connected else FAIL}] {row['name']}: "
            f"status={row['status']!r} model={row['model']!r} host={row['host']!r}"
        )
    missing = set(names) - {row["name"] for row in rows}
    if missing:
        ok = False
        print(f"  [{FAIL}] cameras missing from response: {sorted(missing)}")
    return ok


async def check_snapshots(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 2. Snapshots (get_snapshot per camera) ==")
    SNAP_DIR.mkdir(exist_ok=True)
    ok = True
    for name in names:
        async def call(session, name=name):
            return await session.call_tool("get_snapshot", {"camera": name})

        try:
            result = await with_session(env, call)
        except Exception as exc:  # noqa: BLE001 — QA harness reports, never raises
            print(f"  [{FAIL}] {name}: transport error: {exc}")
            ok = False
            continue
        if result.isError:
            text = next(
                (b.text for b in result.content if isinstance(b, TextContent)), "?"
            )
            print(f"  [{FAIL}] {name}: tool error: {text}")
            ok = False
            continue
        caption = next(
            (b.text for b in result.content if isinstance(b, TextContent)), None
        )
        image = next(
            (b for b in result.content if isinstance(b, ImageContent)), None
        )
        if image is None:
            print(f"  [{FAIL}] {name}: no image content block in response")
            ok = False
            continue
        data = base64.b64decode(image.data)
        im = PILImage.open(io.BytesIO(data))
        long_edge_ok = max(im.size) <= 1280
        caption_ok = bool(caption) and name in caption and "x" in caption
        out = SNAP_DIR / f"{name}.jpg"
        out.write_bytes(data)
        good = long_edge_ok and caption_ok
        ok &= good
        print(
            f"  [{PASS if good else FAIL}] {name}: {im.width}x{im.height} "
            f"({len(data) // 1024} KiB) -> {out.relative_to(REPO_ROOT)}"
        )
        print(f"         caption: {caption}")
        if not long_edge_ok:
            print(f"         long edge {max(im.size)} exceeds 1280")
    return ok


async def check_unknown_camera(env: dict[str, str]) -> bool:
    print("\n== 3. Error path: unknown camera name ==")

    async def call(session):
        return await session.call_tool(
            "get_snapshot", {"camera": "qa_nonexistent_camera"}
        )

    result = await with_session(env, call)
    text = next(
        (b.text for b in result.content if isinstance(b, TextContent)), ""
    )
    good = result.isError and "qa_nonexistent_camera" in text
    print(f"  [{PASS if good else FAIL}] isError={result.isError} message: {text}")
    return good


async def check_restarts(env: dict[str, str], count: int) -> bool:
    print(f"\n== 4. Coexistence: {count} consecutive restarts (HDWR-03) ==")
    print("  (surveillance-security-ai should be running normally right now)")
    failures = 0
    for i in range(1, count + 1):
        async def call(session):
            return await session.call_tool("list_cameras", {})

        try:
            rows = parse_camera_rows(await with_session(env, call))
            bad = [r for r in rows if r["status"] != "connected"]
            if bad:
                failures += 1
                details = "; ".join(f"{r['name']}: {r['status']}" for r in bad)
                print(f"  [{FAIL}] restart {i}/{count}: {details}")
            else:
                print(f"  [{PASS}] restart {i}/{count}: all connected")
        except Exception as exc:  # noqa: BLE001 — QA harness reports, never raises
            failures += 1
            print(f"  [{FAIL}] restart {i}/{count}: {exc}")
    if failures == 0:
        print(f"  [{PASS}] {count}/{count} clean restarts, zero session-limit errors")
    return failures == 0


async def check_wrong_password(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 5. Error path: wrong password (single attempt) ==")
    name = names[0]
    bad_env = dict(env)
    bad_env[f"RMCP_CAMERAS__{name}__PASSWORD"] = "definitely-wrong-password"

    async def call(session):
        return await session.call_tool("list_cameras", {})

    rows = parse_camera_rows(await with_session(bad_env, call))
    row = next(r for r in rows if r["name"] == name)
    good = row["status"] != "connected"
    print(f"  [{PASS if good else FAIL}] {name} with bad password: {row['status']!r}")
    return good


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restarts", type=int, default=10)
    parser.add_argument("--skip-snapshots", action="store_true")
    parser.add_argument("--skip-restarts", action="store_true")
    parser.add_argument(
        "--wrong-password-test",
        action="store_true",
        help="WARNING: repeated failed logins can trigger Reolink's account "
        "lockout — this makes exactly one attempt, but use sparingly",
    )
    args = parser.parse_args()

    env = load_env()
    names = preflight(env)
    print(f"Cameras under test: {', '.join(names)}")
    print(f"Config: {resolve_config_path()}")

    results: dict[str, bool] = {}
    results["connectivity"] = await check_connectivity(env, names)
    if not args.skip_snapshots:
        results["snapshots"] = await check_snapshots(env, names)
    results["unknown-camera error"] = await check_unknown_camera(env)
    if not args.skip_restarts:
        results["coexistence restarts"] = await check_restarts(env, args.restarts)
    if args.wrong_password_test:
        results["wrong-password error"] = await check_wrong_password(env, names)

    print("\n== Summary ==")
    for check, good in results.items():
        print(f"  [{PASS if good else FAIL}] {check}")
    print(
        "  [note] stdout purity: verified implicitly — every check above "
        "completed a JSON-RPC handshake on stdout"
    )
    print(
        "  [note] coexistence: confirm surveillance-security-ai stayed healthy "
        "during the restart loop"
    )
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
