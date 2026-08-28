# TRISHULA — the Shiva Agent upgrade core ☤🔱

**Trishula** (त्रिशूल, Shiva's trident) is the engine that upgrades Shiva from
a self-improving *agent* into a self-improving *software engineering
organization*. It combines the four superpowers of the best agent harnesses
in the world into one stdlib-only package that runs anywhere Shiva runs —
from a \$5 VPS to Termux to a GPU cluster:

| Prong | Name | Heritage | What it adds |
|------|------|----------|--------------|
| 1 | **Kāraṇa** | Claude Code | Precise edits, repository map, curated context, mandatory verification, plan→act→verify coding loop |
| 2 | **Āyudha** | Codex | Declarative tool registry with JSON-schema validation, confined workspace, guarded shell (denylist, timeouts, output caps, network denial, bwrap/unshare isolation) |
| 3 | **Chit-Shodhana** | Hermes | Trajectory retrospectives with hard-signal scoring, automatic skill distillation, BM25 skill retrieval, and skills that **patch themselves during use** |
| 4 | **Devas** | Devin AI | Goal→task-DAG planning, a catalogue of specialist roles, and a parallel swarm executor with a shared blackboard and review gates |

```
  goal
   │
   ├─► Devas:  scout → architect → ⟨implementers in parallel⟩ → reviewer → qa → docs
   │             │  (each worker is a role-scoped Kāraṇa coding loop)
   │             ▼
   ├─► Kāraṇa: context engine → edit (str_replace/insert/undo) → verify (syntax/tests/build)
   │             │  (every action executes through Āyudha tools)
   │             ▼
   ├─► Āyudha: workspace containment + sandboxed shell + schema-validated tools
   │             ▼
   └─► Chit-Shodhana: journal every event → reflect → score → distill/patch skills
                 │
                 └──► next goal starts smarter (retrieved skills injected)
```

## Why it degrades gracefully

Trishula is **stdlib-only** and fully usable with **zero API keys**. When no
model is configured (`TRISHULA_PROVIDER` unset):

* the planner uses a four-phase heuristic that inspects the real repository;
* the verifier actually runs your tests (pytest → npm → go → cargo → direct
  `python3 test_*.py` fallback);
* the reflector scores runs from hard signals (verdict, failed edits,
  thrashing, denials, timeouts) — no prose;
* the swarm runs deterministic workers that produce role-appropriate
  artifacts.

Plug in a model at any time (`--provider openai|openrouter|nous|anthropic` or
via env) and every deterministic path upgrades to model-driven reasoning with
**the same data shapes** — nothing else changes.

## Usage

```bash
# Autonomous coding task with the full learning loop (plan → edit → verify → reflect → learn)
trishula code "fix the retry logic in the API client and add a regression test"

# Run a goal as a full development team (plan only, or execute the swarm)
trishula team "ship the webhooks feature: API endpoint, tests, CI, docs"
trishula team "refactor the billing module" --plan-only

# Inspect the self-improvement library
trishula skills list
trishula skills search "flaky test retry"

# History of autonomous runs with their scores
trishula runs

# Engine test suite (no pytest required)
trishula selftest
```

```python
from trishula import AutonomyLoop, TeamPlanner, Swarm, Workspace

# One-shot autonomous engineering with learning
run = AutonomyLoop("/path/to/repo").coding_task("make the parser handle unicode")
print(run.report["verdict"], run.retrospective["score"])

# Or coordinate a team
plan = TeamPlanner("/path/to/repo").plan("add SSO login")
report = Swarm("/path/to/repo", plan).execute()  # parallel workers, review gates
```

## Configuration

Environment variables (all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRISHULA_HOME` | `$SHIVA_HOME/trishula` | state dir (skills.db, runs.db) |
| `TRISHULA_PROVIDER` | _(offline)_ | `stub` · `openai` · `openrouter` · `nous` · `anthropic` |
| `TRISHULA_MODEL` | — | model id for the chosen provider |
| `TRISHULA_ALLOW_NETWORK` | `0` | let sandboxed shells reach the network |
| `TRISHULA_PARALLEL_TEAMS` | `1` | run swarm workers in parallel |
| `TRISHULA_MAX_WORKERS` | `8` | swarm thread-pool size |

## Security model

* **Filesystem**: every path is resolved and asserted under the workspace
  root; symlink escapes are rejected; read-only mode refuses all writes.
  `.git`, `node_modules`, venvs, and cache dirs are never walked.
* **Shell**: destructive command denylist (`rm -rf /`, fork bombs, …),
  working-directory confinement, hard timeouts with process-group kill,
  output truncation, secret-env stripping (`*_KEY`, `*_TOKEN`, `*_SECRET`, …),
  black-hole proxy when network is denied, and best-effort
  `bwrap`/`unshare -Urn` namespace isolation on Linux.
* **Failures are data**: tool failures, edit misses, and denials return
  structured results — they never crash the agentic loop, and they feed the
  reflector's anti-pattern extraction.

## Layout

```
trishula/
  core/        types, errors, journal/events, config, sqlite storage, logging
  llm/         client protocol, offline stub, OpenAI-compatible + Anthropic backends
  tools/       workspace, sandboxed shell, tool registry, built-in coding tools
  coding/      edits, repository map, context engine, verifier, coding loop
  autonomy/    reflector, BM25 skill library, learning loop
  team/        role catalogue, DAG planner, blackboard, swarm executor
  cli.py       trishula command (code | team | skills | runs | selftest)
  tests/       70 stdlib-unittest tests covering every prong end-to-end
```
