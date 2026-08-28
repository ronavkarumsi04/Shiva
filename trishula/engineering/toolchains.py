"""Engineering toolchain detection and simulation command planning.

This is the honest middle ground between "run the physics" and "guess":
Trishula detects which open toolchains are actually installed, can run them
where they exist (SPICE/HDL/build tools are deterministic and CLI-friendly),
and — where it cannot run a heavy GUI/FEM suite — emits a precise,
reproducible command plan for the user's workstation or CI instead of
faking results.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from trishula.tools.workspace import Workspace


@dataclass
class Toolchain:
    key: str
    name: str
    domain: str
    commands: tuple[str, ...]          # candidate binaries to probe
    purpose: str
    # returns shell commands appropriate to files in the workspace
    plan: Callable[[List[Path]], List[str]] = lambda files: []
    runnable_headless: bool = True


def _probe(commands: tuple[str, ...]) -> Optional[str]:
    for c in commands:
        if shutil.which(c):
            return c
    return None


# ── planners ────────────────────────────────────────────────────────────────

def _ngspice_plan(files: List[Path]) -> List[str]:
    decks = [f for f in files if f.suffix.lower() in {".cir", ".sp", ".spice", ".net"}]
    return [f"ngspice -b -r {f.with_suffix('.raw').name} {f.name}" for f in decks]


def _iverilog_plan(files: List[Path]) -> List[str]:
    vs = [f.name for f in files if f.suffix.lower() == ".v"]
    tbs = [f for f in vs if "tb" in f.lower() or "testbench" in f.lower()]
    if not vs:
        return []
    targets = tbs or vs
    out = [
        "iverilog -o sim.vvp " + " ".join(targets),
        "vvp sim.vvp",
        "gtkwave sim.vcd  # view waveform",
    ]
    return out


def _ghdl_plan(files: List[Path]) -> List[str]:
    vhd = [f.name for f in files if f.suffix.lower() in {".vhd", ".vhdl"}]
    if not vhd:
        return []
    return [
        "ghdl -a " + " ".join(vhd),
        "ghdl -e tb_top  # your testbench entity",
        "ghdl -r tb_top --wave=wave.ghw",
    ]


def _platformio_plan(files: List[Path]) -> List[str]:
    has_ini = any(f.name == "platformio.ini" for f in files)
    if not has_ini:
        return ["# No platformio.ini detected — create one with: pio project init --board <id>"]
    return [
        "pio run                  # build firmware",
        "pio test                 # run unit tests (native + on-target)",
        "pio run -t upload        # flash",
    ]


def _kicad_plan(files: List[Path]) -> List[str]:
    pro = [f for f in files if f.suffix.lower() == ".kicad_pcb"]
    sch = [f for f in files if f.suffix.lower() == ".kicad_sch"]
    cmds: List[str] = []
    if sch:
        cmds.append(f"kicad-cli sch erc {sch[0].name} --output erc.rpt --severity-all")
    if pro:
        cmds.append(f"kicad-cli pcb drc {pro[0].name} --output drc.rpt --severity-all")
        cmds.append(f"kicad-cli pcb export gerbers {pro[0].name} -o gerbers/")
    return cmds


def _openscad_plan(files: List[Path]) -> List[str]:
    scad = [f.name for f in files if f.suffix.lower() == ".scad"]
    return [f"openscad -o {Path(s).with_suffix('.stl').name} {s}" for s in scad]


def _calculix_plan(files: List[Path]) -> List[str]:
    inp = [f.name for f in files if f.suffix.lower() in {".inp", ".fea"}]
    return [f"ccx -i {f}  # requires CalculiX ccx + a prepared .inp mesh/bcs" for f in inp]


def _openfoam_plan(files: List[Path]) -> List[str]:
    is_case = any(f.name == "controlDict" for f in files)
    if not is_case:
        return ["# OpenFOAM case requires constant/, system/controlDict, 0/ directories"]
    return ["blockMesh", "checkMesh", "simpleFoam  # or rhoSimpleFoam / icoFoam per case"]


def _xcodebuild_plan(files: List[Path]) -> List[str]:
    from trishula.engineering.ios import detect_ios_project, ios_test_plan
    # This planner runs against workspace paths; handled by ios module directly.
    return []


_REGISTRY: Dict[str, Toolchain] = {}


def _tc(t: Toolchain) -> Toolchain:
    _REGISTRY[t.key] = t
    return t


_tc(Toolchain("ngspice", "Ngspice SPICE simulator", "electrical",
              ("ngspice",), "DC/AC/transient circuit simulation", _ngspice_plan))
_tc(Toolchain("iverilog", "Icarus Verilog + GTKWave", "electrical",
              ("iverilog", "vvp", "gtkwave"), "Verilog RTL simulation", _iverilog_plan))
_tc(Toolchain("ghdl", "GHDL VHDL simulator", "electrical",
              ("ghdl",), "VHDL simulation", _ghdl_plan))
_tc(Toolchain("kicad-cli", "KiCad ERC/DRC + fabrication exports", "electrical",
              ("kicad-cli",), "Schematic ERC, PCB DRC, Gerber export", _kicad_plan,
              runnable_headless=True))
_tc(Toolchain("platformio", "PlatformIO (embedded)", "embedded",
              ("pio", "platformio"), "Firmware build/test/flash", _platformio_plan))
_tc(Toolchain("arduino-cli", "Arduino CLI", "embedded",
              ("arduino-cli",), "Arduino build/upload", lambda f: ["arduino-cli compile ."] if f else []))
_tc(Toolchain("qemu", "QEMU (emulation)", "embedded",
              ("qemu-system-arm", "qemu-system-riscv32"), "Hardware-in-loop emulation",
              lambda f: []))
_tc(Toolchain("openscad", "OpenSCAD", "mechanical",
              ("openscad",), "Parametric CAD to STL render", _openscad_plan))
_tc(Toolchain("calculix", "CalculiX FEM solver", "mechanical",
              ("ccx", "cgx"), "Linear/nonlinear FEA", _calculix_plan,
              runnable_headless=True))
_tc(Toolchain("openfoam", "OpenFOAM CFD", "aerospace",
              ("simpleFoam", "icoFoam", "rhoSimpleFoam"), "CFD meshing & solving",
              _openfoam_plan, runnable_headless=True))
_tc(Toolchain("freecad", "FreeCAD", "mechanical",
              ("freecad", "freecadcmd"), "CAD import/inspection (headless limited)",
              lambda f: [], runnable_headless=False))
_tc(Toolchain("xcodebuild", "Xcode (iOS/macOS)", "software",
              ("xcodebuild",), "iOS/macOS build & XCTest (macOS only)",
              _xcodebuild_plan, runnable_headless=True))
_tc(Toolchain("swift", "Swift / SwiftPM", "software",
              ("swift",), "SwiftPM build & test (cross-platform logic tests)",
              lambda f: ["swift test"] if any(f.name == "Package.swift" for f in f) else []))
_tc(Toolchain("flutter", "Flutter", "software",
              ("flutter",), "Flutter app tests",
              lambda f: ["flutter test"] if any(f.name == "pubspec.yaml" for f in f) else []))
_tc(Toolchain("node", "Node / npm", "software",
              ("npm", "node"), "React Native / JS unit tests",
              lambda f: ["npm test"] if any(f.name == "package.json" for f in f) else []))


def detect_toolchains() -> Dict[str, str]:
    """Return ``{toolchain_key: binary_path}`` for everything installed."""
    found: Dict[str, str] = {}
    for key, tc in _REGISTRY.items():
        probe = _probe(tc.commands)
        if probe:
            found[key] = probe
    return found


def plan_simulations(
    workspace: Workspace,
    *,
    domains: Optional[Dict[str, float]] = None,
) -> List[dict]:
    """For each relevant toolchain, report availability and planned commands.

    Output is a list of::
        {"toolchain", "name", "domain", "available", "binary", "runnable_here",
         "commands", "note"}
    """
    from trishula.engineering.domains import detect_domains

    domains = domains if domains is not None else detect_domains(workspace)
    installed = detect_toolchains()
    files = workspace.walk_files(max_files=4000)

    plans: List[dict] = []
    active_domains = {k for k, v in domains.items() if v >= 0.25} or set(domains)
    for key, tc in _REGISTRY.items():
        if tc.domain not in active_domains and tc.domain != "software":
            continue
        commands = tc.plan(files)
        available = key in installed
        # Only show toolchains that have something to act on OR are installed.
        if not commands and not available:
            continue
        note = ""
        if not available:
            note = (
                f"Not installed here — install {tc.name} and run the commands "
                f"on a machine that has it (or via CI; see ios module for remote)."
            )
        elif not tc.runnable_headless:
            note = "Installed, but this tool is GUI/interactive — run manually."
        plans.append({
            "toolchain": key,
            "name": tc.name,
            "domain": tc.domain,
            "available": available,
            "binary": installed.get(key, ""),
            "runnable_here": available and tc.runnable_headless,
            "commands": commands,
            "note": note,
        })
    return plans
