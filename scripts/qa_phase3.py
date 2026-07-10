#!/usr/bin/env python
"""Phase 3 QA harness — drives the real reolink-mcp server over real MCP stdio.

Extends `scripts/qa_phase2.py`'s harness pattern (same `load_env()`/
`preflight()`/`server_params()`/`with_session()`/`parse_tool_result()`/
`_call_per_camera()` plumbing, same PASS/FAIL constants, same
per-tool-call-spawns-a-fresh-server-subprocess discipline) with six checks
covering all nine control tools:

  1. spotlight      — set_spotlight: on -> off round-trip, read-back matches
                       the requested state, per camera with white_led
  2. IR lights       — set_ir_lights: auto (baseline) -> on -> auto
                       (restore), read-back matches the requested mode
  3. white LED       — set_white_led: on(brightness=50) -> off round-trip
  4. zoom             — set_zoom: relative step in -> step out, position_pct
                       is an int 0-100 and the two calls moved opposite ways
  5. PTZ              — list_presets / ptz_move_to_preset / ptz_position /
                       ptz_guard(action="set") on the first PTZ-capable
                       camera found; prints a clear [SKIP] (never a false
                       failure) when no configured camera reports any PTZ
                       capability — the project's acknowledged no-hardware
                       gap
  6. siren (LAST)     — the one interactive, physically loud check (D-16):
                       requires an explicit human confirmation immediately
                       before the one ~2s audible burst, times the API
                       round-trip with a stopwatch, and asks the operator to
                       report the perceived real-world duration — resolving
                       Pitfall 1/Assumption A1's `duration` units ambiguity
                       against reality. The siren is unconditionally stopped
                       afterward regardless of outcome.

Every mutating check issues its camera-facing call via
`await session.call_tool("set_...", {...})` inside a fresh `with_session(...)`
per call — exactly `qa_phase2.py`'s own black-box-over-real-stdio-subprocess
discipline. This harness never imports or calls
`src/reolink_mcp/tools/control.py`'s Python functions directly.

Usage (from the repo root, reusing Phase 1/2's existing config.yaml/.env):

    uv run python scripts/qa_phase3.py

Exit code 0 = every executed (non-skipped) check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
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
    once per camera — shared plumbing so transport/tool-error handling
    stays identical across every check below."""
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


async def _capabilities_by_camera(
    env: dict[str, str], names: list[str]
) -> dict[str, dict]:
    """Fetch get_capabilities once per camera — shared plumbing so every
    capability-gated check below (spotlight/IR/white LED/zoom/PTZ/siren)
    skips cleanly on unsupported cameras instead of duplicating the same
    probe six times. Cameras whose get_capabilities call itself failed are
    simply omitted (callers treat a missing entry as unsupported)."""
    caps: dict[str, dict] = {}
    async for name, result, err in _call_per_camera(env, names, "get_capabilities"):
        if err:
            print(f"  [{FAIL}] {name}: get_capabilities failed: {err}")
            continue
        caps[name] = parse_tool_result(result, "get_capabilities")
    return caps


async def _call_tool(
    env: dict[str, str], call, tool_name: str
) -> tuple[dict | None, str | None]:
    """Run one `call(session)` closure inside a fresh `with_session(...)` and
    parse its result — shared plumbing for every mutating check below,
    mirroring `_call_per_camera`'s own transport/tool-error handling."""
    try:
        result = await with_session(env, call)
    except Exception as exc:  # noqa: BLE001 — QA harness reports, never raises
        return None, f"transport error: {exc}"
    if result.isError:
        text = next(
            (b.text for b in result.content if isinstance(b, TextContent)), "?"
        )
        return None, f"tool error: {text}"
    return parse_tool_result(result, tool_name), None


async def check_spotlight(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 1. Spotlight (set_spotlight) ==")
    caps_by_name = await _capabilities_by_camera(env, names)
    ok = True
    for name in names:
        caps = caps_by_name.get(name)
        if not caps or not caps.get("white_led"):
            print(f"  [SKIP] {name}: no white_led capability")
            continue

        good = True
        for label, on in [("on", True), ("off", False)]:

            async def call(session, name=name, on=on):
                args = {"camera": name, "on": on}
                return await session.call_tool("set_spotlight", args)

            data, err = await _call_tool(env, call, "set_spotlight")
            if err:
                print(f"  [{FAIL}] {name} ({label}): {err}")
                good = False
                continue
            actual = (data or {}).get("spotlight", {}).get("on")
            if actual != on:
                print(
                    f"  [{FAIL}] {name} ({label}): expected on={on}, got {actual!r}"
                )
                good = False
        ok &= good
        print(f"  [{PASS if good else FAIL}] {name}: on -> off round-trip")
    return ok


async def check_ir_lights(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 2. IR lights (set_ir_lights) ==")
    caps_by_name = await _capabilities_by_camera(env, names)
    ok = True
    for name in names:
        caps = caps_by_name.get(name)
        if not caps or not caps.get("ir_lights"):
            print(f"  [SKIP] {name}: no ir_lights capability")
            continue

        good = True
        # Baseline auto, then on, then restore auto — D-06's factory default
        # must be the state this check leaves the camera in.
        for label, mode in [
            ("baseline auto", "auto"),
            ("on", "on"),
            ("restore auto", "auto"),
        ]:

            async def call(session, name=name, mode=mode):
                args = {"camera": name, "mode": mode}
                return await session.call_tool("set_ir_lights", args)

            data, err = await _call_tool(env, call, "set_ir_lights")
            if err:
                print(f"  [{FAIL}] {name} ({label}): {err}")
                good = False
                continue
            actual = (data or {}).get("ir_lights")
            if actual != mode:
                print(
                    f"  [{FAIL}] {name} ({label}): expected mode={mode!r}, "
                    f"got {actual!r}"
                )
                good = False
        ok &= good
        print(f"  [{PASS if good else FAIL}] {name}: auto -> on -> auto round-trip")
    return ok


async def check_white_led(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 3. White LED (set_white_led) ==")
    caps_by_name = await _capabilities_by_camera(env, names)
    ok = True
    for name in names:
        caps = caps_by_name.get(name)
        if not caps or not caps.get("white_led"):
            print(f"  [SKIP] {name}: no white_led capability")
            continue

        good = True
        for label, on, brightness in [("on", True, 50), ("off", False, None)]:

            async def call(session, name=name, on=on, brightness=brightness):
                args = {"camera": name, "on": on}
                if brightness is not None:
                    args["brightness"] = brightness
                return await session.call_tool("set_white_led", args)

            data, err = await _call_tool(env, call, "set_white_led")
            if err:
                print(f"  [{FAIL}] {name} ({label}): {err}")
                good = False
                continue
            actual = (data or {}).get("white_led", {}).get("on")
            if actual != on:
                print(
                    f"  [{FAIL}] {name} ({label}): expected on={on}, got {actual!r}"
                )
                good = False
        ok &= good
        print(
            f"  [{PASS if good else FAIL}] {name}: on(brightness=50) -> off round-trip"
        )
    return ok


async def check_zoom(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 4. Zoom (set_zoom) ==")
    caps_by_name = await _capabilities_by_camera(env, names)
    ok = True
    for name in names:
        caps = caps_by_name.get(name)
        if not caps or not caps.get("zoom"):
            print(f"  [SKIP] {name}: no zoom capability")
            continue

        good = True
        positions: dict[str, int | None] = {}
        for label, step in [("in", 1), ("out", -1)]:

            async def call(session, name=name, step=step):
                args = {"camera": name, "step": step}
                return await session.call_tool("set_zoom", args)

            data, err = await _call_tool(env, call, "set_zoom")
            if err:
                print(f"  [{FAIL}] {name} ({label}): {err}")
                good = False
                continue
            pct = (data or {}).get("zoom", {}).get("position_pct")
            if not isinstance(pct, int) or not (0 <= pct <= 100):
                print(
                    f"  [{FAIL}] {name} ({label}): position_pct={pct!r} "
                    f"not an int 0-100"
                )
                good = False
            positions[label] = pct

        both_set = positions.get("in") is not None and positions.get("out") is not None
        if good and both_set:
            if positions["in"] == positions["out"]:
                print(
                    f"  [{FAIL}] {name}: step=1 then step=-1 produced the same "
                    f"position_pct ({positions['in']}) — expected opposite movement"
                )
                good = False

        ok &= good
        print(
            f"  [{PASS if good else FAIL}] {name}: nudge in -> nudge out ({positions})"
        )
    return ok


async def check_ptz(env: dict[str, str], names: list[str]) -> bool:
    print(
        "\n== 5. PTZ (list_presets / ptz_move_to_preset / ptz_position / ptz_guard) =="
    )
    caps_by_name = await _capabilities_by_camera(env, names)
    ptz_capable = [
        name
        for name in names
        if caps_by_name.get(name)
        and (
            caps_by_name[name].get("ptz_presets")
            or caps_by_name[name].get("pan_tilt")
            or caps_by_name[name].get("ptz_guard")
        )
    ]
    if not ptz_capable:
        print(
            "  [SKIP] no PTZ-capable camera configured — mock-validated only "
            "(project has no PTZ hardware yet)"
        )
        return True

    ok = True
    for name in ptz_capable:

        async def call_list_presets(session, name=name):
            return await session.call_tool("list_presets", {"camera": name})

        data, err = await _call_tool(env, call_list_presets, "list_presets")
        if err:
            print(f"  [{FAIL}] {name} list_presets: {err}")
            ok = False
            continue
        presets = (data or {}).get("presets", {})
        print(f"  [{PASS}] {name} list_presets: {presets}")

        if presets:
            first_preset = next(iter(presets))

            async def call_move(session, name=name, preset=first_preset):
                args = {"camera": name, "preset": preset}
                return await session.call_tool("ptz_move_to_preset", args)

            data, err = await _call_tool(env, call_move, "ptz_move_to_preset")
            good = err is None
            print(
                f"  [{PASS if good else FAIL}] {name} "
                f"ptz_move_to_preset({first_preset!r}): {data if good else err}"
            )
            ok &= good

        async def call_position(session, name=name):
            return await session.call_tool("ptz_position", {"camera": name})

        data, err = await _call_tool(env, call_position, "ptz_position")
        good = err is None
        print(
            f"  [{PASS if good else FAIL}] {name} ptz_position: {data if good else err}"
        )
        ok &= good

        async def call_guard(session, name=name):
            args = {"camera": name, "action": "set"}
            return await session.call_tool("ptz_guard", args)

        data, err = await _call_tool(env, call_guard, "ptz_guard")
        good = err is None
        print(
            f"  [{PASS if good else FAIL}] {name} ptz_guard(set): "
            f"{data if good else err}"
        )
        ok &= good

    return ok


async def check_siren(env: dict[str, str], names: list[str]) -> bool:
    print("\n== 6. Siren (set_siren) — interactive, D-16 ==")
    caps_by_name = await _capabilities_by_camera(env, names)
    siren_name = next(
        (name for name in names if caps_by_name.get(name, {}).get("siren")), None
    )
    if siren_name is None:
        print("  [SKIP] no siren-capable camera configured")
        return True

    print(
        f"\nAbout to sound the siren on {siren_name} for ~2s. This produces a "
        f"REAL AUDIBLE SOUND."
    )
    input("Press Enter when you are ready and present to hear it (Ctrl+C to abort)...")

    async def call_sound(session):
        args = {"camera": siren_name, "action": "sound", "duration": 2}
        return await session.call_tool("set_siren", args)

    start = time.monotonic()
    try:
        data, err = await _call_tool(env, call_sound, "set_siren")
    finally:
        elapsed = time.monotonic() - start

        async def call_stop(session):
            args = {"camera": siren_name, "action": "stop"}
            return await session.call_tool("set_siren", args)

        try:
            await with_session(env, call_stop)
        except Exception as exc:  # noqa: BLE001 — best-effort safety stop
            print(f"  [WARNING] failed to send siren stop command: {exc}")

    if err:
        print(f"  [{FAIL}] {siren_name}: {err}")
        return False
    del data  # sound's own read-back is echo-only (no live siren-state getter)

    print(f"  [info] set_siren API round-trip took {elapsed:.2f}s")

    perceived_raw = input(
        "Roughly how many seconds did the siren actually sound for? (enter a number): "
    )
    try:
        perceived = float(perceived_raw)
    except ValueError:
        print(
            f"  [info] could not parse {perceived_raw!r} as a number — "
            f"skipping duration comparison"
        )
        perceived = None

    if perceived is not None:
        print(f"  [info] requested 2s, operator reported {perceived}s")
        if perceived > 2 * 2:
            print(
                f"  [WARNING] perceived siren duration ({perceived}s) is more than "
                f"double the requested 2s — may confirm reolink-aio's internal "
                f"'times * 5' bookkeeping means duration is NOT literal seconds "
                f"(Pitfall 1/Assumption A1) — flag for launch review"
            )

    print(f"  [{PASS}] {siren_name}: siren check complete, siren stopped")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    env = load_env()
    names = preflight(env)
    print(f"Cameras under test: {', '.join(names)}")
    print(f"Config: {resolve_config_path()}")

    results: dict[str, bool] = {}
    results["spotlight"] = await check_spotlight(env, names)
    results["ir lights"] = await check_ir_lights(env, names)
    results["white led"] = await check_white_led(env, names)
    results["zoom"] = await check_zoom(env, names)
    results["ptz"] = await check_ptz(env, names)
    results["siren"] = await check_siren(env, names)  # LAST per D-16

    print("\n== Summary ==")
    for check, good in results.items():
        print(f"  [{PASS if good else FAIL}] {check}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
