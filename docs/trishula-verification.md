# Trishula Phase-2 verification & parallel engineering

Two upgrades turn Trishula from "runs tests" into "proves correctness with
genuinely parallel engineers": the **property + coverage verifier** and
**git-worktree worker isolation**.

## 1. Property-based testing (stdlib only)

`trishula/coding/proplib.py` is a Hypothesis-style harness with zero
dependencies: strategies generate inputs, boundary values (0, 1, −1, empty,
min/max) are tried first, and any counterexample is **shrunk** to a minimal
failing case.

```python
from trishula.coding.proplib import check, ints, lists
check(lambda xs: sorted(sorted(xs)) == sorted(xs), lists(ints(-50, 50)))   # passes
check(lambda x: x >= 0, ints(-100, 100))          # fails, shrinks to (-1,)
```

Decorate functions with `@property_test(...)` and they run automatically via
`run_property_file()` during verification.

## 2. Statement coverage feedback (no pytest-cov needed)

`trishula/coding/coverage.py` runs the test suite in a subprocess under
`trace.Trace` and measures, per changed file, executable statement lines
(from `ast`) vs. lines hit. The verifier (config flags `verify_coverage`,
`coverage_min_pct`, default 70%) then turns uncovered regions into
machine-actionable **feedback** for the coding loop, e.g.:

> `calc.py is 50% covered by tests; uncovered lines around 4, 5 — add tests that exercise them.`

## 3. Auto-scaffolded tests

With `auto_generate_tests=True`, the verifier generates
`test_trishula_autogen_<module>.py` for changed modules: import smoke tests
for every public function plus determinism *property* skeletons for pure,
annotated functions. The scaffolding is marked as generated and is safe to
extend — a model (or engineer) replaces the TODOs with real invariants.

### Verdict semantics

| Signal | Effect |
|--------|--------|
| syntax errors / failing tests | `fail` |
| tests pass | `pass` |
| property violation | downgrades to `partial` |
| coverage below `coverage_min_pct` | feedback listing uncovered lines (doesn't fail alone) |

## 4. Git-worktree parallel workers

`trishula/team/worktree.py` gives each swarm worker its own git worktree +
branch:

* `acquire(task)` creates `worktree add -b trishula/<task> HEAD` (bounded pool);
* the worker edits in its own checkout — **no two workers share a cwd**;
* on success the branch is committed and `git merge --no-ff` back under a
  lock (serializes git mutations); clean merges keep the change;
* a **conflict** (same regions edited by parallel workers) aborts the merge
  cleanly and the task fails with `conflict_files`, so it is retried/redone;
* non-git workspaces transparently degrade to in-place execution
  (`isolated=False`).

Enabled by `team_use_worktrees=True` (default; only active inside git repos).
The swarm passes each worker its isolated `Workspace`; `LocalAgentWorker` runs
its role-scoped coding loop inside it, so implementer roles now truly work in
parallel and merge automatically.

## Configuration

```python
TrishulaConfig(
    verify_property_tests=True,
    verify_coverage=True,
    coverage_min_pct=0.70,
    auto_generate_tests=False,   # opt-in test scaffolding
    team_use_worktrees=True,
)
```

All additions are stdlib-only; the verifier still runs anywhere Shiva does.
