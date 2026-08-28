"""End-to-end: the full autonomy loop fixes a real bug and learns from it.

These tests exercise all four prongs together against a throwaway project:

* a buggy module + a failing stdlib test,
* a scripted model that applies the real fix via the tool registry,
* mandatory verification (the test goes red -> green),
* reflection that scores the run and distills a skill,
* and a second run that retrieves and uses the distilled skill.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.autonomy.loop import AutonomyLoop
from trishula.coding.loop import CodingLoop
from trishula.core.config import TrishulaConfig
from trishula.core.types import Message
from trishula.llm.base import LLMClient, LLMResponse
from trishula.llm.stub import StubClient
from trishula.team.planner import TeamPlanner
from trishula.team.swarm import Swarm, DeterministicWorker


BUGGY = "def square(x):\n    return x + x\n\ndef cube(x):\n    return x ** 2\n"
TEST = (
    "from calc import square, cube\n"
    "assert square(3) == 9, f'square broken: {square(3)}'\n"
    "assert cube(2) == 8, f'cube broken: {cube(2)}'\n"
    "print('all calc tests passed')\n"
)


class ScriptedClient(LLMClient):
    """Plays back a fixed tool-call script; then tells the loop to finish."""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls_seen = 0

    def complete(self, messages, *, tools=None, **kw):
        if self.script:
            step = self.script.pop(0)
            self.calls_seen += 1
            return LLMResponse(
                content="",
                tool_calls=[{"id": f"call_{self.calls_seen}", "name": step[0], "arguments": step[1]}],
                model=self.name,
            )
        return LLMResponse(content="Done and verified.", model=self.name)


class CodingLoopE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "calc.py").write_text(BUGGY)
        (root / "test_calc.py").write_text(TEST)
        self.cfg = TrishulaConfig(home=self.tmp.name + "/state", coding_max_steps=20)

    def tearDown(self):
        self.tmp.cleanup()

    def test_red_green_fix_verified_and_learned(self):
        # Baseline: the test fails.
        import subprocess

        bad = subprocess.run(
            [sys.executable, "test_calc.py"], cwd=self.tmp.name,
            capture_output=True, text=True,
        )
        self.assertNotEqual(bad.returncode, 0)

        client = ScriptedClient([
            ("read_file", {"path": "calc.py", "start_line": 1, "end_line": 10}),
            ("str_replace", {
                "path": "calc.py",
                "old_string": "def square(x):\n    return x + x",
                "new_string": "def square(x):\n    return x * x",
            }),
            ("str_replace", {
                "path": "calc.py",
                "old_string": "def cube(x):\n    return x ** 2",
                "new_string": "def cube(x):\n    return x ** 3",
            }),
        ])

        loop = AutonomyLoop(self.tmp.name, client=client, config=self.cfg)
        run = loop.coding_task("Fix the square and cube math bugs in calc.py")

        self.assertTrue(run.report["ok"], run.report["summary"])
        self.assertEqual(run.report["verdict"], "pass")
        self.assertIn("calc.py", run.report["changed_files"])
        # The fix landed on disk.
        self.assertIn("return x * x", Path(self.tmp.name, "calc.py").read_text())
        self.assertIn("return x ** 3", Path(self.tmp.name, "calc.py").read_text())

        # Reflection: high score, a skill was distilled from the winning tactic.
        retro = run.retrospective
        self.assertTrue(retro["success"])
        self.assertGreaterEqual(retro["score"], 0.6)
        self.assertTrue(run.skills_created, "a skill should have been distilled")

        # The run was persisted.
        history = loop.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "success")

    def test_skill_is_retrievable_on_second_run(self):
        client1 = ScriptedClient([
            ("str_replace", {
                "path": "calc.py",
                "old_string": "def square(x):\n    return x + x",
                "new_string": "def square(x):\n    return x * x",
            }),
            ("str_replace", {
                "path": "calc.py",
                "old_string": "def cube(x):\n    return x ** 2",
                "new_string": "def cube(x):\n    return x ** 3",
            }),
        ])
        loop = AutonomyLoop(self.tmp.name, client=client1, config=self.cfg)
        run1 = loop.coding_task("Fix the math bugs in calc.py")
        self.assertTrue(run1.report["ok"])

        hits = loop.library.search("fix failing math function bug")
        self.assertTrue(hits, "the distilled skill should be retrievable")
        top = hits[0][0]
        self.assertGreater(top.quality, 0.5)

    def test_stub_client_runs_end_to_end_offline(self):
        # No model at all: the deterministic engine must still execute the
        # tool chain without crashing and produce a structured report.
        loop = CodingLoop(self.tmp.name, client=StubClient(), config=self.cfg)
        report = loop.run("investigate the calc module", max_steps=8)
        self.assertGreaterEqual(report.steps, 1)
        self.assertTrue(report.tool_calls)

    def test_team_plan_and_deterministic_swarm_on_project(self):
        planner = TeamPlanner(self.tmp.name, config=self.cfg)
        plan = planner.plan("fix the bug and add tests with full team coordination")
        self.assertGreaterEqual(len(plan.tasks), 5)
        swarm = Swarm(self.tmp.name, plan, worker=DeterministicWorker(), config=self.cfg)
        report = swarm.execute()
        self.assertTrue(report.ok)
        roles_done = {r.assignee for r in report.results}
        self.assertIn("scout", roles_done)
        self.assertIn("qa", roles_done)
        self.assertIn("reviewer", roles_done)


class SandboxSecurityE2ETests(unittest.TestCase):
    def test_str_replace_cannot_touch_outside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = CodingLoop(tmp, client=StubClient(), config=TrishulaConfig(home=tmp + "/s"))
            result = loop.registry.call("str_replace", {
                "path": "../../etc/hostname",
                "old_string": "x",
                "new_string": "y",
            })
            self.assertFalse(result.ok)
            self.assertIn("outside", result.error.lower() + result.output.lower())

    def test_shell_refuses_to_run_destructive_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = CodingLoop(tmp, client=StubClient(), config=TrishulaConfig(home=tmp + "/s"))
            result = loop.registry.call("run_shell", {"command": "rm -rf /"})
            self.assertFalse(result.ok)
            self.assertTrue(result.data.get("denied"))


if __name__ == "__main__":
    unittest.main()
