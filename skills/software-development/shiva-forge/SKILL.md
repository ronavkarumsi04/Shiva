---
name: shiva-forge
description: Build code with repository-aware verification.
version: 1.0.0
author: Shiva Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  shiva:
    category: software-development
    tags: [coding, verification, repository, tests]
---

# Shiva Forge Skill

Shiva Forge gives coding agents a repeatable repository-intelligence and verification loop. It discovers project contracts, scopes checks to changed code, executes declared project commands with hard timeouts, and records durable evidence instead of trusting an agent's completion claim.

## When to Use

Use Forge for every non-trivial coding task, especially unfamiliar repositories, multi-language workspaces, and autonomous or delegated implementation. Skip it only for prose-only work with no executable contract.

## Prerequisites

- Python 3.11 or newer.
- A local project directory.
- Git for change detection in Git repositories.
- Project runtimes required by selected checks.

## How to Run

```bash
FORGE=skills/software-development/shiva-forge/scripts/forge.py
python "$FORGE" --root . scan
python "$FORGE" --root . plan
python "$FORGE" --root . verify --timeout 600
```

Use `--base <ref>` to assess a branch against a merge base. Add `--full` before release to include broader declared checks.

## Quick Reference

| Command | Result |
|---|---|
| `scan` | Repository, language, manifest, instruction, Git, and change inventory |
| `plan` | Deterministically selected checks without execution |
| `verify` | Check results plus a durable `.shiva/forge-receipt.json` |

## Procedure

### 1. Orient before editing

Run `scan`. Read the reported instruction chain completely, then inspect manifests and the nearest tests. Do not infer architecture from filenames alone. Establish a clean baseline or explicitly record existing failures.

### 2. Form a falsifiable plan

Write acceptance criteria as observable behavior. Identify the smallest owning modules, callers, persisted or wire contracts, and tests capable of disproving the change. For broad work, use `shiva-engineering-team` to split disjoint ownership.

### 3. Search semantically

Search definitions, uses, tests, configuration, and documentation before changing a symbol. Trace data from boundary to effect. When fixing a bug, identify the exact line where incorrect behavior emerges and verify that the proposed change reaches it.

### 4. Edit transactionally

Prefer small coherent patches. Preserve local style and public contracts. Avoid unrelated cleanup. After each patch, inspect the diff and compile or type-check the narrow surface before continuing.

### 5. Plan verification from the actual diff

Run `plan`. Forge chooses commands from tracked changes and repository-declared scripts; it does not invent package commands. Review the plan and add domain-specific checks where the repository's declarations cannot express the behavior.

### 6. Verify with evidence

Run `verify`. Required checks must pass. A missing runtime, timeout, or failed command is not success. Read the receipt, inspect output, and fix root causes. Never edit a receipt to make a run look green.

### 7. Review adversarially

Re-read the diff as a hostile reviewer. Check boundary conditions, cancellation, concurrency, retries, paths, permissions, secrets, partial writes, backward compatibility, and misleading success states. Add regression tests for discovered failure classes.

### 8. Deliver precisely

Report changed behavior, files, commands, observed results, unresolved risks, and the exact commit. Distinguish what was verified from what was not runnable in the environment.

## Pitfalls

- Passing compilation does not prove behavior.
- Running the entire suite after every keystroke wastes time; use narrow checks, then release gates.
- Generated files and lockfiles may require project-specific regeneration beyond Forge's selection.
- A clean diff can still violate runtime contracts; inspect callers and tests.
- Do not claim unavailable checks passed.
- Do not optimize for line count. Optimize for capability, correctness, and maintained surface area.

## Verification

A completed Forge run should show:

1. the exact Git head and branch;
2. every changed or untracked path considered;
3. why each check was selected;
4. command, status, exit code, duration, and bounded output;
5. a machine-readable receipt written atomically;
6. overall failure when any required check fails or is unavailable.
