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

## 5th prong — Vishvakarma: engineering across disciplines

Trishula is not just for software. The **Vishvakarma** prong makes Shiva an
engineer for physical disciplines, fully offline and stdlib-only:

* **Formula library** — 48+ verified, SI-pure formulas across electrical
  (Ohm, RC, reactance, IPC-2221 PCB trace current), mechanical/structural
  (stress, beam deflection, torsion, Euler buckling, FOS), thermal/fluid
  (conduction, Reynolds, Darcy–Weisbach), aerospace (dynamic pressure,
  lift/drag, orbit velocity/period, Tsiolkovsky Δv, speed of sound/Mach),
  biomedical (BMI, Du Bois BSA, cardiac output, Laplace, SNR), chemical
  (ideal gas), optics (Snell, critical angle), controls (settling time, PID
  derivative), and embedded (ADC LSB/resolution). Inputs accept non-SI units
  (`[0.25, "mm"]`) and are converted automatically.
* **Domain detection** — classifies a workspace/goal into electrical,
  embedded, mechanical, aerospace, biomedical, civil, chemical, optical,
  thermal, controls, or software/iOS from file types and content.
* **Certification gates** — evidence checklists for **DO-178C** (airborne
  software), **ISO 26262** (automotive/ASIL), **IEC 62304 + IEC 60601**
  (medical devices), **IEC 61508** (functional safety/SIL), **IPC-2221**
  (PCB), **ASCE 7** (structural loads), and a mechanical factor-of-safety
  gate. Gates report satisfied vs. *missing evidence* deterministically —
  they never claim certification.
* **Simulation toolchains** — detects ngspice (SPICE), Icarus Verilog/GHDL,
  KiCad ERC/DRC, PlatformIO, OpenSCAD, CalculiX (FEA), OpenFOAM (CFD),
  Swift/Xcode and emits exact analysis commands; missing tools produce
  honest install/CI guidance instead of faked results.
* **Engineering teams** — new specialist roles (`ee`, `embedded`,
  `mechanical`, `aerospace`, `biomedical`, `compliance`) are automatically
  added to team plans for engineering goals, with a compliance gate that
  blocks completion until evidence is enumerated.

### iOS / Xcode testing across platforms

The honest constraint: **iOS Simulator and XCUITest require macOS + Xcode** —
Apple ships them for no other OS. Trishula splits the work accordingly:

| Test | Mac | Windows/Linux |
|------|-----|---------------|
| SwiftPM logic tests (`swift test`) | ✅ | ✅ |
| React Native (jest) / Flutter tests | ✅ | ✅ |
| XCTest / XCUITest on iOS Simulator | ✅ `xcodebuild test` | ☁️ via generated `macos` CI workflow, remote/EC2 Mac, or device cloud (Appetize/BrowserStack) |

From Windows/Linux, `ios_test(write_ci=true)` (or `shiva trishula ios
--write-ci`) generates a ready-to-push GitHub Actions `macos-latest`
workflow plus exact SSH commands for a remote Mac.

```bash
shiva trishula eng formulas electrical --query trace
shiva trishula eng calc dynamic_pressure rho=1.225 v=340
shiva trishula eng detect "design the power supply and PCB"
shiva trishula eng gates do-178c
shiva trishula eng sim
shiva trishula ios --write-ci
```

Agent tools: `eng_formula`, `eng_calculate`, `eng_detect`, `eng_safety_gate`,
`eng_sim_plan`, `ios_test` (the `engineering` toolset); skill:
`vishvakarma-engineering`.

## Invoking Trishula from Shiva

Trishula is wired into Shiva in three places, not just the standalone CLI:

1. **`shiva trishula …`** (alias `shiva tri …`) — the engine CLI under the
   Shiva command: `shiva trishula code "…"`, `shiva trishula team "…"`,
   `shiva trishula skills`, `shiva trishula runs`, `shiva trishula selftest`
   (parser: `shiva_cli/subcommands/trishula.py`).
2. **Agent-callable tools** — the model can invoke the engine mid-conversation
   via `trishula_code` (autonomous verified coding task), `trishula_team`
   (plan/execute a dev-team swarm), `trishula_skills` (search the distilled
   skill library), and `trishula_runs` (run history). These ship in the
   `trishula` toolset, which is folded into the `coding` posture and the
   `shiva-acp` editor toolset (`tools/trishula_tools.py`, registered
   automatically by the tools-directory auto-discovery).
3. **Skill** — `skills/software-development/trishula-autonomous-engineering/`
   teaches the agent when to reach for each tool and to trust the
   verification verdict rather than narration.

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
