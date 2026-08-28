---
name: trishula-autonomous-engineering
description: "Autonomous verified engineering with the Trishula engine — self-verifying code changes, Devin-style dev-team plans, and a skill library that improves across runs."
version: 1.0.0
author: Shiva Agent
license: MIT
platforms: [linux, macos, windows, termux]
metadata:
  shiva:
    tags: [coding, autonomous, verification, planning, teams, self-improvement, trishula]
    related_skills: [shiva-engineering-team, plan, requesting-code-review, simplify-code]
---

# Trishula: Autonomous Verified Engineering

## Overview

Trishula (🔱) is Shiva's autonomous engineering engine. It fuses four
capabilities into one sandboxed, self-improving loop — and you can invoke
any of them directly with the `trishula_*` tools or the `shiva trishula`
CLI:

1. **Verified coding** (`trishula_code`) — the engine curates repository
   context, makes precise edits, and *mandatorily* runs the project's tests
   (red→green). It never reports success without a verification verdict, and
   it reflects on the run afterward.
2. **Dev teams** (`trishula_team`) — compiles a goal into a dependency-linked
   task DAG assigned to specialist roles: scout, architect, implementers
   (parallel), reviewer, QA, devops, docs-writer — with review gates.
3. **Skill library** (`trishula_skills`) — every successful run distills a
   reusable, BM25-retrievable skill; skills patch themselves when they fail.
4. **Run history** (`trishula_runs`) — past autonomous runs with verdicts and
   retrospective scores.

Everything runs **offline with zero API keys** (deterministic planning + real
test execution) and upgrades to model-driven reasoning when
`TRISHULA_PROVIDER` is set.

## When to reach for Trishula

| Situation | Use |
|-----------|-----|
| "Fix bug X / add feature Y" in a real repo where tests must go green | `trishula_code` |
| A multi-part project ("ship webhooks: endpoint, tests, CI, docs") | `trishula_team` (plan first, then `execute=true`) |
| About to attempt a complex, possibly-solved-before task | `trishula_skills` search first |
| User asks "what have you been working on / did it work?" | `trishula_runs` |
| You want a team-style breakdown before touching code | `trishula_team` with `plan_only=true` |

## Operating procedure

### Single coding task

1. `trishula_skills` (action=search, query=<task>) — a distilled tactic may
   already exist.
2. `trishula_code` with a specific, verifiable goal. Name files/symptoms and
   require a regression test for bugs.
3. Read the returned `verdict` (`pass`/`fail`/`partial`/`skipped`),
   `changed_files`, and `retrospective_lessons`. A `fail` verdict means the
   task is **not done** — report honestly and iterate; never claim success.
4. Mention any `skills_created` — the engine learned something reusable.

### Team project

1. `trishula_team` with `plan_only=true` to review the DAG (roles, deps,
   acceptance criteria). Present the plan for big efforts.
2. Re-run with `execute=true` to run the swarm; inspect per-role
   `results` and `artifacts`. Reviewer rejections automatically trigger
   one bounded repair round.

## Rules

- **Trust the verdict, not the narration.** Only `pass`/`partial` means the
  change works. `skipped` means no tests were found to prove it — say so.
- Keep goals concrete and testable; vague goals produce vague plans.
- The engine is sandboxed (no network, confined workspace) — don't ask it to
  install packages or reach external services; do that yourself in the
  normal terminal first if needed.
- `trishula_code` edits the repo on disk under `path` (default cwd) — point
  `path` at the right project root.

## CLI equivalents

```bash
shiva trishula code  "fix the flaky retry test and add a regression check"
shiva trishula team  "ship webhooks: endpoint, tests, CI, docs" --plan-only
shiva trishula skills search "retry backoff"
shiva trishula runs
shiva trishula selftest     # engine test suite (70+ tests, no deps)
```

See `docs/trishula.md` for the full architecture.
