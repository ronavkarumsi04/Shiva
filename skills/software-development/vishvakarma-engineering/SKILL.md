---
name: vishvakarma-engineering
description: "Engineering across physical disciplines with Shiva — electrical/PCB, embedded, mechanical/structural, aerospace, biomedical, thermal/fluid, optical, controls — formulas, certification gates (DO-178C/ISO 26262/IEC 60601…), simulations, and cross-platform iOS/Xcode testing."
version: 1.0.0
author: Shiva Agent
license: MIT
platforms: [linux, macos, windows, termux]
metadata:
  shiva:
    tags: [engineering, electrical, mechanical, aerospace, biomedical, embedded, pcb, fea, cfd, certification, ios, xcode, trishula]
    related_skills: [trishula-autonomous-engineering]
---

# Vishvakarma: Multi-Domain Engineering

Shiva's engineering prong (🔱 Trishula · Vishvakarma). Use these tools for
**physical** engineering, not just software:

| Tool | Use for |
|------|---------|
| `eng_detect` | Identify which disciplines a project involves and which gate/toolchains apply |
| `eng_formula` | Search the 48+ formula library (electrical → aerospace → biomedical…) |
| `eng_calculate` | Evaluate a formula with automatic unit conversion |
| `eng_safety_gate` | Cert-evidence checklists: DO-178C, ISO 26262, IEC 62304/60601/61508, IPC-2221, ASCE 7, FOS |
| `eng_sim_plan` | Detect simulators (ngspice, iverilog/GHDL, KiCad ERC/DRC, PlatformIO, CalculiX, OpenFOAM…) and emit commands |
| `ios_test` | iOS/Xcode testing — native on Mac, CI/remote on Windows/Linux |

## Operating procedure

1. **`eng_detect` first** — pass the task as `goal`; it returns domains with
   confidence, the applicable safety gate, and toolchains.
2. **Compute with units, not numbers from memory.** Use `eng_formula`/`eng_calculate`;
   pass values as `[value, unit]` pairs (`{"width": [0.25, "mm"]}`) and state
   assumptions. Always do hand calcs *before* trusting simulation.
3. **Verify with simulation where a toolchain exists** — `eng_sim_plan`
   detects it and gives exact commands; if it isn't installed, say so and
   give the install/CI command rather than fabricating results.
4. **Regulated work → gate check** — `eng_safety_gate evaluate <key>` and
   enumerate the missing evidence; treat items as passing *only on evidence*.
   These checklists are starting points — never claim certification.

## Domain notes

- **Electrical/PCB**: SPICE before tapeout; trace width via `pcb_trace_current`
  (IPC-2221); ERC/DRC (`kicad-cli`) must be clean; check creepage/clearance.
- **Embedded**: bound every sensor path; safety paths need diagnostics
  (`diagnostic coverage`); PlatformIO builds/tests; QEMU for HIL-less runs.
- **Mechanical/structural**: stress, deflection, buckling and **factor of
  safety** (≥1.5 typical, higher for life-safety) with units stated; FEA
  (CalculiX) confirms hand calcs, not replaces; fatigue for cyclic loads.
- **Aerospace**: compute dynamic pressure, lift/drag, Δv/orbital regimes,
  mass/power margins; software that flies tracks to DO-178C (DAL, MC/DC).
- **Biomedical**: patient safety first; ISO 14971 risk file, IEC 60601
  electrical/EMC, IEC 62304 lifecycle; respect physiological ranges/units.

## iOS / Xcode on Windows or Linux — be honest

iOS Simulator and XCUITest **require macOS + Xcode**; Apple ships them for no
other OS. The correct cross-platform answer is:

1. **Runs anywhere now**: SwiftPM logic tests (`swift test`), React Native
   jest (`npm test`), Flutter (`flutter test`) — run them directly.
2. **Simulator/UI tests**: `ios_test` with `write_ci=true` generates a
   `macos-latest` GitHub Actions workflow (build + test on Apple hardware in
   the cloud); or run on a remote/EC2 Mac via the emitted SSH commands; or use
   a device cloud (Appetize/BrowserStack) with an IPA built by macOS CI.

Never claim simulator tests passed on Windows/Linux.

## CLI

```bash
shiva trishula eng formulas electrical --query trace
shiva trishula eng calc dynamic_pressure rho=1.225 v=340
shiva trishula eng calc pcb_trace_current width=0.25:mm thickness=35:um
shiva trishula eng detect "design the power supply and PCB"
shiva trishula eng gates ipc-2221
shiva trishula eng sim
shiva trishula ios --write-ci
```
