# Trishula Studio

A local, dependency-free desktop app for the Shiva engineering engine. It runs
with the system Python only (`python3 trishula_launch.py studio`) — no pip, no
node, no build step — and opens in your browser.

## Two modes

| Mode | Name (default) | What it is |
|------|----------------|------------|
| **Code** | **Shiva** | The autonomous coding agent. You give it a repo + goal; it plans, edits, runs tests, verifies, and runs bounded repair rounds — streaming every step. |
| **Chat** | **Saraswati** | An advanced, ChatGPT-style reasoning companion. Markdown answers, live token streaming, and diagrams. |

Both names, taglines, themes, accents, and the companion's persona are
renamable in **Customize** (persisted to `~/.trishula/studio_settings.json`).
API keys are read from your environment and never stored.

## Launch

```bash
./trishula_launch.py studio              # serves http://127.0.0.1:8765 and opens a browser
./trishula_launch.py studio --port 9000 --no-open
```

Set `TRISHULA_PROVIDER` (and `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` …) to drive
a real model; with no provider it runs in deterministic **offline (stub)** mode
so the full UI and flow still work.

## Rich response formats

The agent flow (Claude-Code style) renders structured blocks instead of a wall
of text:

- **Tool cards** — live “running → done/error” chips for reads, searches, shell
  commands, and edits; collapsible output bodies.
- **Diffs** — green/red add/remove lines for every edit.
- **Verification badge + coverage meters** — PASS / PARTIAL / FAIL with per-file
  coverage bars and uncovered line numbers.
- **Activity timeline** — a glowing trace of every step and repair round.
- **Diagrams** — a tiny SVG layer renders a mini-DSL. Any message may embed a
  ```` ```diagram ```` block:

  ```
  Goal: add webhooks -> Edit: models.py -> Edit: api.py -> Verify: pass
  Build -> Test -> Deploy
  Plan: A -> B: sends
  B -> C: triggers
  ```

  Lines of the form `A -> B` become connectors (`A -> B: label` labels the edge);
  `A: label` names a node. The layer auto-lays-out columns and draws glowing
  rounded nodes with arrow markers.
- **Simulation metrics** — SPICE/FEA/CFD results surface as metric grids
  (gain/BW, von Mises, FoS, Cd/Cl, pressure drop) and are never shown unless the
  run actually converged.

## Architecture

- **Backend** — `trishula/desktop/server.py`: a stdlib `ThreadingHTTPServer`
  serving static assets and streaming **Server-Sent Events**. The coding loop
  runs in a worker thread; every journal event (`tool.call`, `edit.applied`,
  `verify.verdict`, repair rounds) is published live to the browser.
- **Frontend** — `trishula/desktop/static/`: `index.html` shell, `styles.css`
  design system (4 themes × 5 accents, ambient glows, springy motion, responsive
  sidebar), and `app.js` (mode switching, SSE client, markdown + diagram + diff
  + meter renderers).
- **Learning, wired in** — coding runs inject relevant **engineering memory**,
  run through the bounded **verify→repair loop**, and afterwards feed the
  **self-improving prompt loop**; converged simulation logs are captured into
  memory. All of it is visible in the UI.

## Design principles

- **Terminal-grade, text-first** — flat monospace UI modeled on Claude Code /
  Codex: a dense single-line tool trace with a spinner that resolves to ✓/✗,
  collapsible detail, unified diffs inline, and a bottom prompt. No gradients,
  no glows, no card-soup. Three themes (dark / light / sepia) and five muted
  accents; both agents and the chat persona are fully renamable.
- **Slash commands** — `/calc`, `/formulas`, `/gate`, `/gates`, `/diagram`
  surface engineering results as flat, aligned output.
- **Diagrams** — render flat and can be drag-edited inline.
- **Honest** — test, coverage, and simulation results come from real runs; the
  UI never invents metrics. Offline/stub mode is labeled, not faked.
- **Customizable** — rename both agents, theme the whole app, set a persona.
