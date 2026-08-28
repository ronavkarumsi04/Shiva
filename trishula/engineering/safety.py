"""Certification / safety-standard gates for engineered systems.

A gate is a domain-specific checklist of *evidence items* an assessor (or
the agent) must satisfy. Items are deterministic wherever possible (does the
artifact exist? does the test log exist? does the hazards file mention
mitigations?) and otherwise marked as manual evidence to produce. The gate
never claims compliance — it reports what evidence is present, what is
missing, and the verdict **no-pass until all items are evidenced**.

These checklists are distilled, practitioner-oriented starting points; they
are NOT a substitute for the official standard text or a licensed sign-off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

from trishula.tools.workspace import Workspace


@dataclass
class GateItem:
    id: str
    text: str
    kind: str = "artifact"   # artifact (file pattern) | content (text present) | manual
    pattern: str = ""
    content_needles: tuple[str, ...] = ()
    manual_hint: str = ""


@dataclass
class Gate:
    key: str
    name: str
    domain: str
    description: str
    items: List[GateItem] = field(default_factory=list)
    reference: str = ""


@dataclass
class ItemResult:
    id: str
    text: str
    satisfied: bool
    detail: str = ""


@dataclass
class GateReport:
    gate: str
    gate_name: str
    satisfied: List[ItemResult]
    missing: List[ItemResult]
    verdict: str          # "pass" (all evidenced) | "gap" | "manual-required"
    coverage: float

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "gate_name": self.gate_name,
            "verdict": self.verdict,
            "coverage": round(self.coverage, 3),
            "satisfied": [{"id": r.id, "detail": r.detail} for r in self.satisfied],
            "missing": [{"id": r.id, "text": r.text, "detail": r.detail} for r in self.missing],
        }


def _artifact(itemspec: GateItem, ws: Workspace) -> tuple[bool, str]:
    files = ws.walk_files(max_files=4000)
    pat = itemspec.pattern
    hits = [f for f in files if _match(f, pat)]
    if hits:
        return True, f"found: {', '.join(ws.rel(h) for h in hits[:3])}"
    return False, f"no artifact matching {pat}"


def _content(itemspec: GateItem, ws: Workspace) -> tuple[bool, str]:
    files = ws.walk_files(max_files=4000)
    for f in files:
        if not f.suffix.lower() in {".md", ".txt", ".yml", ".yaml", ".json", ".c",
                                    ".h", ".py", ".swift", ".rst", ".csv"}:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if all(n.lower() in text for n in itemspec.content_needles):
            return True, f"all topics present in {ws.rel(f)}"
    return False, f"no document covering: {', '.join(itemspec.content_needles)}"


def _match(path: Path, pattern: str) -> bool:
    rel = path.as_posix().lower()
    name = path.name.lower()
    p = pattern.lower()
    if p.startswith("name:"):
        return name == p[5:]
    if p.startswith("glob:"):
        import fnmatch
        return fnmatch.fnmatch(rel, p[5:])
    return p in rel or p in name


def evaluate_gate(gate_key: str, workspace: Workspace | None = None) -> GateReport:
    gate = GATES[gate_key]
    satisfied: List[ItemResult] = []
    missing: List[ItemResult] = []
    for item in gate.items:
        ok, detail = False, ""
        if workspace is not None and item.kind == "artifact":
            ok, detail = _artifact(item, workspace)
        elif workspace is not None and item.kind == "content":
            ok, detail = _content(item, workspace)
        else:
            detail = item.manual_hint or "manual evidence required"
        (satisfied if ok else missing).append(
            ItemResult(item.id, item.text, ok, detail)
        )
    total = len(gate.items)
    coverage = len(satisfied) / total if total else 0.0
    if not missing:
        verdict = "pass"
    elif any(i.kind == "manual" for i in gate.items) and coverage > 0:
        verdict = "manual-required"
    else:
        verdict = "gap"
    return GateReport(gate.key, gate.name, satisfied, missing, verdict, coverage)


# ── Gate catalogue ──────────────────────────────────────────────────────────

GATES: Dict[str, Gate] = {}


def _g(g: Gate) -> Gate:
    GATES[g.key] = g
    return g


_g(Gate("do-178c", "DO-178C — airborne software", "aerospace",
    "Software considerations in airborne systems and equipment certification.",
    [
        GateItem("do1", "Software level (DAL) & requirements allocation documented",
                 "content", content_needles=("dal", "software level")),
        GateItem("do2", "Software requirements specification exists",
                 "artifact", pattern="glob:**/*requirement*"),
        GateItem("do3", "Requirements-based test evidence",
                 "artifact", pattern="glob:**/test*"),
        GateItem("do4", "Structural coverage analysis (MC/DC for DAL A)",
                 "content", content_needles=("coverage", "mc/dc")),
        GateItem("do5", "Configuration management & problem reports log",
                 "content", content_needles=("configuration management", "problem report")),
        GateItem("do6", "Independent verification & traceability matrix", "manual",
                 manual_hint="Produce requirements↔tests traceability matrix with independent review sign-off."),
    ],
    reference="RTCA DO-178C / EUROCAE ED-12C"))

_g(Gate("iso-26262", "ISO 26262 — automotive functional safety", "embedded",
    "Road vehicles — functional safety (ASIL lifecycle).",
    [
        GateItem("iso1", "HARA / hazard analysis & ASIL classification",
                 "content", content_needles=("hazard", "asil")),
        GateItem("iso2", "Safety goals & functional safety requirements",
                 "content", content_needles=("safety goal",)),
        GateItem("iso3", "Safety mechanism / diagnostic coverage evidence",
                 "content", content_needles=("diagnostic coverage", "safety mechanism")),
        GateItem("iso4", "Unit & integration test reports",
                 "artifact", pattern="glob:**/test*"),
        GateItem("iso5", "FMEA or FTA analysis document",
                 "content", content_needles=("fmea", "fault tree")),
        GateItem("iso6", "Independent safety assessment sign-off", "manual",
                 manual_hint="Arrange functional safety manager / assessor review."),
    ],
    reference="ISO 26262:2018"))

_g(Gate("iec-62304", "IEC 62304 — medical device software lifecycle", "biomedical",
    "Medical device software — software life cycle processes.",
    [
        GateItem("m1", "Software safety classification (A/B/C)",
                 "content", content_needles=("software safety classification",)),
        GateItem("m2", "Software development plan & SRS",
                 "content", content_needles=("software requirements",)),
        GateItem("m3", "Risk management file (ISO 14971)",
                 "content", content_needles=("risk", "hazard")),
        GateItem("m4", "Verification & integration test records",
                 "artifact", pattern="glob:**/test*"),
        GateItem("m5", "Anomaly / change-control tracking",
                 "content", content_needles=("change control", "anomaly")),
        GateItem("m6", "Cybersecurity (IEC 81001-5-1 / SBOM) evidence",
                 "content", content_needles=("sbom", "cybersecurity")),
    ],
    reference="IEC 62304:2006+A1; ISO 14971; IEC 60601-1"))

_g(Gate("iec-60601", "IEC 60601 — medical electrical equipment safety", "biomedical",
    "General safety & essential performance of medical electrical equipment.",
    [
        GateItem("e1", "Risk management file present",
                 "content", content_needles=("risk",)),
        GateItem("e2", "Electrical safety / leakage current test report",
                 "content", content_needles=("leakage current", "dielectric")),
        GateItem("e3", "EMC test evidence (IEC 60601-1-2)",
                 "content", content_needles=("emc",)),
        GateItem("e4", "Biocompatibility evidence (ISO 10993) for patient contact",
                 "content", content_needles=("biocompatib",)),
        GateItem("e5", "Usability / human factors (IEC 62366) study", "manual",
                 manual_hint="Plan formative/summative usability testing."),
    ],
    reference="IEC 60601-1, 60601-1-2, ISO 10993, IEC 62366-1"))

_g(Gate("iec-61508", "IEC 61508 — functional safety (E/E/PE)", "embedded",
    "Functional safety of electrical/electronic/programmable electronic systems.",
    [
        GateItem("s1", "SIL determination documented",
                 "content", content_needles=("sil",)),
        GateItem("s2", "Safety requirements specification",
                 "content", content_needles=("safety requirement",)),
        GateItem("s3", "Diagnostic coverage / proof test interval analysis",
                 "content", content_needles=("diagnostic coverage", "proof test")),
        GateItem("s4", "Validation test records",
                 "artifact", pattern="glob:**/test*"),
        GateItem("s5", "Systematic capability / tool confidence assessment", "manual",
                 manual_hint="Document tool confidence (TCL) and systematic capability."),
    ],
    reference="IEC 61508:2010"))

_g(Gate("ipc-2221", "IPC-2221 — PCB design (generic)", "electrical",
    "Generic standard on printed board design — trace sizing, clearance, creepage.",
    [
        GateItem("p1", "PCB design files present",
                 "artifact", pattern="glob:**/*.kicad_pcb"),
        GateItem("p2", "Design rules (clearance/creepage) documented",
                 "content", content_needles=("clearance", "creepage")),
        GateItem("p3", "Trace width vs current verified (IPC-2221 charts/formula)",
                 "content", content_needles=("trace width", "temperature rise")),
        GateItem("p4", "ERC/DRC report clean",
                 "content", content_needles=("drc", "erc")),
        GateItem("p5", "Netlist / schematic present",
                 "artifact", pattern="glob:**/*.kicad_sch"),
    ],
    reference="IPC-2221A / IPC-2223 / IPC-A-600"))

_g(Gate("asce-7", "ASCE 7 — structural design loads", "civil",
    "Minimum design loads and associated criteria for buildings/structures.",
    [
        GateItem("c1", "Load assumptions documented (dead/live/wind/seismic)",
                 "content", content_needles=("dead load", "live load")),
        GateItem("c2", "Member sizing / capacity calculations",
                 "content", content_needles=("moment", "stress")),
        GateItem("c3", "Factor of safety against yield/buckling recorded",
                 "content", content_needles=("factor of safety", "fos")),
        GateItem("c4", "Analysis model / FEA report",
                 "artifact", pattern="glob:**/*.inp"),
        GateItem("c5", "Seismic/wind per ASCE 7 risk category", "manual",
                 manual_hint="Confirm risk category and site-specific loads with a licensed PE."),
    ],
    reference="ASCE/SEI 7; AISC steel; ACI concrete"))

_g(Gate("fos-mechanical", "Factor-of-safety gate (mechanical)", "mechanical",
    "Deterministic mechanical sanity gate: every load-bearing path should show FOS ≥ target against yield and buckling, with fatigue noted for cyclical loads.",
    [
        GateItem("f1", "Stress calculations present for load paths",
                 "content", content_needles=("stress",)),
        GateItem("f2", "Factor of safety values recorded",
                 "content", content_needles=("factor of safety", "fos")),
        GateItem("f3", "Buckling check for columns/slender members",
                 "content", content_needles=("buckling",)),
        GateItem("f4", "Fatigue analysis for cyclical loading",
                 "content", content_needles=("fatigue",)),
        GateItem("f5", "Material data (E, yield) specified",
                 "content", content_needles=("young", "yield")),
    ],
    reference="Mechanics of materials; Shigley's Mechanical Engineering Design"))


def gates_for_domain(domain: str) -> List[str]:
    return [k for k, g in GATES.items() if g.domain == domain]
