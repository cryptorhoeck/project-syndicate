# Erdős Problem #128 — CLAUDE.md

Project config and conventions for Claude Code sessions. Read this first.

## What this project is

A search for a **counterexample** to Erdős Problem #128 ($250 bounty, falsifiable).

**The statement.** Let `G` be a graph on `n` vertices such that every induced
subgraph on `≥ ⌊n/2⌋` vertices has more than `n²/50` edges. Must `G` contain a
triangle?

**To win.** Exhibit a single **triangle-free** graph `G` on `n` vertices such
that *every* induced subgraph on `⌊n/2⌋` or more vertices has **strictly more
than `n²/50`** edges. One counterexample = done.

## Search architecture (A + B hybrid)

- **Strategy B (constructive, "generate"):** build candidate graphs from named
  triangle-free families with high *uniform* density (Mycielski, Kneser,
  bipartite blow-ups, generalized Petersen, Cayley constructions). These are the
  quality candidates.
- **Strategy A (verification, "minimize"):** for each candidate, find the
  `⌊n/2⌋`-vertex induced subgraph with the **fewest** edges (local search / ILP
  via PuLP). If even that minimum-edge subset has `> n²/50` edges, the candidate
  is a counterexample.

A alone is too slow (NP-hard per graph); B alone misses the space outside named
families. Together: B generates cheaply, A verifies rigorously.

## Owner & working style

- **Owner:** Andrew (~6 months of dev experience). Write code and comments for
  that level: explain **WHY**, not just what. Prefer clarity over cleverness.
- **Type hints** where they aid understanding; **docstrings on every function**.
- **Comments explain the reasoning** — the trap avoided, the invariant relied on.

## Directory layout

```
erdos-128/
├── CLAUDE.md            ← this file
├── CHANGELOG.md         ← versioned, dated; update with every meaningful change
├── requirements.txt     ← pinned dependency versions
├── pytest.ini           ← makes this dir the self-contained pytest rootdir
├── .gitignore
├── .venv/               ← virtual environment (gitignored; never use global Python)
├── src/
│   └── boilerplate.py   ← standard pre-flight prelude (see below)
├── tests/
│   └── test_boilerplate.py
├── results/
│   └── run_log.jsonl    ← append-only audit trail (committed)
└── backups/             ← timestamped snapshots (contents gitignored)
```

## Non-negotiable discipline

1. **Boilerplate first.** Every long-running script calls
   `run_boilerplate(...)` from `src/boilerplate.py` before real work. It does:
   env check → version note → backup → process-lock, and logs to
   `results/run_log.jsonl`.
2. **Backup before destructive changes.** Use `make_backup([...])` (or the
   `backup_paths=` argument of `run_boilerplate`) to snapshot any file you're
   about to overwrite into `backups/<timestamp>/`.
3. **`__version__` in every module/script.** Bump it when behaviour changes.
4. **Update CHANGELOG.md every phase / meaningful change**, dated.
5. **Use the `.venv`, never global Python.** Recreate it from `requirements.txt`.
6. **Do NOT push to a public/standalone remote without Andrew's explicit
   approval.** Local commits and the working review branch are fine.
7. **Don't touch unrelated files during a focused phase.**

## Environment

- **Target spec:** Windows 11, CMD only, Python 3.12, at `E:\the lab\projects\erdos-128\`.
- **Phase 0 was actually run on:** a Linux container, Python 3.11.15, inside the
  `cryptorhoeck/project-syndicate` repo (only repo this session could access).
  `boilerplate.py` is cross-platform, so both environments work. See CHANGELOG
  Phase 0 for the full deviation list.

### Dependencies (pinned — see requirements.txt)
networkx 3.6.1 · numpy 2.4.6 · python-igraph 1.0.0 (imports as `igraph`;
pulls in texttable 1.7.0) · PuLP 3.3.1 · pytest 9.0.3.

> **igraph gotcha:** the PyPI package is `python-igraph` but you `import igraph`.
> On Linux `pip install python-igraph` worked directly (no fallback needed). If
> the Windows wheel ever fails, try `pip install igraph` and keep the same import.

## Common commands

Linux/macOS (this container):
```bash
# create / use the venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# run the smoke test
.venv/bin/python -m pytest

# run the boilerplate self-check
.venv/bin/python src/boilerplate.py
```

Windows 11 / CMD (Andrew's machine — never PowerShell):
```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe src\boilerplate.py
```

## Status

- **Phase 0 — Setup and environment: COMPLETE.** Structure, venv, deps,
  cross-platform boilerplate, smoke test, docs, local commit.
- **Phase 1 — Generators, verifier, search cores: COMPLETE.**
  - `src/graphs.py` — triangle-free family generators (Mycielski, Kneser,
    generalized Petersen, blow-ups, Cayley/middle-third, complete bipartite).
  - `src/verify.py` — triangle-free + density verifier. **Key reduction:** by the
    monotonicity lemma, the condition "every >= floor(n/2) subgraph has > n^2/50
    edges" is equivalent to the single check `f(floor(n/2)) > n^2/50`, where
    `f(m)` is the minimum edge count over m-subsets.
  - `src/search_a.py` — Strategy A: sparsest-m-subset finder (bruteforce / ILP /
    local search). Exact methods PROVE; local search only DISQUALIFIES.
  - `src/search_b.py` — Strategy B: iterate families and screen each candidate.
    Run with `python -m src.search_b`.
  - `tests/test_known_graphs.py` — sanity suite. **Whole suite: 29 passing.**
  - First sweep: no counterexample (expected). C5 blow-ups land exactly at the
    threshold — a lead for later phases.
- **Phase 2 — NEXT (not yet started):** scale the search (larger n, more families,
  perturbations of near-miss candidates like C5 blow-ups), tighten the ILP path,
  and add result persistence/analysis beyond the run log.

### How the pieces talk
`graphs.py` builds candidates -> `verify.py` (uses `search_a.py`) decides ->
`search_b.py` orchestrates the sweep and logs to `results/run_log.jsonl`.
Import as a package from the project root (e.g. `from src.verify import ...`);
run the driver as a module (`python -m src.search_b`).
