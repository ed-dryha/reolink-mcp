# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — MVP

**Shipped:** 2026-07-11
**Phases:** 4 | **Plans:** 17

### What Was Built
- Standalone Reolink MCP server: 16 tools (6 observe, 10 control), capability-gated, annotated, read-only mode — published to PyPI + MCP Registry as `reolink-mcp` 1.0.0
- Config/session layer proven safe against the project's biggest field risk: env-var-only secrets with redaction on every failure path, and session coexistence with `surveillance-security-ai` on shared cameras
- Hardware-free CI (9-job matrix + 3-OS packaging smoke) and a tag-driven OIDC publish pipeline with TestPyPI rehearsal

### What Worked
- MVP vertical-slice roadmap: Phase 1 delivered the core value end-to-end (config → connect → snapshot in a real MCP client against real hardware), so every later phase built on a proven spine
- Front-loading the riskiest constraint (shared-camera session limit, HDWR-03) into Phase 1 — it never bit again for the rest of the milestone
- Verification loop with adversarial re-review caught two real security leaks (CR-01 twice: `load_settings()`, then its sibling `validate_yaml_shape()`) that plan execution had missed
- Live-hardware checkpoints surfaced issues mocks never would: silent siren (→ `set_audio_alarm` tool added mid-checkpoint), `get_device_info` serials None on real standalone cameras

### What Was Inefficient
- The Phase 1 CR-01 gap-closure fixed one of two structurally identical leak paths and the phase was marked complete with `gaps_found` still open — the remaining leak sat unresolved through Phases 2–4 and shipped in v1.0.0, only closed at milestone close. Gap closure should grep for the *pattern*, not just the reported line
- Phase 2 decision-coverage gate was overridden ("Proceed anyway") for 7 uncited decisions; plan-checker re-verified them, but the override created follow-up audit work
- REQUIREMENTS.md checkbox/traceability bookkeeping drifted from reality (only Phase 2 rows ever marked Complete) — reconciliation had to happen wholesale at milestone close

### Patterns Established
- Named-map config (`dict[str, CameraConfig]`) + `env_nested_delimiter="__"` for env-var secret overlay
- Error messages from `ValidationError.errors()` loc/type only — never `str(e)` (pydantic v2 embeds plaintext `input_value`)
- Curated error taxonomy via `classify_reolink_error` matcher table; every tool failure names the camera and likely cause
- Capability gating helper (`capabilities.py`) consulted by every control tool before touching the camera (CTRL-10)
- Registry-wide regression tests for cross-cutting guarantees (annotation completeness, read-only stripping) instead of per-tool asserts
- Per-phase QA harness scripts (`scripts/qa_phaseN.py`) for structured live-hardware checkpoints

### Key Lessons
1. When a security fix closes a leak, search the codebase for the same pattern before declaring it closed — the second CR-01 leak was a structurally identical sibling one function away
2. Don't close a milestone (or publish a release) with a `gaps_found` verification open; the v1.0.0 wheel shipped with a known Critical that a pre-release audit gate would have caught
3. Real-hardware checkpoints are irreplaceable: three field-only defects (silent siren, None serials, session behavior) were invisible to a 186-test mock suite
4. Keep requirement bookkeeping in the phase-completion loop, not the milestone-close loop — wholesale reconciliation at close loses the per-phase evidence trail

### Cost Observations
- Model profile: quality (per .planning/config.json)
- Notable: empty repo → published PyPI package in 4 days / 103 commits, with live hardware validation in three of four phases

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1.0 | 4 | 17 | Baseline: MVP vertical slices, live-hardware checkpoints, adversarial verification |

### Cumulative Quality

| Milestone | Tests | Zero-Dep Additions |
|-----------|-------|--------------------|
| v1.0 | 186 | Pillow (planned), otherwise stack-as-researched |

### Top Lessons (Verified Across Milestones)

1. (Single milestone so far — candidates: pattern-wide gap closure; no release with open `gaps_found`; hardware checkpoints catch what mocks cannot)
