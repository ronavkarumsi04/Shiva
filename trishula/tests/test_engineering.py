"""Tests for the Vishvakarma prong: formulas, units, domains, gates,
toolchains, iOS planning, engineering roles, and engineering tools."""

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.core.config import TrishulaConfig
from trishula.engineering import (
    FORMULAS, calculate, list_formulas, formulas_for_domain,
    DOMAINS, detect_domains,
    GATES, evaluate_gate,
    detect_toolchains, plan_simulations,
    detect_ios_project, ios_test_plan,
)
from trishula.engineering.units import to_si, from_si
from trishula.tools.workspace import Workspace
from trishula.tools.builtin import build_registry
from trishula.team.roles import RoleCatalog
from trishula.team.planner import TeamPlanner


class UnitsTests(unittest.TestCase):
    def test_lengths(self):
        self.assertAlmostEqual(to_si(1, "mm"), 1e-3)
        self.assertAlmostEqual(to_si(1, "mil"), 2.54e-5)
        self.assertAlmostEqual(to_si(1, "um"), 1e-6)
        self.assertAlmostEqual(to_si(1, "ft"), 0.3048)

    def test_pressure_and_power(self):
        self.assertAlmostEqual(to_si(1, "psi"), 6894.757, places=2)
        self.assertAlmostEqual(to_si(1, "hp"), 745.7, places=1)

    def test_temperature_affine(self):
        self.assertAlmostEqual(to_si(0, "C"), 273.15)
        self.assertAlmostEqual(to_si(32, "F"), 273.15)
        self.assertAlmostEqual(from_si(373.15, "C"), 100.0)

    def test_speed(self):
        self.assertAlmostEqual(to_si(100, "km/h"), 27.7778, places=3)


class FormulaLibraryTests(unittest.TestCase):
    def test_library_covers_all_claimed_domains(self):
        domains = {f.domain for f in FORMULAS.values()}
        for d in ("electrical", "mechanical", "aerospace", "biomedical",
                  "embedded", "optical", "controls", "chemical",
                  "thermal", "fluid"):
            self.assertIn(d, domains, f"missing domain {d}")
        self.assertGreaterEqual(len(FORMULAS), 45)

    def test_ohms_and_power(self):
        self.assertAlmostEqual(calculate("ohms_law", i=2.0, r=10.0), 20.0)
        self.assertAlmostEqual(calculate("power_electric", v=12, i=3), 36.0)

    def test_resistor_networks(self):
        self.assertAlmostEqual(calculate("resistors_series", resistances=[10, 20, 30]), 60.0)
        self.assertAlmostEqual(calculate("resistors_parallel", resistances=[100, 100, 100]), 33.3333, places=3)

    def test_energy_storage(self):
        self.assertAlmostEqual(calculate("capacitor_energy", c=1e-3, v=10), 0.05, places=4)
        self.assertAlmostEqual(calculate("inductor_energy", l=1e-3, i=2), 0.002, places=5)

    def test_pcb_trace_units_and_sanity(self):
        # 0.25 mm wide, 1 oz copper ~35 um, 10C rise should carry roughly ~0.9A
        i = calculate("pcb_trace_current", width=[0.25, "mm"], thickness=[35, "um"], temp_rise=10)
        self.assertGreater(i, 0.5)
        self.assertLess(i, 2.0)
        # wider trace carries more
        i2 = calculate("pcb_trace_current", width=[1.0, "mm"], thickness=[35, "um"], temp_rise=10)
        self.assertGreater(i2, i)

    def test_structural_mechanics(self):
        # 50x100 mm rectangular beam
        I = calculate("second_moment_rectangle", b=0.05, h=0.1)
        self.assertAlmostEqual(I, 0.05 * 0.1 ** 3 / 12, places=10)
        stress = calculate("axial_stress", f=10000, area=0.01)
        self.assertAlmostEqual(stress, 1e6)
        fos = calculate("factor_of_safety", capacity=250e6, applied=100e6)
        self.assertAlmostEqual(fos, 2.5)
        # stronger beam deflects less
        d_steel = calculate("beam_deflection_cantilever_end", f=1000, length=1, e=200e9, i=I)
        d_alu = calculate("beam_deflection_cantilever_end", f=1000, length=1, e=69e9, i=I)
        self.assertGreater(d_alu, d_steel)

    def test_euler_buckling(self):
        # Doubling length quarters the critical load.
        I = 1e-8
        p1 = calculate("buckling_euler", e=200e9, i=I, length=1, k=1)
        p2 = calculate("buckling_euler", e=200e9, i=I, length=2, k=1)
        self.assertAlmostEqual(p2 / p1, 0.25, places=6)

    def test_aerospace_orbital_values(self):
        mu, r = 3.986004418e14, 6.371e6 + 400e3
        v = calculate("orbital_velocity", mu=mu, r=r)
        t = calculate("orbital_period", mu=mu, r=r)
        self.assertAlmostEqual(v / 1000, 7.67, places=1)     # ~7.7 km/s LEO
        self.assertAlmostEqual(t / 60, 92.4, places=0)       # ~92 min
        dv = calculate("rocket_delta_v", ve=3000, m0=1000, mf=300)
        self.assertAlmostEqual(dv, 3000 * math.log(1000 / 300), places=4)
        # dynamic pressure at sea level, 340 m/s
        q = calculate("dynamic_pressure", rho=1.225, v=340)
        self.assertAlmostEqual(q, 0.5 * 1.225 * 340 ** 2, places=1)
        lift = calculate("lift_force", rho=1.225, v=50, area=20, cl=0.8)
        self.assertGreater(lift, 0)
        a = calculate("speed_of_sound", gamma=1.4, r_specific=287.05, t=288.15)
        self.assertAlmostEqual(a, 340.3, places=1)

    def test_biomedical(self):
        bsa = calculate("bsa_du_bois", mass_kg=70, height_m=1.75)
        self.assertAlmostEqual(bsa, 1.85, places=2)
        co = calculate("cardiac_output", stroke_volume_m3=7e-5, hr=72)
        self.assertAlmostEqual(co, 5.04, places=2)        # L/min
        bmi = calculate("bmi", mass_kg=70, height_m=1.75)
        self.assertAlmostEqual(bmi, 70 / 1.75 ** 2, places=3)

    def test_optics(self):
        ang = calculate("snells_angle", n1=1.0, n2=1.5, theta1_deg=30)
        self.assertAlmostEqual(ang, 19.47, places=2)
        crit = calculate("critical_angle", n1=1.5, n2=1.0)
        self.assertAlmostEqual(crit, 41.81, places=2)

    def test_fluid_thermal(self):
        re = calculate("reynolds_number", rho=1000, v=2, d=0.05, mu=1e-3)
        self.assertAlmostEqual(re, 100_000)
        q = calculate("conduction_heat", k=50, area=0.01, delta_t=20, length=0.1)
        self.assertAlmostEqual(q, 100.0)

    def test_embedded_adc(self):
        lsb = calculate("adc_lsb", v_ref=3.3, bits=12)
        self.assertAlmostEqual(lsb, 3.3 / 4096, places=7)
        v = calculate("adc_code_to_voltage", code=2048, v_ref=3.3, bits=12)
        self.assertAlmostEqual(v, 1.65, places=3)

    def test_unknown_formula_raises(self):
        with self.assertRaises(KeyError):
            calculate("nope_not_real", x=1)


class DomainDetectionTests(unittest.TestCase):
    def test_detects_electrical_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("board/psu.kicad_sch", "(kicad_sch)")
            ws.write("board/board.kicad_pcb", "(kicad_pcb)")
            conf = detect_domains(ws, goal="design the switching power supply")
            self.assertIn("electrical", conf)
            self.assertEqual(primary(conf), "electrical")

    def test_detects_aerospace_from_goal(self):
        with tempfile.TemporaryDirectory() as d:
            conf = detect_domains(Workspace(d), goal="analyze rocket nozzle thrust and orbit delta-v")
            self.assertIn("aerospace", conf)

    def test_detects_ios_software(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("App/App.xcodeproj/project.pbxproj", "// xcode")
            conf = detect_domains(ws, goal="xcode swift UI tests")
            self.assertIn("software", conf)


def primary(conf):
    return next(iter(conf))


class SafetyGateTests(unittest.TestCase):
    def test_gate_catalogue_present(self):
        for key in ("do-178c", "iso-26262", "iec-62304", "iec-60601",
                    "iec-61508", "ipc-2221", "asce-7", "fos-mechanical"):
            self.assertIn(key, GATES)

    def test_empty_project_gap(self):
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_gate("ipc-2221", Workspace(d))
            self.assertIn(report.verdict, ("gap", "manual-required"))
            self.assertLess(report.coverage, 0.5)

    def test_evidence_increases_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("board.kicad_pcb", "(kicad_pcb)")
            ws.write("board.kicad_sch", "(kicad_sch)")
            ws.write("design_rules.md",
                     "clearance 0.2mm, creepage requirements checked; "
                     "trace width vs temperature rise verified; DRC and ERC reports clean")
            report = evaluate_gate("ipc-2221", ws)
            self.assertGreater(report.coverage, 0.4)
            self.assertTrue(any(r.id == "p4" and r.satisfied for r in report.satisfied))

    def test_gate_dict_shape(self):
        with tempfile.TemporaryDirectory() as d:
            dct = evaluate_gate("do-178c", Workspace(d)).to_dict()
            self.assertEqual(dct["gate"], "do-178c")
            self.assertIn("missing", dct)


class ToolchainTests(unittest.TestCase):
    def test_detect_returns_dict(self):
        found = detect_toolchains()
        self.assertIsInstance(found, dict)

    def test_plan_reports_missing_toolchains_honestly(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("amp.cir", "* SPICE netlist\nV1 1 0 DC 5\nR1 1 0 1k\n")
            plans = plan_simulations(ws)
            by_key = {p["toolchain"]: p for p in plans}
            self.assertIn("ngspice", by_key)
            if not by_key["ngspice"]["available"]:
                self.assertTrue(by_key["ngspice"]["note"])
                self.assertTrue(any("ngspice -b" in c for c in by_key["ngspice"]["commands"]))


class IOSPlanTests(unittest.TestCase):
    def _xcode_workspace(self, d):
        ws = Workspace(d)
        ws.write("App/App.xcodeproj/project.pbxproj", "// !$*UTF8*$!")
        ws.write("App/App/App.swift", "import UIKit\n")
        return ws

    def test_detect_xcode_project(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self._xcode_workspace(d)
            proj = detect_ios_project(ws)
            self.assertIsNotNone(proj)
            self.assertEqual(proj.kind, "xcode")
            self.assertIn(".xcodeproj", proj.xcodeproj)

    def test_detect_swiftpm_and_rn(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("Package.swift", "// swift-tools-version")
            ws.write("Sources/lib.swift", "func x() {}")
            proj = detect_ios_project(ws)
            self.assertEqual(proj.kind, "swiftpm")
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("package.json", "{}")
            ws.write("ios/Podfile", "")
            proj = detect_ios_project(ws)
            self.assertEqual(proj.kind, "react-native")

    def test_plan_on_linux_windows_uses_ci(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self._xcode_workspace(d)
            plan = ios_test_plan(ws)
            is_mac = sys.platform == "darwin"
            self.assertEqual(plan.can_run_simulator_tests_locally, is_mac and bool(plan.can_run_simulator_tests_locally))
            dct = plan.to_dict()
            if not is_mac:
                self.assertEqual(dct["simulator_path"], "ci")
                self.assertTrue(dct["limitations"])  # honest about the boundary
            self.assertIn("xcodebuild test", plan.ci_workflow_yaml)
            self.assertIn("macos", plan.ci_workflow_yaml)
            self.assertTrue(plan.remote_mac_commands)

    def test_write_ci_workflow_via_tool(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self._xcode_workspace(d)
            reg = build_registry(ws)
            r = reg.call("ios_test_plan", {"write_ci": True})
            self.assertTrue(r.ok, r.error)
            self.assertTrue(ws.exists(".github/workflows/ios-ci.yml"))

    def test_swiftpm_offered_everywhere(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("Package.swift", "// swift-tools-version:5.9")
            plan = ios_test_plan(ws)
            labels = [o.label for o in plan.local]
            self.assertTrue(any("SwiftPM" in l for l in labels))


class EngineeringRolesAndPlannerTests(unittest.TestCase):
    def test_roles_registered(self):
        names = RoleCatalog().names()
        for role in ("ee", "embedded", "mechanical", "aerospace", "biomedical", "compliance"):
            self.assertIn(role, names)

    def test_engineering_team_plan_inserts_specialists_and_gate(self):
        with tempfile.TemporaryDirectory() as d:
            planner = TeamPlanner(d)
            plan = planner.plan("design the switching power supply circuit: PCB, trace widths, and run SPICE then IPC/ERC checks")
            roles = [t.assignee for t in plan.tasks]
            self.assertIn("ee", roles)
            self.assertIn("compliance", roles)
            # compliance runs after the engineering task and before/with review
            by_role = {t.assignee: t for t in plan.tasks}
            gate = by_role["compliance"]
            ee = by_role["ee"]
            self.assertIn(ee.id, gate.deps)
            # still validates as a DAG
            self.assertTrue(all(d in {t.id for t in plan.tasks} for t in plan.tasks for d in t.deps))

    def test_aerospace_team_plan(self):
        with tempfile.TemporaryDirectory() as d:
            plan = TeamPlanner(d).plan("analyze the rocket nozzle and orbit delta-v with margins")
            roles = [t.assignee for t in plan.tasks]
            self.assertIn("aerospace", roles)
            self.assertIn("compliance", roles)


class EngineeringToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.reg = build_registry(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tools_present(self):
        for name in ("eng_formulas", "eng_calculate", "eng_detect",
                     "eng_gates", "eng_sim_plan", "ios_test_plan"):
            self.assertIn(name, self.reg.names())

    def test_eng_calculate_tool(self):
        r = self.reg.call("eng_calculate", {"name": "dynamic_pressure", "arguments": {"rho": 1.225, "v": 340}})
        self.assertTrue(r.ok, r.error)
        self.assertGreater(r.data["value"], 70000)
        bad = self.reg.call("eng_calculate", {"name": "ghost"})
        self.assertFalse(bad.ok)

    def test_eng_formulas_search(self):
        r = self.reg.call("eng_formulas", {"query": "orbit"})
        self.assertTrue(r.ok)
        self.assertIn("orbital_velocity", r.output)

    def test_eng_gates_list_and_evaluate(self):
        r = self.reg.call("eng_gates", {"action": "list"})
        self.assertTrue(r.ok)
        self.assertIn("do-178c", r.output)
        r2 = self.reg.call("eng_gates", {"action": "evaluate", "gate": "iso-26262"})
        self.assertTrue(r2.ok)
        self.assertIn("verdict", r2.output.lower())


if __name__ == "__main__":
    unittest.main()
