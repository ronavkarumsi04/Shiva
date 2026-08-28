"""Vishvakarma — multi-domain engineering for Shiva.

Named for the divine architect-engineer of the Hindu pantheon, this prong
makes Trishula an engineer for *physical* disciplines, not just software:

* :mod:`trishula.engineering.constants` / ``units`` — physical constants and
  SI unit conversions;
* :mod:`trishula.engineering.formulas` — a verified, cross-domain formula
  library (electrical, mechanical, thermal, aerospace, biomedical, chemical,
  optical, controls, civil) with unit-documented arguments;
* :mod:`trishula.engineering.domains` — workspace/goal detection of the
  engineering domains in play;
* :mod:`trishula.engineering.safety` — certification gate checklists
  (DO-178C, ISO 26262, IEC 60601/62304/14971, IEC 61508, IPC-2221, …) with
  deterministic artifact checks;
* :mod:`trishula.engineering.toolchains` — detection and command planning for
  simulators/analyzers (ngspice, iverilog/ghdl, PlatformIO, KiCad ERC/DRC,
  CalculiX/OpenFOAM, OpenSCAD, Swift, …);
* :mod:`trishula.engineering.ios` — Xcode/iOS testing: native
  ``xcodebuild`` on macOS, SwiftPM/jest/flutter logic tests anywhere, and
  automated macOS-CI / remote-Mac paths for simulator tests on Windows/Linux.

Everything is stdlib-only and works offline; missing toolchains degrade to a
precise "install this / run this" plan rather than pretending to simulate.
"""

from trishula.engineering.constants import PHYSICAL_CONSTANTS
from trishula.engineering.units import to_si, from_si, CONVERSIONS
from trishula.engineering.formulas import (
    FORMULAS, Formula, calculate, list_formulas, formulas_for_domain,
)
from trishula.engineering.domains import Domain, DOMAINS, detect_domains
from trishula.engineering.safety import GATES, GateReport, evaluate_gate
from trishula.engineering.toolchains import Toolchain, detect_toolchains, plan_simulations
from trishula.engineering.ios import (
    IOSProject, detect_ios_project, ios_test_plan, IOSTestPlan,
)

__all__ = [
    "PHYSICAL_CONSTANTS",
    "to_si", "from_si", "CONVERSIONS",
    "FORMULAS", "Formula", "calculate", "list_formulas", "formulas_for_domain",
    "Domain", "DOMAINS", "detect_domains",
    "GATES", "GateReport", "evaluate_gate",
    "Toolchain", "detect_toolchains", "plan_simulations",
    "IOSProject", "detect_ios_project", "ios_test_plan", "IOSTestPlan",
]
