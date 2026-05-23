# Changelog — Erdős Problem #128

All notable changes to this project are recorded here. Newest entries on top.
Versions follow simple `0.x` numbering during the research phases.

Format: each entry is dated (YYYY-MM-DD) and grouped by phase.

---

## [0.1.0] — 2026-05-23 — Phase 0: Setup and environment

Initial scaffold of the Erdős-128 counterexample search project.

### Added
- Project directory structure: `src/`, `tests/`, `results/`, `backups/`.
- `src/boilerplate.py` (v0.1.0) — the standard pre-flight prelude every
  long-running script calls: environment check → version note → backup →
  process-management lock. Cross-platform (stdlib only). Also writes an
  append-only audit trail to `results/run_log.jsonl`.
- `tests/test_boilerplate.py` — Phase 0 smoke test (9 tests). Verifies Python
  version gating, dependency imports, backup snapshotting, run-log appending,
  and the "don't run two copies at once" lock. **All 9 pass.**
- `requirements.txt` — dependencies pinned to installed versions:
  networkx 3.6.1, numpy 2.4.6, python-igraph 1.0.0 (+ texttable 1.7.0),
  PuLP 3.3.1, pytest 9.0.3.
- `pytest.ini` — makes the project a self-contained pytest rootdir.
- `.gitignore` — ignores `.venv/`, `backups/*`, caches; keeps `results/`.
- `CLAUDE.md` — project config and conventions for Claude Code sessions.

### Environment / deviations from the original spec
The spec assumed Windows 11 / CMD / Python 3.12 at `E:\the lab\projects\erdos-128\`.
This Phase 0 was executed in a Linux container under the
`cryptorhoeck/project-syndicate` repository (the only repo this session can
access). The following conscious deviations were made and are flagged here per
the spec's "report any deviations" rule:

1. **Location:** built as a self-contained subtree at `erdos-128/` inside the
   existing repo rather than a standalone `cryptorhoeck/erdos-128` repo —
   creating a separate GitHub remote was outside this session's permissions.
   The subtree has its own CLAUDE.md/CHANGELOG.md/requirements/tests and does
   not touch any Project Syndicate files.
2. **OS:** Linux, not Windows. `boilerplate.py` was written cross-platform so it
   runs identically on Windows/Linux/macOS. Commands in docs show both forms.
3. **Python:** 3.11.15 (container default), not 3.12. `MIN_PYTHON` set to (3, 11).
   No 3.12-only features are used.
4. **igraph:** `pip install python-igraph` succeeded directly on Linux — the
   Windows wheel fallback to bare `igraph` was not needed. Import name is `igraph`.

### Notes
- Git: committed locally to the working branch. No standalone remote added (per
  spec, owner reviews before any public push).
- `results/run_log.jsonl` contains entries from the smoke-test run.

---
