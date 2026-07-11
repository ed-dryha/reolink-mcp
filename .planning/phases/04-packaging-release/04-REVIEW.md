---
phase: 04-packaging-release
reviewed: 2026-07-11T17:05:55Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - .github/workflows/ci.yml
  - .github/workflows/registry-publish.yml
  - .github/workflows/release.yml
  - CHANGELOG.md
  - README.md
  - config.example.yaml
  - pyproject.toml
  - scripts/packaging_smoke.py
  - server.json
findings:
  critical: 1
  warning: 6
  info: 4
  total: 11
status: issues_found
---

# Phase 04: Code Review Report

**Reviewed:** 2026-07-11T17:05:55Z
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Reviewed the packaging & release surface: two publish workflows, CI, PyPI
metadata, the MCP Registry manifest, user-facing docs, and the packaging smoke
script. The smoke script's contract was cross-checked against the actual
implementation (`src/reolink_mcp/config.py` raises `SystemExit("config error:
...")` before any transport start; `Path.home()` honors the script's
`HOME`/`USERPROFILE` override on all three CI OSes) and holds up. Action SHA
pins were resolved against upstream tags — one pin comment is factually wrong.

The headline problems are release-pipeline gates, not shipped-code behavior
(consistent with the context note that v1.0.0 is already out and findings
matter for future releases): the tag-triggered release path publishes
irrevocably to PyPI without ever running the test suite, nothing validates
that the tag, `pyproject.toml`, and `server.json` versions agree, and the
rehearsal classifier is a fragile substring match that routes most prerelease
tag conventions straight to production PyPI.

The intentionally checkout-free `publish-pypi` job and the intentional
`workflow_dispatch` re-publish path in `registry-publish.yml` were treated as
design decisions per the review brief and are not flagged.

## Critical Issues

### CR-01: Tagged releases publish irrevocably to PyPI with no test or lint gate

**File:** `.github/workflows/release.yml:9-63`, `.github/workflows/ci.yml:3-4`
**Issue:** `release.yml` fires on any `v*` tag push and its `build` job runs
only `uv build` plus the packaging smoke check — never `pytest` or `ruff`.
Meanwhile `ci.yml` triggers on `pull_request` **only** (ci.yml:3-4): it has no
`push` trigger for `main` or for tags. The repo's own history shows commits
landing directly on `main` (recent `fix(03)`/`chore(03)` commits are direct
pushes), so those commits never run CI anywhere. Net effect: a future tag can
publish a package whose unit tests fail — and PyPI versions are immutable
(yank-only; the version number is burned). `github-release` and
`publish-registry` then propagate the untested release further.
**Fix:** Gate publishing on the test suite. Either add the test matrix (or a
single-OS test job) to `release.yml` as a `needs` prerequisite of
`publish-pypi`, or at minimum run tests in the `build` job:

```yaml
# release.yml, build job, before "uv build"
- run: uv sync --locked --dev
- run: uv run ruff check src/ tests/ scripts/
- run: uv run pytest tests/ -q
```

Additionally extend `ci.yml` triggers so main-branch commits are tested:

```yaml
on:
  pull_request:
  push:
    branches: [main]
```

## Warnings

### WR-01: No consistency check between git tag, `pyproject.toml` version, and `server.json` versions

**File:** `.github/workflows/release.yml:21,97-116`; `pyproject.toml:3`; `server.json:9,14`
**Issue:** The release version lives in four places that must agree: the tag,
`pyproject.toml` `version`, and **two** fields in `server.json` (`version` and
`packages[0].version`). The workflow validates only the CHANGELOG heading
(release.yml:86-89), and that check runs *after* `publish-pypi` has already
succeeded. Failure modes for the next release: (a) tag `v1.0.1` with
`pyproject.toml` still at `1.0.0` → `uv publish` fails on the duplicate file
only after the artifact is built, or worse silently publishes `1.0.0` content
under a `v1.0.1` tag on TestPyPI rehearsals; (b) `server.json` left at
`1.0.0` → `publish-registry` fails on the duplicate registry version (or
publishes stale metadata) *after* PyPI and the GitHub Release are already
done, leaving a partial release that needs the manual
`registry-publish.yml` escape hatch.
**Fix:** Fail fast in the `build` job before anything is published:

```bash
version="${GITHUB_REF_NAME#v}"; version="${version%%-*}"
py_ver=$(uv run --no-project python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
sj_ver=$(uv run --no-project python -c "import json;d=json.load(open('server.json'));print(d['version'],d['packages'][0]['version'])")
[ "$py_ver" = "$version" ] && [ "$sj_ver" = "$version $version" ] || { echo "version skew: tag=$version pyproject=$py_ver server.json=($sj_ver)" >&2; exit 1; }
```

### WR-02: Rehearsal classifier `*-rc*` misroutes most prerelease tag formats to production PyPI

**File:** `.github/workflows/release.yml:36-40`
**Issue:** `[[ "$REF_NAME" == *-rc* ]]` is an anywhere-substring match on one
specific spelling. PEP 440's canonical prerelease form has **no hyphen**
(`1.1.0rc1`), so a tag `v1.1.0rc1` — the spelling anyone matching
`pyproject.toml`'s version string would naturally use — classifies as a real
release. So do `v1.1.0-beta1`, `v2.0.0-alpha.1`, and `v1.1.0-dev1`. All of
these then publish irrevocably to production PyPI, create a GitHub Release,
and publish to the MCP Registry. The CHANGELOG gate (release.yml:86-89) would
likely fail for such a tag, but only *after* the PyPI publish already
happened — burning the version number and leaving a partial release.
**Fix:** Treat *any* non-final-looking tag as a rehearsal (fail closed), e.g.:

```bash
if [[ "$REF_NAME" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "is_rehearsal=false" >> "$GITHUB_OUTPUT"
else
  echo "is_rehearsal=true" >> "$GITHUB_OUTPUT"
fi
```

### WR-03: `mcp-publisher` fetched from unpinned `releases/latest` with no checksum, executed with `id-token: write`

**File:** `.github/workflows/release.yml:107-110`; `.github/workflows/registry-publish.yml:15-18`
**Issue:** Both workflows `curl -L .../releases/latest/download/... | tar xz`
an arbitrary binary and execute it in a job holding an OIDC `id-token: write`
credential that authorizes publishing to the MCP Registry as this repo. Every
other dependency in these workflows is SHA-pinned; this one floats to
whatever upstream last released — a compromised or simply breaking
`mcp-publisher` release runs with publish-capable credentials on the next
tag. The `curl` invocation also lacks `-f`/`--fail`, so an HTTP 404/500 body
is piped into `tar`, producing a confusing "not a gzip" failure instead of a
clear HTTP error.
**Fix:** Pin a specific release version, verify its published checksum, and
add `-fsSL`:

```bash
VER=1.4.0  # example: pin and bump deliberately
curl -fsSL -o mcp-publisher.tar.gz \
  "https://github.com/modelcontextprotocol/registry/releases/download/v${VER}/mcp-publisher_linux_amd64.tar.gz"
echo "<known-sha256>  mcp-publisher.tar.gz" | sha256sum -c -
tar xzf mcp-publisher.tar.gz mcp-publisher
```

Apply identically in both workflow files (the install block is duplicated —
drift between the two copies is its own hazard).

### WR-04: ci.yml pins setup-uv **v8.3.1** but the comment claims `# v8.3.2` — pin/comment mismatch across workflows

**File:** `.github/workflows/ci.yml:16,32`; `.github/workflows/release.yml:18,55`
**Issue:** Verified against upstream tags: `astral-sh/setup-uv@f98e0693...`
(ci.yml) resolves to tag **v8.3.1**, while `astral-sh/setup-uv@11f9893b...`
(release.yml) is the real **v8.3.2**. Both carry the comment `# v8.3.2`. The
ci.yml comment is false. Version comments on SHA pins are the only
human-auditable signal of what a pin is; a wrong one defeats supply-chain
review (an auditor "confirming v8.3.2" would wave through an arbitrary SHA)
and hides the fact that CI and Release run different action versions.
**Fix:** Update ci.yml lines 16 and 32 to the verified v8.3.2 SHA used in
release.yml:

```yaml
- uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
```

### WR-05: README password instruction is a bare shell assignment — followed verbatim, the server never receives the password

**File:** `README.md:42-44,56-60`
**Issue:** The quickstart's only password-setting instruction is:

```bash
RMCP_CAMERAS__front_door__PASSWORD=<camera-password>
```

A bare `VAR=value` (no `export`, no command on the same line) sets a shell
variable that is **not** passed to any child process — it does literally
nothing for a server later launched by `claude mcp add ... uvx reolink-mcp`.
Even with `export`, it only works if Claude Code is launched from that same
shell session, which the README never states. A user following the Claude
Code path verbatim gets `config error: camera 'front_door' has no password`.
The Claude Desktop JSON example (README.md:66-78) handles this correctly via
its `env` block; the Claude Code path and the generic-stdio path do not.
**Fix:** Use the client's env wiring in the Claude Code example and make the
shell-export caveat explicit:

```bash
claude mcp add reolink \
  --env RMCP_CAMERAS__front_door__PASSWORD=<camera-password> \
  -- uvx reolink-mcp
```

### WR-06: server.json declares no password environment variable — the only required, secret configuration is invisible to registry clients

**File:** `server.json:16-29`
**Issue:** `environmentVariables` lists only `RMCP_CONFIG_FILE` and
`RMCP_READ_ONLY`, both `isRequired: false`, both `isSecret: false`. But the
server cannot start usefully without at least one
`RMCP_CAMERAS__<name>__PASSWORD` secret (config.py exits with "camera has no
password" otherwise). MCP clients and directories that render configuration
UIs from `server.json` will present a server with zero required inputs and
zero secrets — users get a non-functional install with no machine-readable
hint that a secret env var is needed, and no `isSecret` masking for the value
they eventually do supply. The per-camera dynamic name makes a fully static
declaration impossible, but the manifest currently doesn't acknowledge the
requirement at all.
**Fix:** Declare a templated entry using the schema's variable substitution
(supported by the 2025-12-11 schema for named inputs), e.g.:

```json
{
  "name": "RMCP_CAMERAS__{camera_name}__PASSWORD",
  "description": "Camera admin password; one variable per camera key in the config YAML",
  "isRequired": true,
  "isSecret": true,
  "variables": {
    "camera_name": {
      "description": "Camera key from config.yaml (lowercase snake_case)",
      "isRequired": true
    }
  }
}
```

If the registry's validator rejects templated env names, at minimum state the
requirement in the `RMCP_CONFIG_FILE` description so it surfaces in client
UIs.

## Info

### IN-01: CHANGELOG says "nine control tools" but lists ten

**File:** `CHANGELOG.md:23-27`
**Issue:** The 1.0.0 entry reads "nine control tools" and then enumerates
ten: `set_siren`, `set_audio_alarm`, `set_spotlight`, `set_ir_lights`,
`set_white_led`, `set_zoom`, `list_presets`, `ptz_move_to_preset`,
`ptz_position`, `ptz_guard`. README.md:120 correctly says "all 10 control
tools".
**Fix:** Change "nine" to "ten" in the `[Unreleased]`-side history (the 1.0.0
entry text is already published; correct it in-place per Keep-a-Changelog
practice since the file is the source of future release notes).

### IN-02: `pillow` is the only runtime dependency with no version constraint

**File:** `pyproject.toml:23`
**Issue:** `mcp`, `reolink-aio`, and `pydantic-settings` all carry bounds per
the documented stack policy; `pillow` has none. End users install via `uvx`,
which resolves fresh with no lockfile protection — a future Pillow major
release with a breaking `Image` API change breaks every new `uvx reolink-mcp`
invocation overnight.
**Fix:** Bound it, e.g. `"pillow>=11,<13"` (or at least a floor matching the
tested version).

### IN-03: `packaging_smoke.py` leaks its temp directory and carries unreachable `return` statements

**File:** `scripts/packaging_smoke.py:29-42,48-51`
**Issue:** (a) `tempfile.mkdtemp(...)` at line 51 is never removed — harmless
on ephemeral CI runners, but the script is also run locally per its docstring
and accumulates `reolink-mcp-packaging-smoke-*` dirs. (b) `_fail()` always
calls `sys.exit(1)` but is annotated `-> None`, forcing dead `return`
statements after every call site (lines 49, 66, 77, 87, 97).
**Fix:** Wrap the body in `try/finally: shutil.rmtree(isolated_home,
ignore_errors=True)`, and annotate `_fail` as `-> NoReturn`
(`typing.NoReturn`) so the trailing `return`s can be deleted.

### IN-04: dist artifact is uploaded before the packaging smoke check runs

**File:** `.github/workflows/release.yml:23-29`
**Issue:** `upload-artifact` (line 23) precedes the smoke check (line 27), so
a build that fails the smoke check still leaves its `dist` artifact attached
to the failed run, retained and downloadable — a footgun for anyone tempted
to publish it manually. Publishing is safe today only because `publish-pypi`
`needs: build`.
**Fix:** Reorder: run the smoke check immediately after `uv build`, then
upload the artifact.

---

_Reviewed: 2026-07-11T17:05:55Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
