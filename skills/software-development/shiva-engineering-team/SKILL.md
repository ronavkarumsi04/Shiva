---
name: shiva-engineering-team
description: Coordinate durable autonomous software teams.
version: 1.0.0
author: Shiva Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  shiva:
    category: software-development
    tags: [coding, teams, autonomy, orchestration]
---

# Shiva Engineering Team Skill

Coordinate multiple coding agents as a durable software-delivery team. This skill combines parallel delegation with dependency ordering, role ownership, leases, evidence gates, and crash recovery; it does not replace repository-specific tests or human approval for consequential changes.

## When to Use

Use this for changes with several independent workstreams, cross-cutting migrations, long-running implementation, or work requiring specialist review. For a small single-file change, work directly instead.

## Prerequisites

- A Git repository with its own instructions loaded.
- Shiva's `delegate_task`, `terminal`, `read_file`, and patching capabilities.
- Python 3.11 or newer.
- A clean baseline and a stated verification command.

## How to Run

Set the helper path:

```bash
TEAM=skills/software-development/shiva-engineering-team/scripts/team.py
```

Create a JSON plan, initialize it, and claim work:

```bash
python "$TEAM" --db .shiva/team.db init plan.json --project upgrade
python "$TEAM" --db .shiva/team.db claim upgrade --worker backend-1
python "$TEAM" --db .shiva/team.db status upgrade
```

The command always returns JSON. Treat nonzero exit status as an orchestration failure.

## Quick Reference

| Command | Purpose |
|---|---|
| `init SPEC --project ID` | Validate and create a task DAG |
| `claim PROJECT --worker ID` | Atomically lease the highest-priority ready task |
| `heartbeat PROJECT TASK --worker ID` | Extend a live lease |
| `complete PROJECT TASK --worker ID --result TEXT` | Finish with evidence |
| `fail PROJECT TASK --worker ID --reason TEXT` | Retry or terminally fail work |
| `status PROJECT` | Inspect all work and dependencies |
| `events PROJECT` | Read the append-only audit stream |

## Procedure

### 1. Establish the contract

Before implementation, record:

- exact user outcome and explicit non-goals;
- repository constraints and protected surfaces;
- baseline test, lint, build, and security commands;
- acceptance criteria observable from outside the implementation;
- rollback strategy and files whose ownership must not overlap.

Never convert hype or scale language into fake line-count goals. Optimize for verified capability, not generated volume.

### 2. Build a dependency graph

Use a plan shaped like:

```json
{
  "goal": "Ship the verified product upgrade",
  "metadata": {"baseline": "git SHA", "test": "project test command"},
  "tasks": [
    {"id": "architecture", "title": "Map contracts", "role": "architect", "priority": 100},
    {"id": "backend", "title": "Implement core", "role": "backend", "depends_on": ["architecture"]},
    {"id": "frontend", "title": "Implement UX", "role": "frontend", "depends_on": ["architecture"]},
    {"id": "review", "title": "Adversarial review", "role": "reviewer", "depends_on": ["backend", "frontend"]},
    {"id": "verify", "title": "Run release gates", "role": "qa", "depends_on": ["review"]}
  ]
}
```

Tasks should have disjoint primary file ownership. Separate implementation and review. Put integration after parallel lanes, not before them.

### 3. Delegate specialists

Use `delegate_task` batch mode for currently ready tasks. Give each worker:

- its claimed task JSON;
- exact owned files and read-only context;
- acceptance and test commands;
- instruction to report changed files, tests, risks, and unresolved issues;
- prohibition against declaring success without observed evidence.

Architects map interfaces. Implementers change code. Reviewers seek defects rather than restating the patch. QA runs real commands against the integrated tree. A lead agent owns final synthesis and user communication.

### 4. Maintain leases

A worker must heartbeat before its lease expires. If a worker disappears, the next `claim` or `status` returns its task to the queue. Claims are transactional, so several agents can safely request work concurrently.

Do not share one worker identity between concurrent agents. Use stable names such as `backend-1`, `security-1`, and `qa-1`.

### 5. Require evidence

Complete tasks with a concise result and JSON evidence:

```bash
python "$TEAM" --db .shiva/team.db complete upgrade backend \
  --worker backend-1 \
  --result "Implemented transaction boundary and regression tests" \
  --evidence '{"tests":["42 passed"],"files":["src/store.py"],"commit":"abc123"}'
```

Evidence should contain commands and observed results, not intentions. A task that cannot pass its gate should be failed with the actual blocker. Retriable failures return to the queue until `max_attempts`; exhausted work marks the project as needing attention.

### 6. Integrate continuously

After each lane:

1. inspect the actual diff;
2. run narrow tests;
3. merge compatible work;
4. run cross-lane tests;
5. update task evidence;
6. only then unlock dependent review.

For shared-process agents, protect the worktree from concurrent writes. Prefer isolated worktrees or assign disjoint paths. Never let two workers rewrite the same lockfile simultaneously.

### 7. Run adversarial review

Ask review agents to find:

- behavior absent from tests;
- race conditions and partial failures;
- unsafe shell, path, secret, and permission handling;
- cache invalidation and context growth;
- incompatible API/schema changes;
- weak observability or impossible rollback;
- claims not supported by command output.

Route discovered defects back into new tasks instead of burying them in a summary.

### 8. Close with release gates

The lead runs the repository's full required suite, checks a clean status, verifies the exact remote commit when pushing, and reports limitations honestly. A project is complete only when every non-cancelled task is done and the integrated artifact passes its gates.

## Pitfalls

- Spawning many agents before defining file ownership creates merge churn.
- Letting implementers approve their own output removes the review boundary.
- Treating agent prose as evidence produces false completion.
- Huge prompts reduce focus; delegate the minimum sufficient context.
- Unlimited retries burn budget without learning. Record failure reasons and change strategy.
- Dynamic system-prompt changes break prompt caching. Pass task context in the delegated user goal.
- Process-local background work is not durable. The SQLite board is durable; child processes are not.

## Verification

Confirm all of the following:

1. `status` shows no queued, running, blocked, or failed tasks.
2. Every implementation task records changed files and narrow test evidence.
3. Review is performed by a different role or agent.
4. Integrated tests and static checks pass on the final tree.
5. Security-sensitive changes have explicit negative tests.
6. The final Git commit and remote branch match.
7. The report distinguishes verified outcomes from deferred work.
