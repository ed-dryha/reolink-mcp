---
phase: 04-packaging-release
plan: 01
subsystem: infra
tags: [github-actions, ci, packaging, uv, ruff, pytest, oidc-adjacent-supply-chain]

# Dependency graph
requires:
  - phase: 03-safe-controls-safety-rails
    provides: full 16-tool registry, 181-test hermetic suite (tests/conftest.py collection-time stub), read-only mode, curated SystemExit error philosophy (config.py::load_settings)
provides:
  - .github/workflows/ci.yml -- PR-triggered 9-job lint+test matrix (3 Python x 3 OS) plus a 3-OS packaging-smoke job
  - scripts/packaging_smoke.py -- standalone, no-pytest, no-reolink_mcp-import assertion script proving the installed wheel's entry point fails loudly and cleanly with no config
affects: [04-02, 04-03, 04-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "GitHub Actions pinned to resolved 40-char commit SHAs (not mutable tags), re-resolved via git ls-remote --tags at execution time rather than copied from RESEARCH.md"
    - "packaging-smoke job forces shell: bash on every run: step so dist/*.whl glob expansion is consistent across ubuntu/macos/windows-latest runners"
    - "packaging_smoke.py is a standalone assertion script (no pytest, no reolink_mcp import) designed for reuse via uv run --isolated --no-project --with dist/*.whl, so it works identically in ci.yml and (per 04-03-PLAN.md) release.yml"

key-files:
  created:
    - scripts/packaging_smoke.py
    - .github/workflows/ci.yml
  modified: []

key-decisions:
  - "Resolved current commit SHAs at execution time via git ls-remote --tags (actions/checkout@9c091bb.. # v7.0.0, astral-sh/setup-uv@f98e069.. # v8.3.2) instead of reusing the (potentially stale) SHAs quoted in 04-RESEARCH.md, per the plan's explicit instruction"
  - "packaging-smoke job runs on all 3 OSes with Python 3.13 fixed (no python-version axis) -- matches 04-RESEARCH.md Open Question 1's recommendation: catch OS-specific entry-point/glob breakage without 9x redundant OS-independent wheel builds"

patterns-established:
  - "Packaging smoke check asserts exactly: no TimeoutExpired, returncode != 0, stdout == '', 'config error:' in stderr -- never exit-code-0 (Pitfall 4, 04-RESEARCH.md); this is the contract any future release-pipeline smoke check must preserve"

requirements-completed: [REL-03]

# Metrics
duration: 5min
completed: 2026-07-11
---

# Phase 04 Plan 01: PR-Triggered CI Pipeline Summary

**First GitHub Actions workflow in the repo: a 9-job OS x Python lint/test matrix plus a 3-OS packaging-smoke job that proves the installed wheel's entry point fails loudly (not silently, not with a traceback) when no config is present.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-07-11T15:41:00Z
- **Completed:** 2026-07-11T15:46:22Z
- **Tasks:** 2
- **Files modified:** 2 (both new)

## Accomplishments
- `scripts/packaging_smoke.py`: standalone script (no pytest, no `reolink_mcp` import) that runs the installed `reolink-mcp` console-script entry point in an isolated `HOME`/`USERPROFILE` with `RMCP_CONFIG_FILE` stripped, and asserts the four required behaviors from Pitfall 4 (no hang, non-zero exit, empty stdout, curated `config error:` prefix on stderr)
- `.github/workflows/ci.yml`: `pull_request`-only trigger (never `pull_request_target`); job `test` runs the full 3x3 matrix (`ubuntu/macos/windows-latest` x `3.11/3.12/3.13`, `fail-fast: false`) doing `uv sync --locked --dev` -> `uv run ruff check src/ tests/ scripts/` -> `uv run pytest tests/ -q`; job `packaging-smoke` runs the same wheel-build-and-smoke-test on all 3 OSes (Python 3.13 fixed, no python axis) with `shell: bash` explicit on every `run:` step
- Verified live: `uv build` + `uv run --isolated --no-project --with dist/*.whl python scripts/packaging_smoke.py` exits 0; `uv run ruff check` and `uv run pytest tests/ -q` (the exact commands `ci.yml` runs) both pass locally (181 tests passed, ruff clean)

## Task Commits

Each task was committed atomically:

1. **Task 1: Write scripts/packaging_smoke.py** - `664f35f` (feat)
2. **Task 2: Write .github/workflows/ci.yml** - `70b46ff` (feat)

**Plan metadata:** (this commit, `docs(04-01)`)

## Files Created/Modified
- `scripts/packaging_smoke.py` - isolated-env subprocess smoke check against the installed `reolink-mcp` entry point; T201-exempt per `pyproject.toml`'s `scripts/*` per-file-ignore
- `.github/workflows/ci.yml` - PR-triggered `test` (9-job matrix) and `packaging-smoke` (3-job, per-OS) jobs

## Decisions Made
- Re-resolved `actions/checkout` and `astral-sh/setup-uv` commit SHAs live via `git ls-remote --tags` rather than trusting the SHAs quoted in `04-RESEARCH.md` (which the plan explicitly flagged as possibly stale) -- landed on `actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0` and `astral-sh/setup-uv@f98e06938123ccabd21905ea5d0069192241f9f1 # v8.3.2`, both newer than the versions cited in research
- Kept the packaging-smoke job's matrix to OS-only (3 jobs, Python 3.13 fixed) per the plan's Task 2 instructions and 04-RESEARCH.md's Open Question 1 recommendation, rather than running the smoke check across all 9 combinations

## Deviations from Plan

None - plan executed exactly as written. Both tasks' automated verification commands and acceptance-criteria greps passed on the first attempt after one minor local fix (see below), which stayed within Task 1's own verification loop (not a cross-task deviation).

Note: during Task 1's own verify step, `uv run ruff check scripts/packaging_smoke.py` initially failed on `E501` (one line at 105 chars, over the 88-char limit implied by ruff's default line-length with `select = ["E", "F", "I", "T201"]`). Fixed by reformatting the `_fail()` signature across multiple lines before recording the task as done -- this is normal task-internal iteration (write code, verify, fix), not a plan deviation requiring a Rule 1-4 classification, since it never left Task 1's own acceptance-criteria loop and no plan text needed to change.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. This plan adds no new runtime/dev dependencies (confirmed by 04-RESEARCH.md's Package Legitimacy Audit) and both new files are CI-only infrastructure with no local environment changes needed beyond what `uv sync` already provides.

## Next Phase Readiness
- `.github/workflows/ci.yml` and `scripts/packaging_smoke.py` exist, committed, and verified locally (ruff clean, 181 tests passing, packaging smoke exits 0 against a real build wheel)
- `scripts/packaging_smoke.py` is designed for unmodified reuse by 04-03-PLAN.md's `release.yml` (per the plan's `<objective>` and `04-PATTERNS.md`'s "Packaging smoke check" analysis) -- no changes anticipated
- Live GitHub Actions execution of the matrix (the only way to fully prove all 9 jobs pass on real runners) is deferred to 04-04-PLAN.md, after the repo is pushed, per this plan's `<verification>` section
- No blockers for 04-02 (packaging/release metadata: `pyproject.toml` version bump, `LICENSE`, `README.md`, `CHANGELOG.md`, `config.example.yaml`) or 04-03 (`release.yml`, `server.json`)

---
*Phase: 04-packaging-release*
*Completed: 2026-07-11*

## Self-Check: PASSED

- FOUND: scripts/packaging_smoke.py
- FOUND: .github/workflows/ci.yml
- FOUND: .planning/phases/04-packaging-release/04-01-SUMMARY.md
- FOUND commit: 664f35f (Task 1)
- FOUND commit: 70b46ff (Task 2)
- FOUND commit: f21c524 (plan metadata)
