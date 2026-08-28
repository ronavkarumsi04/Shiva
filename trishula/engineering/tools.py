"""Engineering tools for the Trishula registry (Vishvakarma prong).

Attaches model/CLI-callable tools for every engineering capability:

    eng_formulas      list/search the formula library
    eng_calculate     evaluate a formula (with non-SI unit conversion)
    eng_detect        detect engineering domains in the workspace
    eng_gates         list certification gates / evaluate one
    eng_sim_plan      detect simulator toolchains and emit run commands
    ios_test_plan     cross-platform iOS/Xcode testing plan
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trishula.core.types import Journal, ToolResult
from trishula.engineering import formulas as F
from trishula.tools.registry import ToolRegistry
from trishula.tools.workspace import Workspace


def attach_engineering_tools(
    reg: ToolRegistry,
    workspace: Workspace,
    *,
    journal: Journal | None = None,
) -> None:
    # ── formulas ────────────────────────────────────────────────────────

    def eng_formulas(domain: str = "", query: str = "") -> ToolResult:
        entries = F.list_formulas(domain or "")
        if query:
            q = query.lower()
            entries = [f for f in entries
                       if q in f.name.lower() or q in f.description.lower()
                       or q in f.domain.lower() or any(q in t for t in f.tags)]
        lines = [
            f"{f.name} [{f.domain}] -> {f.result_unit}\n    {f.description}\n    args: {f.args}"
            for f in entries
        ]
        return ToolResult(True, output="\n".join(lines) or "no formulas match",
                          data={"count": len(entries)})

    reg.register(
        "eng_formulas",
        "List or search the cross-domain engineering formula library "
        "(electrical, mechanical, aerospace, biomedical, thermal, fluid, "
        "chemical, optical, controls, embedded).",
        {"type": "object",
         "properties": {
             "domain": {"type": "string", "description": "Optional domain filter", "default": ""},
             "query": {"type": "string", "description": "Optional keyword search", "default": ""},
         }},
        eng_formulas, tags=("engineering", "read"), read_only=True,
    )

    def eng_calculate(name: str, arguments: dict | None = None) -> ToolResult:
        if name not in F.FORMULAS:
            close = [n for n in F.FORMULAS if name.lower() in n.lower()]
            return ToolResult(False, error=f"unknown formula {name!r}; similar: {close[:8]}")
        try:
            value = F.calculate(name, arguments or {})
            formula = F.FORMULAS[name]
            return ToolResult(True, output=f"{name} = {value:g} {formula.result_unit}",
                              data={"value": value, "unit": formula.result_unit})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")

    reg.register(
        "eng_calculate",
        "Evaluate an engineering formula by name. Pass arguments as an object; "
        "values are SI unless given as [value, unit] pairs, e.g. "
        '{"v": [340, "m/s"], "rho": 1.225} for dynamic_pressure. Use eng_formulas to list names.',
        {"type": "object",
         "properties": {
             "name": {"type": "string", "description": "Formula name, e.g. dynamic_pressure, ohms_law, orbital_velocity, pcb_trace_current"},
             "arguments": {"type": "object", "description": "Named inputs; scalars in SI, or [value, unit] pairs"},
         },
         "required": ["name"]},
        eng_calculate, tags=("engineering",),
    )

    # ── domain detection ────────────────────────────────────────────────

    def eng_detect(goal: str = "") -> ToolResult:
        from trishula.engineering.domains import detect_domains, DOMAINS
        conf = detect_domains(workspace, goal=goal)
        lines = [f"{score:4.2f}  {key:<11} {DOMAINS[key].name}" for key, score in conf.items()]
        return ToolResult(True, output="\n".join(lines) or "no engineering domains detected",
                          data={"domains": conf})

    reg.register(
        "eng_detect",
        "Detect which engineering disciplines are present in the workspace "
        "(electrical, embedded, mechanical, aerospace, biomedical, civil, "
        "chemical, optical, thermal, controls, software/iOS) from files and the goal.",
        {"type": "object",
         "properties": {"goal": {"type": "string", "description": "Task description to hint domains", "default": ""}}},
        eng_detect, tags=("engineering", "read"), read_only=True,
    )

    # ── safety gates ────────────────────────────────────────────────────

    def eng_gates(action: str = "list", gate: str = "") -> ToolResult:
        from trishula.engineering.safety import GATES, evaluate_gate
        if action == "list":
            lines = [f"{k:<14} [{g.domain}] {g.name} — {g.reference}" for k, g in GATES.items()]
            return ToolResult(True, output="\n".join(lines), data={"count": len(GATES)})
        if not gate:
            return ToolResult(False, error="gate key required for action=evaluate (see list)")
        if gate not in GATES:
            return ToolResult(False, error=f"unknown gate {gate!r}; known: {sorted(GATES)}")
        report = evaluate_gate(gate, workspace)
        d = report.to_dict()
        missing = "\n".join(f"  ✗ {m['id']}: {m['text']} ({m['detail']})" for m in d["missing"])
        ok = "\n".join(f"  ✓ {s['id']}: {s['detail']}" for s in d["satisfied"])
        out = (f"Gate {d['gate_name']} — verdict: {d['verdict'].upper()} "
               f"(coverage {d['coverage']:.0%})\nSatisfied:\n{ok or '  (none)'}\n"
               f"Missing/required:\n{missing or '  (none — all evidenced locally)'}")
        return ToolResult(True, output=out, data=d)

    reg.register(
        "eng_gates",
        "List certification/safety-standard gates (DO-178C, ISO 26262, IEC 62304, "
        "IEC 60601, IEC 61508, IPC-2221, ASCE 7, mechanical FOS) or evaluate one "
        "against workspace evidence. Use action=list then action=evaluate.",
        {"type": "object",
         "properties": {
             "action": {"type": "string", "description": "list|evaluate", "default": "list"},
             "gate": {"type": "string", "description": "Gate key for evaluate", "default": ""},
         }},
        eng_gates, tags=("engineering", "read"), read_only=True,
    )

    # ── simulation toolchains ───────────────────────────────────────────

    def eng_sim_plan() -> ToolResult:
        from trishula.engineering.toolchains import plan_simulations
        plans = plan_simulations(workspace)
        lines = []
        for p in plans:
            mark = "✓ installed" if p["available"] else "✗ not installed"
            lines.append(f"{p['toolchain']:<12} ({p['domain']}) {mark}: {p['name']}")
            for c in p["commands"]:
                lines.append(f"    $ {c}")
            if p["note"]:
                lines.append(f"    note: {p['note']}")
        return ToolResult(True, output="\n".join(lines) or "no relevant toolchains for detected domains",
                          data={"plans": plans})

    reg.register(
        "eng_sim_plan",
        "Detect installed engineering simulators/analyzers (ngspice, iverilog/ghdl, "
        "KiCad ERC/DRC, PlatformIO, OpenSCAD, CalculiX, OpenFOAM, Swift/Xcode) and emit "
        "the exact analysis commands; where a tool is missing, report install/CI guidance.",
        {"type": "object", "properties": {}},
        eng_sim_plan, tags=("engineering", "read"), read_only=True,
    )

    # ── iOS testing ─────────────────────────────────────────────────────

    def ios_test_plan(scheme: str = "", write_ci: bool = False) -> ToolResult:
        from trishula.engineering.ios import ios_test_plan as _plan
        plan = _plan(workspace, scheme=scheme)
        d = plan.to_dict()
        lines = [
            f"Project kind: {d['kind']}  host: {d['host_platform']}",
            f"Simulator tests locally: {d['can_run_simulator_tests_locally']}  "
            f"path: {d['simulator_path']}",
            "",
        ]
        for opt in d["local"]:
            mark = "✓" if opt["available_here"] else "✗ (missing toolchain)"
            lines.append(f"{mark} {opt['label']} [runs: {opt['runs_on']}]")
            for c in opt["commands"]:
                lines.append(f"    $ {c}")
            if opt["requires"]:
                lines.append(f"    requires: {opt['requires']}")
        if d["limitations"]:
            lines.append("\nLimitations:")
            lines.extend(f"  • {l}" for l in d["limitations"])
        lines.append("\nRemote Mac commands:")
        lines.extend(f"  {c}" for c in d["remote_mac_commands"])
        lines.append("\nDevice cloud:")
        lines.extend(f"  • {n}" for n in d["cloud_notes"])

        wrote = ""
        if write_ci:
            target = workspace.resolve(".github/workflows/ios-ci.yml")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.ci_workflow_yaml, encoding="utf-8")
            wrote = f"\nWrote GitHub Actions workflow: {workspace.rel(target)}"
        return ToolResult(True, output="\n".join(lines) + wrote,
                          data={**d, "ci_workflow_written": bool(write_ci)})

    reg.register(
        "ios_test_plan",
        "Plan iOS/Xcode testing for this host. Runs SwiftPM/jest/flutter logic tests "
        "on ANY OS; iOS-simulator/XCUITest only on macOS — from Windows/Linux it "
        "generates a macos CI workflow (write_ci=true) and remote-Mac commands.",
        {"type": "object",
         "properties": {
             "scheme": {"type": "string", "description": "Xcode scheme (auto-detected on Mac)", "default": ""},
             "write_ci": {"type": "boolean", "description": "Write .github/workflows/ios-ci.yml", "default": False},
         }},
        ios_test_plan, tags=("engineering", "ios"),
    )
