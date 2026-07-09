#!/usr/bin/env python
"""Phase 2 QA harness — drives the real reolink-mcp server over real MCP stdio.

Extends `scripts/qa_phase1.py`'s harness pattern (same `load_env()`/
`preflight()`/`server_params()`/`with_session()` plumbing, same PASS/FAIL
constants) with structural checks for the four new Phase 2 observe tools:

  1. device info    — get_device_info: non-empty model/firmware/hardware/
                       serial/MAC per camera
  2. capabilities    — get_capabilities: curated boolean flags present +
                       typed correctly, ai_detection_types is a list; prints
                       a side-by-side table so the operator can eyeball the
                       expected P437-vs-P320 contrast (both report
                       ptz_presets unsupported; P320 reports white_led/
                       siren/zoom unsupported while P437 reports them
                       supported)
  3. states          — get_states: day_night is a non-empty state-name
                       string or "unsupported"; white_led is a dict or
                       "unsupported"; ir_lights is a bool or "unsupported";
                       siren is the literal string "supported" or
                       "unsupported"; motion is bool; polled_at/age_seconds
                       are sane
  4. recent events   — get_recent_events: person/vehicle/pet are each one of
                       detected/not_detected/unsupported, motion is bool,
                       polled_at/age_seconds are sane

None of these checks assert a specific hardcoded value per camera model —
only structural/type invariants. The whole point of this live check is to
observe genuinely different real hardware (P437 vs. P320) and let a human
confirm the result looks right, mirroring qa_phase1.py's `check_snapshots`'
"print for a human to inspect" pattern rather than asserting exact expected
output.

Usage (from the repo root, reusing Phase 1's existing config.yaml/.env):

    uv run python scripts/qa_phase2.py

Exit code 0 = all executed checks passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = REPO_ROOT / "qa-snapshots"
SESSION_TIMEOUT_S = 60

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

CAPABILITY_BOOL_FIELDS = [
    "zoom",
    "ir_lights",
    "white_led",
    "siren",
    "ptz_presets",
    "day_night",
    "motion_detection",
]
STATE_FIELD_VALIDATORS = {
    # daynight_state() returns a str ("Auto"/"Color"/"Black&White"), or "unsupported"
    "day_night": lambda v: isinstance(v, str) and v != "",
    "white_led": lambda v: isinstance(v, dict) or v == "unsupported",
    "ir_lights": lambda v: isinstance(v, bool) or v == "unsupported",
    "siren": lambda v: v in ("supported", "unsupported"),
}
BASELINE_EVENT_TYPES = ["person", "vehicle", "pet"]
ALLOWED_EVENT_VALUES = {"detected", "not_detected", "unsupported"}


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
        # Mirror the server's own name rule so the mistake surfaces here as
        # one clear line instead of a startup traceback.
        if not re.fullmatch(r"[a-z0-9_]+", name):
            problems.append(
                f"  camera name '{name}' must be lowercase snake_case "
                f"(e.g. 'front_left') — rename it in config.yaml AND in "
                f"the matching .env var"
            )
            continue
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


def parse_tool_result(result, tool_name: str) -> dict:
    """`structuredContent` is the primary path (FastMCP populates it for
    every dict-returning tool this harness calls); parsing the `TextContent`
    block's JSON is a defensive fallback only, mirroring qa_phase1.py's
    `parse_camera_rows`."""
    if result.structuredContent:
        return result.structuredContent
    for block in result.content:
        if isinstance(block, TextContent):
            return json.loads(block.text)
    raise RuntimeError(f"{tool_name} returned no parseable content")


async def _call_per_camera(env, names, tool_name, args_fn=None):
    """Yield (name, result_or_None, error_or_None) for `tool_name` called
    once per camera — shared plumbing for the four check functions below so
    transport/tool-error handling stays identical across all of them."""
    for name in names:

        async def call(session, name=name):
            args = {"camera": name}
            if args_fn:
                args.update(args_fn(name))
            return await session.call_tool(tool_name, args)

        try:
            result = await with_session(env, call)
        except Exception as exc:  # noqa: BLE001 — QA harness reports, never raises
            yield name, None, f"transport error: {exc}"
            continue
        if result.isError:
            text = next(
                (b.text for b in result.content if isinstance(b, TextContent)), "?"
            )
            yield name, None, f"tool error: {text}"
            continue
        yield name, result, None


async def check_device_info(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 1. Device info (get_device_info) ==")
    ok = True
    fields = ["model", "firmware_version", "hardware_version", "serial", "mac_address"]
    async for name, result, err in _call_per_camera(env, names, "get_device_info"):
        if err:
            print(f"  [{FAIL}] {name}: {err}")
            ok = False
            continue
        data = parse_tool_result(result, "get_device_info")
        missing = [
            f
            for f in fields
            if not isinstance(data.get(f), str) or not data.get(f).strip()
        ]
        good = not missing
        ok &= good
        summary = ", ".join(f"{f}={data.get(f)!r}" for f in fields)
        print(f"  [{PASS if good else FAIL}] {name}: {summary}")
        if missing:
            print(f"         missing/empty fields: {missing}")
    return ok


async def check_capabilities(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 2. Capabilities (get_capabilities) ==")
    ok = True
    rows: dict[str, dict] = {}
    async for name, result, err in _call_per_camera(env, names, "get_capabilities"):
        if err:
            print(f"  [{FAIL}] {name}: {err}")
            ok = False
            continue
        data = parse_tool_result(result, "get_capabilities")
        rows[name] = data
        bad_fields = [
            f for f in CAPABILITY_BOOL_FIELDS if not isinstance(data.get(f), bool)
        ]
        list_ok = isinstance(data.get("ai_detection_types"), list)
        good = not bad_fields and list_ok
        ok &= good
        summary = ", ".join(f"{f}={data.get(f)!r}" for f in CAPABILITY_BOOL_FIELDS)
        print(f"  [{PASS if good else FAIL}] {name}: {summary}")
        if bad_fields:
            print(f"         non-boolean fields: {bad_fields}")
        if not list_ok:
            ai_types = data.get("ai_detection_types")
            print(f"         ai_detection_types is not a list: {ai_types!r}")

    if rows:
        print("\n  -- Cross-camera capability contrast (eyeball this) --")
        col_w = 18
        header = f"  {'camera':<{col_w}}" + "".join(
            f"{f:<{col_w}}" for f in CAPABILITY_BOOL_FIELDS
        )
        print(header)
        for name, data in rows.items():
            row = f"  {name:<{col_w}}" + "".join(
                f"{str(data.get(f)):<{col_w}}" for f in CAPABILITY_BOOL_FIELDS
            )
            print(row)
        print(
            "  [info] expected contrast: both cameras' ptz_presets = False "
            "(no PTZ hardware yet); P320's white_led/siren/zoom = False "
            "while P437's = True"
        )
    return ok


async def check_states(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 3. States (get_states) ==")
    ok = True
    async for name, result, err in _call_per_camera(env, names, "get_states"):
        if err:
            print(f"  [{FAIL}] {name}: {err}")
            ok = False
            continue
        data = parse_tool_result(result, "get_states")
        problems = []
        for f, valid in STATE_FIELD_VALIDATORS.items():
            v = data.get(f)
            if not valid(v):
                problems.append(f"{f}={v!r} fails contract check")
        if not isinstance(data.get("motion"), bool):
            problems.append(f"motion={data.get('motion')!r} is not a bool")
        polled_at = data.get("polled_at")
        try:
            datetime.fromisoformat(polled_at)
        except (TypeError, ValueError):
            problems.append(f"polled_at={polled_at!r} is not ISO-parseable")
        age = data.get("age_seconds")
        if isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0:
            problems.append(f"age_seconds={age!r} is not a non-negative number")
        good = not problems
        ok &= good
        summary = ", ".join(
            f"{f}={data.get(f)!r}"
            for f in [*STATE_FIELD_VALIDATORS, "motion", "polled_at", "age_seconds"]
        )
        print(f"  [{PASS if good else FAIL}] {name}: {summary}")
        if problems:
            print(f"         problems: {problems}")
    return ok


async def check_recent_events(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 4. Recent events (get_recent_events) ==")
    ok = True
    async for name, result, err in _call_per_camera(env, names, "get_recent_events"):
        if err:
            print(f"  [{FAIL}] {name}: {err}")
            ok = False
            continue
        data = parse_tool_result(result, "get_recent_events")
        problems = []
        for f in BASELINE_EVENT_TYPES:
            v = data.get(f)
            if v not in ALLOWED_EVENT_VALUES:
                problems.append(f"{f}={v!r} not in {sorted(ALLOWED_EVENT_VALUES)}")
        if not isinstance(data.get("motion"), bool):
            problems.append(f"motion={data.get('motion')!r} is not a bool")
        polled_at = data.get("polled_at")
        try:
            datetime.fromisoformat(polled_at)
        except (TypeError, ValueError):
            problems.append(f"polled_at={polled_at!r} is not ISO-parseable")
        age = data.get("age_seconds")
        if isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0:
            problems.append(f"age_seconds={age!r} is not a non-negative number")
        good = not problems
        ok &= good
        summary = ", ".join(
            f"{f}={data.get(f)!r}"
            for f in [*BASELINE_EVENT_TYPES, "motion", "polled_at", "age_seconds"]
        )
        print(f"  [{PASS if good else FAIL}] {name}: {summary}")
        if problems:
            print(f"         problems: {problems}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    env = load_env()
    names = preflight(env)
    print(f"Cameras under test: {', '.join(names)}")
    print(f"Config: {resolve_config_path()}")

    results: dict[str, bool] = {}
    results["device info"] = await check_device_info(env, names)
    results["capabilities"] = await check_capabilities(env, names)
    results["states"] = await check_states(env, names)
    results["recent events"] = await check_recent_events(env, names)

    print("\n== Summary ==")
    for check, good in results.items():
        print(f"  [{PASS if good else FAIL}] {check}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
