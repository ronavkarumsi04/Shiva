"""Shiva agent-facing tools for the Vishvakarma engineering prong.

Model-callable wrappers over the trishula engineering engine — multi-domain
calculations, discipline detection, certification gates, simulation plans,
and cross-platform iOS testing. Like ``tools/trishula_tools.py`` this module
imports with zero third-party dependencies (trishula is stdlib-only) and does
its heavy imports lazily inside handlers.

Tools:
    eng_formula     — search/list the cross-domain formula library
    eng_calculate   — evaluate a formula (values in SI or [value, unit] pairs)
    eng_detect      — detect engineering disciplines in a workspace/goal
    eng_safety_gate — list/evaluate certification gates (DO-178C, ISO 26262, …)
    eng_sim_plan    — detect simulators/analyzers and emit run commands
    ios_test        — cross-platform iOS/Xcode test plan (+ GitHub Actions CI)
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result

_EMOJI = "🛠️"
_TOOLSET = "engineering"
_MAX = 6000


def _ws(path: str) -> str:
    path = path or os.getcwd()
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.abspath(path)


def _truncate(s: str, n: int = _MAX) -> str:
    return s if len(s) <= n else s[: n - 120] + f"\n...[truncated {len(s) - n} chars]"


def check_engineering_requirements() -> bool:
    try:
        import trishula.engineering  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def eng_formula_tool(action: str = "list", domain: str = "", query: str = "") -> str:
    try:
        from trishula.engineering.formulas import list_formulas
        entries = list_formulas(domain or "")
        if action == "search" and query:
            q = str(query).lower()
            entries = [f for f in entries
                       if q in f.name.lower() or q in f.description.lower()
                       or q in f.domain or any(q in t for t in f.tags)]
        return tool_result({
            "count": len(entries),
            "formulas": [
                {"name": f.name, "domain": f.domain, "result_unit": f.result_unit,
                 "args": f.args, "description": f.description}
                for f in entries[:120]
            ],
        })
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"eng_formula failed: {type(exc).__name__}: {exc}")


def eng_calculate_tool(name: str, arguments: Dict[str, Any] | None = None) -> str:
    try:
        from trishula.engineering.formulas import FORMULAS, calculate
        if name not in FORMULAS:
            import difflib
            close = difflib.get_close_matches(name, list(FORMULAS), n=8)
            return tool_error(f"unknown formula {name!r}; closest: {close}")
        value = calculate(name, arguments or {})
        return tool_result({
            "name": name, "value": value, "unit": FORMULAS[name].result_unit,
            "statement": f"{name} = {value:g} {FORMULAS[name].result_unit}",
        })
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"eng_calculate failed: {type(exc).__name__}: {exc}")


def eng_detect_tool(goal: str = "", path: str = "") -> str:
    try:
        from trishula.engineering.domains import detect_domains, DOMAINS
        from trishula.tools.workspace import Workspace
        conf = detect_domains(Workspace(_ws(path)), goal=goal or "")
        return tool_result({
            "domains": [
                {"domain": k, "confidence": v, "name": DOMAINS[k].name,
                 "gate": DOMAINS[k].gate, "toolchains": list(DOMAINS[k].toolchains)}
                for k, v in conf.items()
            ]
        })
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"eng_detect failed: {type(exc).__name__}: {exc}")


def eng_safety_gate_tool(action: str = "list", gate: str = "", path: str = "") -> str:
    try:
        from trishula.engineering.safety import GATES, evaluate_gate
        from trishula.tools.workspace import Workspace
        if action == "list":
            return tool_result({
                "gates": [
                    {"key": k, "name": g.name, "domain": g.domain,
                     "reference": g.reference, "items": len(g.items)}
                    for k, g in GATES.items()
                ]
            })
        if gate not in GATES:
            return tool_error(f"unknown gate {gate!r}; known: {sorted(GATES)}")
        report = evaluate_gate(gate, Workspace(_ws(path)))
        d = report.to_dict()
        d["note"] = (
            "Checklists are practitioner starting points, not official compliance; "
            "a 'pass' means evidence was found in the workspace — certification still needs the standard text and licensed sign-off."
        )
        return tool_result(d)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"eng_safety_gate failed: {type(exc).__name__}: {exc}")


def eng_sim_plan_tool(path: str = "") -> str:
    try:
        from trishula.engineering.toolchains import plan_simulations
        from trishula.tools.workspace import Workspace
        plans = plan_simulations(Workspace(_ws(path)))
        return tool_result({"plans": plans})
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"eng_sim_plan failed: {type(exc).__name__}: {exc}")


def ios_test_tool(path: str = "", scheme: str = "", write_ci: bool = False) -> str:
    try:
        from trishula.engineering.ios import ios_test_plan
        from trishula.tools.workspace import Workspace
        ws = Workspace(_ws(path))
        plan = ios_test_plan(ws, scheme=scheme)
        wrote = False
        if write_ci:
            target = ws.resolve(".github/workflows/ios-ci.yml")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.ci_workflow_yaml, encoding="utf-8")
            wrote = True
        d = plan.to_dict()
        d["ci_workflow_written"] = wrote
        if wrote:
            d["ci_workflow_path"] = ".github/workflows/ios-ci.yml"
        d["note"] = (
            "iOS Simulator/XCUITest require macOS+Xcode. On Windows/Linux use the "
            "generated macos CI workflow, a remote/EC2 Mac, or a device cloud "
            "(Appetize/BrowserStack); SwiftPM/jest/flutter logic tests run anywhere."
        )
        return tool_result(d)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"ios_test failed: {type(exc).__name__}: {exc}")


# =============================================================================
# Schemas
# =============================================================================

_SCHEMA = lambda name, desc, props, reqs=(): {  # noqa: E731
    "type": "function",
    "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(reqs)},
    },
}

_PATH_PROP = {"type": "string", "description": "Project/workspace root (default: cwd)", "default": ""}

registry.register(
    name="eng_formula",
    toolset=_TOOLSET,
    schema=_SCHEMA(
        "eng_formula",
        "Search or list the cross-domain engineering formula library "
        "(electrical circuits/PCB, mechanical/structures, thermal/fluid, "
        "aerospace/aero/propulsion/orbital, biomedical/clinical, chemical, "
        "optics, controls, embedded/ADC). Returns formula names with SI units "
        "and descriptions; evaluate them with eng_calculate. 48+ formulas.",
        {
            "action": {"type": "string", "enum": ["list", "search"], "default": "list"},
            "domain": {"type": "string", "description": "Optional domain filter", "default": ""},
            "query": {"type": "string", "description": "Keyword for search (e.g. 'trace', 'orbit', 'stress')", "default": ""},
        },
    ),
    handler=lambda args, **kw: eng_formula_tool(
        action=args.get("action", "list"), domain=args.get("domain", ""), query=args.get("query", "")),
    check_fn=check_engineering_requirements,
    emoji=_EMOJI,
    description="Engineering formula library (all disciplines)",
)

registry.register(
    name="eng_calculate",
    toolset=_TOOLSET,
    schema=_SCHEMA(
        "eng_calculate",
        "Evaluate an engineering formula by name. Values are SI unless given "
        "as [value, unit] pairs for automatic conversion, e.g. "
        'arguments={"rho": 1.225, "v": [340, "m/s"]} for dynamic_pressure, '
        'or {"width": [0.25, "mm"], "thickness": [35, "um"]} for pcb_trace_current.',
        {
            "name": {"type": "string", "description": "Formula name from eng_formula (e.g. ohms_law, lift_force, beam_deflection_cantilever_end, rocket_delta_v)"},
            "arguments": {"type": "object", "description": "Named inputs; scalars in SI, or [value, unit] pairs"},
        },
        ("name",),
    ),
    handler=lambda args, **kw: eng_calculate_tool(
        name=args.get("name", ""), arguments=args.get("arguments", {})),
    check_fn=check_engineering_requirements,
    emoji=_EMOJI,
    description="Evaluate engineering formulas with units",
)

registry.register(
    name="eng_detect",
    toolset=_TOOLSET,
    schema=_SCHEMA(
        "eng_detect",
        "Detect which engineering disciplines a project involves "
        "(electrical, embedded, mechanical, aerospace, biomedical, civil, "
        "chemical, optical, thermal, controls, software/iOS) from its files "
        "and a task description. Suggests the relevant safety gate and toolchains.",
        {
            "goal": {"type": "string", "description": "Task/goal description to hint domains", "default": ""},
            "path": _PATH_PROP,
        },
    ),
    handler=lambda args, **kw: eng_detect_tool(goal=args.get("goal", ""), path=args.get("path", "")),
    check_fn=check_engineering_requirements,
    emoji=_EMOJI,
    description="Detect engineering disciplines in a project",
)

registry.register(
    name="eng_safety_gate",
    toolset=_TOOLSET,
    schema=_SCHEMA(
        "eng_safety_gate",
        "List certification/safety-standard gates (DO-178C aerospace, "
        "ISO 26262 automotive, IEC 62304/60601 medical, IEC 61508 functional "
        "safety, IPC-2221 PCB, ASCE 7 structural, mechanical factor-of-safety) "
        "or evaluate one against a workspace's evidence, returning what is "
        "satisfied and what artifacts/tests/studies are missing.",
        {
            "action": {"type": "string", "enum": ["list", "evaluate"], "default": "list"},
            "gate": {"type": "string", "description": "Gate key for evaluate (e.g. do-178c)", "default": ""},
            "path": _PATH_PROP,
        },
    ),
    handler=lambda args, **kw: eng_safety_gate_tool(
        action=args.get("action", "list"), gate=args.get("gate", ""), path=args.get("path", "")),
    check_fn=check_engineering_requirements,
    emoji=_EMOJI,
    description="Certification/safety-standard gate checklists & evidence",
)

registry.register(
    name="eng_sim_plan",
    toolset=_TOOLSET,
    schema=_SCHEMA(
        "eng_sim_plan",
        "Detect installed engineering simulators/analyzers (ngspice SPICE, "
        "Icarus Verilog/GHDL, KiCad ERC/DRC, PlatformIO, OpenSCAD, CalculiX FEA, "
        "OpenFOAM CFD, Swift/Xcode) and emit the exact analysis commands; for "
        "tools not installed here, report install/CI guidance instead of faking results.",
        {"path": _PATH_PROP},
    ),
    handler=lambda args, **kw: eng_sim_plan_tool(path=args.get("path", "")),
    check_fn=check_engineering_requirements,
    emoji=_EMOJI,
    description="Detect simulators and emit analysis commands",
)

registry.register(
    name="ios_test",
    toolset=_TOOLSET,
    schema=_SCHEMA(
        "ios_test",
        "Plan iOS/Xcode testing on THIS machine. SwiftPM logic tests, React "
        "Native (jest) and Flutter tests run on any OS. iOS-simulator/XCUITest "
        "require macOS+Xcode; on Windows/Linux set write_ci=true to generate a "
        "GitHub Actions macos workflow and get remote-Mac/device-cloud paths.",
        {
            "path": _PATH_PROP,
            "scheme": {"type": "string", "description": "Xcode scheme (auto-detected on Mac)", "default": ""},
            "write_ci": {"type": "boolean", "description": "Write .github/workflows/ios-ci.yml for cloud macOS testing", "default": False},
        },
    ),
    handler=lambda args, **kw: ios_test_tool(
        path=args.get("path", ""), scheme=args.get("scheme", ""), write_ci=args.get("write_ci", False)),
    check_fn=check_engineering_requirements,
    emoji="📱",
    description="Cross-platform iOS/Xcode testing plan + macOS CI generation",
)
