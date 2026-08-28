"""Engineering domain detection.

Given a workspace (and/or a natural-language goal), decide which physical
engineering disciplines are in play so the right formulas, safety gates,
toolchains and team roles are offered. Detection is file-extension driven
plus content keyword scanning — deterministic and offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from trishula.tools.workspace import Workspace


@dataclass(frozen=True)
class Domain:
    key: str
    name: str
    description: str
    extensions: tuple[str, ...]
    markers: tuple[str, ...]      # filenames
    keywords: tuple[str, ...]     # goal / content hints
    # primary toolchains + gate keys (looked up elsewhere)
    toolchains: tuple[str, ...] = ()
    gate: str = ""


DOMAINS: Dict[str, Domain] = {
    "electrical": Domain(
        "electrical", "Electrical / Electronics",
        "Circuits, PCBs, analog/digital electronics, power.",
        (".sch", ".kicad_sch", ".kicad_pcb", ".brd", ".edf", ".cir", ".net",
         ".dsn", ".psx", ".jhd"),
        ("kicad_pcb", "schematic", "netlist", ".opj"),
        ("circuit", "schematic", "pcb", "voltage", "current", "resistor",
         "capacitor", "transistor", "smps", "power supply", "op-amp", "ohm"),
        toolchains=("ngspice", "kicad-cli", "iverilog", "gtkwave"),
        gate="ipc-2221",
    ),
    "embedded": Domain(
        "embedded", "Embedded / Firmware",
        "Microcontrollers, firmware, RTOS, register-level software.",
        (".ino", ".asm", ".ioc", ".dts", ".dtsi", ".ld", ".s", ".S"),
        ("platformio.ini", "CMakeLists.txt", "Makefile", ".cproject"),
        ("firmware", "embedded", "microcontroller", "stm32", "esp32", "arduino",
         "register", "interrupt", "dma", "bootloader", "can bus", "sensor"),
        toolchains=("platformio", "arduino-cli", "qemu"),
        gate="iec-61508",
    ),
    "mechanical": Domain(
        "mechanical", "Mechanical / CAD",
        "Mechanisms, structures, CAD models, structural analysis.",
        (".step", ".stp", ".iges", ".stl", ".obj", ".f3d", ".scad", ".sldprt",
         ".iam", ".3dm", ".fcstd", ".dwg", ".dxf", ".fea", ".inp"),
        ("assembly", "fea", "mesh"),
        ("gearing", "shaft", "bearing", "torque", "stress", "deflection", "cad",
         "fastener", "mechanism", "clamp", "bracket", "fea", "fatigue"),
        toolchains=("calculix", "openscad", "freecad"),
        gate="factor-of-safety",
    ),
    "aerospace": Domain(
        "aerospace", "Aerospace / Aeronautical",
        "Flight vehicles, aerodynamics, propulsion, orbits, airframes.",
        (".bdf", ".nas", ".msh", ".foam"),
        ("airframe", "controlSurfaces"),
        ("airfoil", "lift", "drag", "thrust", "mach", "wing", "aircraft",
         "rocket", "satellite", "orbit", "delta-v", "reynolds", "stall",
         "propulsion", "uav", "drone"),
        toolchains=("openfoam", "calculix"),
        gate="do-178c",
    ),
    "biomedical": Domain(
        "biomedical", "Biomedical / Medical Device",
        "Medical devices, clinical engineering, physiology, biotech.",
        (".gcode",),
        ("ifus", "dicom"),
        ("medical device", "clinical", "patient", "fda", "iso 13485",
         "biocompatible", "steril", "physiologic", "ecg", "ekg", "blood pressure",
         "catheter", "implant", "wearable sensor", "dose", "trial"),
        toolchains=(),
        gate="iec-62304",
    ),
    "civil": Domain(
        "civil", "Civil / Structural",
        "Buildings, bridges, geotechnical, load-bearing structures.",
        (".dwg", ".ifc", ".rdl"),
        ("foundation",),
        ("beam", "column", "load bearing", "concrete", "reinforcement",
         "seismic", "foundation", "bridge", "bending moment", "civil",
         "rebar", "span"),
        toolchains=("calculix",),
        gate="asce-7",
    ),
    "chemical": Domain(
        "chemical", "Chemical / Process",
        "Process engineering, chemistry, reactors, piping.",
        (".mop",),
        ("flowsheet",),
        ("reactor", "distillation", "molar", "stoichiometry", "ph balance",
         "viscosity", "heat exchanger", "piping", "pump curve", "catalyst"),
        toolchains=(),
        gate="",
    ),
    "optical": Domain(
        "optical", "Optical / Photonic",
        "Lenses, lasers, fiber optics, imaging systems.",
        (".zmx", ".os2", ".lens"),
        ("raytrace",),
        ("lens", "laser", "wavelength", "fiber optic", "refractive index",
         "snell", "aberration", "aperture", "collimated", "photodiode"),
        toolchains=(),
        gate="",
    ),
    "thermal": Domain(
        "thermal", "Thermal / Fluid",
        "Heat transfer, CFD, thermodynamics, cooling.",
        (".foam", ".msh"),
        ("heatSink",),
        ("heat sink", "thermal", "conduction", "convection", "cfd",
         "cooling", "reynolds", "nusselt", "junction temperature", "airflow"),
        toolchains=("openfoam",),
        gate="",
    ),
    "controls": Domain(
        "controls", "Controls / Robotics",
        "Control systems, automation, robotics, state estimation.",
        (".urdf", ".sdf"),
        ("pidTuning",),
        ("pid", "control loop", "servo", "kalman", "state space", "stability",
         "bandwidth", "bode", "step response", "actuator", "robotics"),
        toolchains=(),
        gate="",
    ),
    "software": Domain(
        "software", "Software / Mobile",
        "Application software, mobile apps, firmware-adjacent code.",
        (".swift", ".xcodeproj", ".xcworkspace", ".pbxproj", ".dart", ".kt", ".java"),
        ("Podfile", "Package.swift", "pubspec.yaml"),
        ("ios", "xcode", "swift", "iphone", "ipad", "android", "app", "flutter",
         "react native", "unit test"),
        toolchains=("xcodebuild", "xcrun", "swift", "flutter"),
        gate="",
    ),
}

_WORDS_WORTH_SCANNING = (".c", ".h", ".cpp", ".hpp", ".ino", ".py", ".md", ".txt")


def detect_domains(
    workspace: Workspace | None = None,
    goal: str = "",
    *,
    max_files: int = 400,
) -> Dict[str, float]:
    """Return ``{domain_key: confidence 0..1}`` sorted desc, only >0."""
    scores: Dict[str, float] = {k: 0.0 for k in DOMAINS}

    # 1. extensions + markers are strong signals
    if workspace is not None:
        try:
            files = workspace.walk_files(max_files=max_files)
        except Exception:  # noqa: BLE001
            files = []
        for f in files:
            name = f.name.lower()
            suffix = f.suffix.lower()
            for d in DOMAINS.values():
                if suffix in d.extensions or any(m in name for m in d.markers):
                    scores[d.key] += 3.0
            # light content scan for small source/text files
            if suffix in _WORDS_WORTH_SCANNING:
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")[:40000].lower()
                except OSError:
                    continue
                for d in DOMAINS.values():
                    hits = sum(1 for kw in d.keywords if kw in text)
                    scores[d.key] += 0.5 * min(hits, 6)

    # 2. goal keywords
    goal_l = (goal or "").lower()
    for d in DOMAINS.values():
        hits = sum(1 for kw in d.keywords if kw in goal_l)
        scores[d.key] += 2.0 * min(hits, 4)

    # normalize
    if scores:
        top = max(scores.values()) or 1.0
        ranked = {k: round(min(1.0, v / top), 3) for k, v in scores.items() if v > 0}
    else:
        ranked = {}
    return dict(sorted(ranked.items(), key=lambda kv: kv[1], reverse=True))


def primary_domains(confidences: Dict[str, float], threshold: float = 0.25) -> List[str]:
    return [k for k, v in confidences.items() if v >= threshold]
