---
phase: 04-packaging-release
plan: 03
subsystem: infra
tags: [pypi, uv, github-actions, oidc, mcp-registry, changelog, release-engineering]

# Dependency graph
requires:
  - phase: 04-packaging-release (04-01)
    provides: ci.yml's packaging_smoke.py check, astral-sh/setup-uv SHA-pin convention
provides:
  - "pyproject.toml with complete PyPI 1.0.0 metadata (license, readme, classifiers, keywords, urls, testpypi index)"
  - "CHANGELOG.md (Keep a Changelog format) with an Unreleased heading and a dated 1.0.0 Added section"
  - "server.json MCP Registry manifest (io.github.ed-dryha/reolink-mcp)"
  - ".github/workflows/release.yml -- tag-driven, OIDC-only, 4-job release pipeline"
affects: [04-04 (human-guided release execution depends on this pipeline existing and being correct)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "PEP 639 SPDX license string (license = \"MIT\") instead of the deprecated License :: classifier"
    - "Tag-driven release workflow gated by an is_rehearsal job output computed from a -rc substring match on github.ref_name"
    - "Hand-rolled awk CHANGELOG.md section extraction instead of a third-party marketplace action (avoids the [SUS]-flagged ffurrer2/extract-release-notes)"
    - "Per-job least-privilege permissions blocks, never a workflow-level grant"

key-files:
  created: [CHANGELOG.md, server.json, .github/workflows/release.yml]
  modified: [pyproject.toml, uv.lock]

key-decisions:
  - "Resolved a fresh astral-sh/setup-uv SHA (v8.3.2, one release ahead of ci.yml's v8.3.1 pin) rather than copy-pasting ci.yml's pin, per the plan's explicit 'do not reuse a stale SHA' instruction"
  - "Used environment: ${{ needs.build.outputs.is_rehearsal == 'true' && 'testpypi' || 'pypi' }} as a bare-string job-level expression (GitHub Actions supports this form) to select the publish target from the single release.yml file, avoiding two near-duplicate workflows (RESEARCH.md Open Question 3)"
  - "Added --trusted-publishing always to both uv publish invocations per RESEARCH.md's explicit anti-pattern warning -- forces a hard failure instead of a silent token fallback if OIDC is ever misconfigured"

patterns-established:
  - "Release workflow never rebuilds what it publishes: build job's dist/ artifact is uploaded once and downloaded by publish-pypi, so the exact bits that passed the packaging smoke check are the exact bits published"

requirements-completed: [REL-01, REL-02]

# Metrics
duration: 12min
completed: 2026-07-11
---

# Phase 4 Plan 3: Release Metadata & Pipeline Summary

**pyproject.toml carries verified 1.0.0 PyPI metadata (MIT SPDX license, edrygha@gmail.com identity, ed-dryha/reolink-mcp URLs), CHANGELOG.md and server.json exist and cross-validate against it, and a new tag-driven release.yml builds once, smoke-tests, then publishes to PyPI/TestPyPI (OIDC, no stored tokens) before cutting a GitHub Release and re-publishing the MCP Registry manifest.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-07-11T18:52:00+03:00
- **Completed:** 2026-07-11T18:58:37+03:00
- **Tasks:** 3 (+ 1 auto-fixed blocking issue)
- **Files modified:** 5 (pyproject.toml, uv.lock, CHANGELOG.md, server.json, .github/workflows/release.yml)

## Accomplishments
- Closed the three empirically-verified `pyproject.toml` metadata gaps (D-01/D-05/D-06/D-07): a real built wheel's `METADATA` now shows `Version: 1.0.0`, `License-Expression: MIT`, `Author-email: Eduard Dryha <edrygha@gmail.com>`, zero `epam.com` references, and a full `[project.urls]` table
- Added `CHANGELOG.md` (Keep a Changelog) and `server.json` (MCP Registry manifest), with `server.json`'s `description` field verified byte-for-byte identical to `pyproject.toml`'s
- Built `.github/workflows/release.yml`: a 4-job (`build` -> `publish-pypi` -> `github-release` + `publish-registry`), tag-driven (`v*`), OIDC-only pipeline with least-privilege permissions split exactly as specified

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix pyproject.toml release metadata** - `0a870ed` (feat)
2. **Task 2: Write CHANGELOG.md and server.json** - `0facdd6` (feat)
3. **Task 3: Write .github/workflows/release.yml** - `72c521b` (feat)
4. **Auto-fix: sync uv.lock with version bump** - `74ea8e1` (fix, Rule 1)

_This plan had no `checkpoint:*` tasks and no `type="tdd"` gate -- all tasks were `type="auto"`._

## Files Created/Modified
- `pyproject.toml` - version 1.0.0, readme, PEP 639 `license = "MIT"`, `edrygha@gmail.com` author, keywords, classifiers, `[project.urls]`, explicit-only `[[tool.uv.index]]` testpypi block
- `CHANGELOG.md` - Keep a Changelog format: `## [Unreleased]` + dated `## [1.0.0] - 2026-07-11` with an `### Added` section covering all four completed phases
- `server.json` - MCP Registry manifest for `io.github.ed-dryha/reolink-mcp`, `packages[0]` pointing at PyPI `reolink-mcp`, `environmentVariables` limited to `RMCP_CONFIG_FILE`/`RMCP_READ_ONLY`
- `.github/workflows/release.yml` - new tag-driven release pipeline (build/publish-pypi/github-release/publish-registry)
- `uv.lock` - regenerated to match the `pyproject.toml` version bump (Rule 1 auto-fix, see below)

## Decisions Made
- Resolved a fresh `astral-sh/setup-uv` SHA (`11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2`) for `release.yml` rather than reusing `ci.yml`'s slightly older `v8.3.1` pin, per the plan's explicit instruction not to copy a potentially stale SHA. `actions/checkout`'s pin (`v7.0.0`) was re-verified as still current and reused as-is. `actions/upload-artifact` (`v7.0.1`) and `actions/download-artifact` (`v8.0.1`) SHAs were freshly resolved via `git ls-remote --tags` (network available in this environment).
- Used a single parameterized `release.yml` (job-level `environment: ${{ needs.build.outputs.is_rehearsal == 'true' && 'testpypi' || 'pypi' }}` expression) instead of two near-duplicate workflow files, resolving RESEARCH.md's Open Question 3 in favor of minimal duplication while still exercising the real release path for TestPyPI rehearsals.
- Added `--trusted-publishing always` to both `uv publish` invocations (RESEARCH.md's explicit anti-pattern guidance: force a hard failure rather than a silent token fallback).
- Hand-rolled the CHANGELOG-to-release-notes extraction with a 5-line `awk` script (tested locally against the real `CHANGELOG.md`, correctly stops at the next `## [` heading or a trailing reference-link line) rather than using the `[SUS]`-flagged `ffurrer2/extract-release-notes` marketplace action, per the threat model's `T-04-SC` disposition.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] uv.lock left stale by the pyproject.toml version bump**
- **Found during:** Post-Task-3 verification (running the full `uv sync --locked --dev` / ruff / pytest suite to confirm nothing broke)
- **Issue:** Task 1's `version = "0.1.0"` -> `"1.0.0"` edit changed `pyproject.toml`, but `uv.lock`'s self-referential `reolink-mcp` package entry still recorded `version = "0.1.0"`. Both `ci.yml` (`uv sync --locked --dev`) and `release.yml` (`uv build`, which also re-resolves and would drift) depend on the lockfile being in sync; `uv sync --locked --dev` printed "The lockfile at `uv.lock` needs to be updated, but `--locked` was provided" -- this would have failed on the very next PR CI run.
- **Fix:** Ran `uv lock` to regenerate `uv.lock`; the only diff was the `reolink-mcp` entry's `version` field (`0.1.0` -> `1.0.0`). Re-ran `uv sync --locked --dev` from a fresh `.venv` to confirm it now succeeds cleanly with no warning.
- **Files modified:** `uv.lock`
- **Verification:** `uv sync --locked --dev` exits 0 with no lockfile-staleness warning; `uv run ruff check src/ tests/ scripts/` -> `All checks passed!`; `uv run pytest tests/ -q` -> `181 passed`.
- **Committed in:** `74ea8e1` (separate commit, after all 3 planned tasks)

---

**Total deviations:** 1 auto-fixed (1 blocking/Rule 1)
**Impact on plan:** Necessary for CI/release correctness -- without it, the very next PR or tag push would fail at the `uv sync --locked` / `uv build` step. No scope creep; single-field lockfile regeneration only.

## Issues Encountered
None beyond the auto-fixed deviation above.

## Automated Verification Results

- **Task 1:** `uv build --wheel` + wheel `METADATA` inspection -> `METADATA_OK` (Version: 1.0.0, License-Expression: MIT, Author-email edrygha@gmail.com, no epam.com)
- **Task 2:** `python3 -m json.tool server.json` -> valid; `io.github.ed-dryha/reolink-mcp` and `"version": "1.0.0"` present; no `RMCP_CAMERAS` entry; `## [1.0.0]` and `## [Unreleased]` both present in CHANGELOG.md -> `CHANGELOG_SERVER_OK`. Additionally verified (beyond the plan's literal grep check) that `server.json`'s `description` matches `pyproject.toml`'s `description` field byte-for-byte via a Python script -> `DESCRIPTION_MATCH_OK`
- **Task 3:** grep-based structural checks (trigger, permission counts, `uv publish`/`mcp-publisher`/`github-oidc`/`gh release create`/`is_rehearsal`/`packaging_smoke.py` presence, absence of any PyPI token env var or `ffurrer2`) -> `RELEASE_YML_OK`. Additionally validated: `python3 -c "import yaml; yaml.safe_load(...)"` -> `YAML_VALID`; permission block audit confirms build=contents:read only, publish-pypi/publish-registry=id-token:write+contents:read (2x), github-release=contents:write only (1x), no job has both id-token:write and contents:write, no workflow-level `permissions:` block
- **Cross-cutting:** grepped all four touched files for `epam.com`, `edrygka`, and the deprecated `License :: OSI` classifier -- none found
- **Regression:** `uv run ruff check src/ tests/ scripts/` -> all checks passed; `uv run pytest tests/ -q` -> 181 passed; the reused `scripts/packaging_smoke.py` re-run against a freshly built 1.0.0 wheel -> `packaging smoke OK: entry point failed loudly and cleanly with no config`

## Known Stubs

None. No placeholder values, hardcoded empty data, or unwired components were introduced -- this plan is entirely static release-engineering configuration (metadata files + a CI workflow YAML), not application code.

## Threat Flags

None. Every file touched in this plan (`pyproject.toml`, `CHANGELOG.md`, `server.json`, `.github/workflows/release.yml`, `uv.lock`) was explicitly covered by the plan's `<threat_model>` STRIDE register (T-04-06, T-04-07, T-04-08, T-04-SC) or is inert static metadata with no new network endpoint, auth path, file-access pattern, or schema change at a trust boundary.

## User Setup Required

None yet from this plan directly -- `release.yml` cannot succeed on a real tag push until the PyPI/TestPyPI "pending trusted publisher" is configured in the browser (RESEARCH.md Pitfall 2) and the `pypi`/`testpypi` GitHub Environments exist with matching names. That manual, human-in-the-loop setup is explicitly scoped to **04-04-PLAN.md** (human-guided release execution), not this plan.

## Next Phase Readiness
- All four artifacts this plan owns (`pyproject.toml`, `CHANGELOG.md`, `server.json`, `release.yml`) exist, are committed, and pass every automated verification the plan specified plus the additional cross-checks (description match, YAML validity, identity-string grep, full ruff+pytest regression, live packaging-smoke re-run).
- `release.yml` is ready for 04-04 to exercise for real: pushing a `v1.0.0-rc1`-style tag routes to the `testpypi` environment/index (rehearsal, skips GitHub Release and registry publish); pushing `v1.0.0` routes to the real `pypi` environment and runs all four jobs.
- Blocker for 04-04 (expected, not a defect here): the PyPI and TestPyPI "pending trusted publisher" entries do not exist yet -- the first tag push will fail at the `uv publish` step until a human configures them on pypi.org/test.pypi.org, and until the `pypi`/`testpypi` GitHub Environments are created in repo settings with those exact names.

---
*Phase: 04-packaging-release*
*Completed: 2026-07-11*

## Self-Check: PASSED

All created/modified files confirmed present on disk (pyproject.toml, CHANGELOG.md, server.json, .github/workflows/release.yml, uv.lock, this SUMMARY.md). All 5 commits confirmed present in `git log --oneline --all` (0a870ed, 0facdd6, 72c521b, 74ea8e1, 7bc58e5).
