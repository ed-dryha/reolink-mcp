---
phase: 04-packaging-release
plan: 02
subsystem: docs
tags: [readme, mcp-registry, documentation, config-example, pydantic-settings]

# Dependency graph
requires:
  - phase: 03-safe-controls-safety-rails
    provides: the full 16-tool registry with readOnlyHint/destructiveHint annotations (tools/__init__.py), siren safety envelope (D-01..D-04), read-only mode behavior (D-15) that this README documents
provides:
  - "README.md rewritten as the public quickstart: mcp-name ownership comment, three copy-paste client configs (Claude Code, Claude Desktop, generic stdio), full 16-tool table, Safety section, Camera compatibility & session-limits section"
  - "config.example.yaml: committed, password-free, schema-valid example camera config with inline env-var documentation"
affects: [04-03-tag-driven-publish-pipeline, 04-04-mcp-registry-and-ci]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "README as the single source of end-user docs (no docs/ folder, D-14)"
    - "mcp-name ownership-verification comment placed in README.md body (invisible HTML comment) so it survives into the PyPI-rendered long description for the MCP Registry's ownership check"

key-files:
  created:
    - config.example.yaml
  modified:
    - README.md

key-decisions:
  - "Reused PROJECT.md's core-value/market-gap prose near-verbatim in the README opening, per CONTEXT.md instruction, rather than re-authoring"
  - "Tool table Purpose column paraphrased from each tool's own docstring first line (observe.py/control.py) rather than re-derived prose, keeping the table from drifting from the code"
  - "No self-referential github.com/ed-dryha/reolink-mcp badge/link was added to README this plan (no badges requested by the plan's task action beyond removing stale ones); the only external links are to reolink-aio and the MCP Python SDK repos, both correct and unrelated to the identity-consistency requirement"

patterns-established:
  - "Env-var-only secrets pattern (RMCP_CAMERAS__<name>__PASSWORD) documented identically in config.example.yaml and README.md quickstart — single source of truth for the pattern's exact casing/delimiter"

requirements-completed: [REL-04]

# Metrics
duration: 6min
completed: 2026-07-11
---

# Phase 4 Plan 2: README Quickstart & config.example.yaml Summary

**Full-rewrite README.md public quickstart (copy-paste Claude Desktop/Code/generic-stdio config, 16-tool table, Safety + Camera-compatibility sections, mcp-name ownership comment) plus a schema-validated, password-free config.example.yaml**

## Performance

- **Duration:** ~6 min (task execution; excludes worktree `.planning` context sync)
- **Started:** 2026-07-11T18:40:00+03:00 (approx.)
- **Completed:** 2026-07-11T18:46:15+03:00
- **Tasks:** 2/2 completed
- **Files modified:** 2 (1 created, 1 rewritten)

## Accomplishments
- `config.example.yaml` added at repo root: two example cameras (`front_door`, `side_yard`), `host`/`username` only, no password key anywhere — round-trips through the production `CameraYamlEntry` model (`extra="forbid"` makes a stray password key a hard validation failure, verified by the plan's automated check).
- `README.md` fully replaced: hidden `<!-- mcp-name: io.github.ed-dryha/reolink-mcp -->` comment (unblocks the MCP Registry's PyPI-ownership check in Plan 04-04), three copy-paste-ready client config blocks (`claude mcp add`, Claude Desktop JSON, generic stdio JSON), a prominent concurrent-session warning callout in the quickstart linking to the fuller compatibility section, the complete 16-tool table sourced from `tools/__init__.py`'s exact annotations, a Safety section (RMCP_READ_ONLY, siren 5s default/60s cap, capability gating), and a Camera compatibility & session limits section with the P437/P320 tested-hardware table and the PTZ-mock-validated note.
- Zero stale identity references (`edrygka`, `eduard_dryha@epam.com`, `PROJECT-SEED`) remain in README.md — verified by grep.

## Task Commits

Each task was committed atomically:

1. **Task 1: Write config.example.yaml** - `c56b40d` (feat)
2. **Task 2: Rewrite README.md** - `bb70471` (docs)

**Plan metadata:** SUMMARY.md commit follows this document (worktree mode — orchestrator handles STATE.md/ROADMAP.md centrally after merge).

## Files Created/Modified
- `config.example.yaml` - Commented, password-free example camera config (2 entries); documents default config path, `RMCP_CONFIG_FILE` override, `RMCP_CAMERAS__<name>__PASSWORD` pattern per camera, and `RMCP_READ_ONLY`
- `README.md` - Full rewrite: title/description, mcp-name comment, quickstart (3 client configs + session-limit warning), 16-tool table, Safety section, Camera compatibility & session-limits section, License

## Decisions Made
- Reused `.planning/PROJECT.md`'s core-value and market-gap prose near-verbatim in the README opening (plan's explicit instruction, avoids drift between the project doc and the public-facing README).
- Tool-table Purpose column text is paraphrased directly from each tool's own docstring first line in `observe.py`/`control.py` rather than freshly authored, so the table and the code can't silently drift apart.
- No new self-referential `github.com/ed-dryha/reolink-mcp` badge or link was added — the plan's constraint ("every GitHub link/badge... must point at ed-dryha") is a defect-prevention check, not a mandate to add new badges; the only external links present (reolink-aio, MCP Python SDK) were already correct and are unrelated to the project's own identity.

## Deviations from Plan

None - plan executed exactly as written. Both tasks' automated verify commands pass with no modification needed to the plan's specified content/structure.

## Issues Encountered

**Worktree `.planning/` seed gap (environment, not plan-related):** This worktree was spawned with only a partial `.planning/` snapshot (missing phase 01 and phase 04 planning docs, and the top-level `PROJECT.md`/`STATE.md`/`config.json`/`REQUIREMENTS.md`/`ROADMAP.md` — `.planning/` is gitignored, so `git worktree add` does not carry it over). Resolved by an additive `rsync --ignore-existing` from the main repo's `.planning/` into the worktree before reading plan context; no files were overwritten, nothing under `src/`/`tests/`/`config.example.yaml`/`README.md` was affected. This is an environment/tooling gap for the orchestrator to address in worktree-spawn setup, not a deviation in this plan's actual deliverables.

## User Setup Required

None - no external service configuration required. (PyPI Trusted Publishing setup, MCP Registry publish, and CI wiring are Plan 04-03/04-04's scope, not this plan's.)

## Next Phase Readiness
- Plan 04-03/04-04 (tag-driven PyPI publish pipeline + MCP Registry listing) can proceed: the `mcp-name` ownership comment is now live in README.md, ready to be read from the rendered PyPI long description once `pyproject.toml` gains `readme = "README.md"` (that field addition is Plan 04-01/04-03's scope).
- `config.example.yaml`'s content is now the single source of truth for the README's inline quickstart snippet — any future change to the example camera names/shape must update both files together (currently in sync: `front_door`/`side_yard`, `host`+`username` only).
- No blockers identified for downstream packaging/CI/registry plans.

---
*Phase: 04-packaging-release*
*Completed: 2026-07-11*
