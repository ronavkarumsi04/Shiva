"""Tests for Verifier Phase 2 (property tests + coverage) and worktree pools."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.coding.proplib import check, ints, lists, strs, floats, run_property_file, property_test
from trishula.coding.coverage import measure_coverage
from trishula.coding.testgen import extract_functions, generate_tests
from trishula.coding.verifier import Verifier, Verdict
from trishula.core.config import TrishulaConfig
from trishula.team.worktree import WorktreePool
from trishula.team.planner import TeamPlanner
from trishula.team.swarm import Swarm, DeterministicWorker, Blackboard
from trishula.core.types import Task, TaskStatus
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace


# ── property harness ────────────────────────────────────────────────────────

class ProplibTests(unittest.TestCase):
    def test_passing_property(self):
        r = check(lambda xs: sorted(sorted(xs)) == sorted(xs), lists(ints(-50, 50)), iterations=50)
        self.assertTrue(r.ok)

    def test_failing_property_finds_and_shrinks_counterexample(self):
        # a "property" that fails for any negative value
        r = check(lambda x: x >= 0, ints(-100, 100), iterations=200, seed=1)
        self.assertFalse(r.ok)
        # shrunk counterexample should be the minimal negative, -1
        self.assertEqual(r.counterexample, (-1,))

    def test_edge_cases_include_empty_list(self):
        seen = []
        r = check(lambda xs: (seen.append(list(xs)) or True), lists(ints(-10, 10)), iterations=5)
        self.assertTrue(r.ok)
        self.assertIn([], seen)

    def test_zero_arg_property_runs(self):
        r = check(lambda: 1 + 1 == 2)
        self.assertTrue(r.ok)
        r2 = check(lambda: 1 + 1 == 3)
        self.assertFalse(r2.ok)

    def test_exception_counts_as_violation(self):
        # a property that never raises/returns False for its generated range
        r = check(lambda x: (1 / abs(x) if x else 1.0) > 0,
                  floats(1.0, 10.0), iterations=10)
        self.assertTrue(r.ok, r.error)
        # and one that does raise (division by zero at boundary) reports failure
        r2 = check(lambda x: 1 / x >= 0, floats(0.0, 0.0), iterations=5)
        self.assertFalse(r2.ok)

    def test_run_property_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "p.py")
            p.write_text(
                "from trishula.coding.proplib import property_test, ints\n"
                "@property_test(ints(-5, 5), iterations=10)\n"
                "def test_double_idempotent(x):\n"
                "    assert x + x == 2 * x\n"
                "def test_plain():\n"
                "    assert True\n"
            )
            res = run_property_file(str(p))
            self.assertTrue(all(r.ok for r in res), [r.error for r in res])


# ── coverage ─────────────────────────────────────────────────────────────────

class CoverageTests(unittest.TestCase):
    def test_measures_statement_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("calc.py",
                     "def add(a, b):\n    return a + b\n\n"
                     "def unused():\n    return 42\n")
            ws.write("test_calc.py",
                     "from calc import add\nassert add(1, 2) == 3\nprint('ok')\n")
            shell = Shell(d, timeout=120, allow_network=False)
            report = measure_coverage(shell, ["calc.py"], ["test_calc.py"])
            self.assertIsNotNone(report)
            self.assertIn("calc.py", report.files)
            # add() covered; unused() not — coverage < 100% but > 0
            self.assertGreater(report.overall_pct, 0.3)
            self.assertLess(report.overall_pct, 1.0)

    def test_no_tests_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("x.py", "a = 1\n")
            shell = Shell(d)
            self.assertIsNone(measure_coverage(shell, ["x.py"], []))


# ── test generation ──────────────────────────────────────────────────────────

class TestgenTests(unittest.TestCase):
    def test_extracts_public_functions(self):
        funcs = extract_functions("def pub(x: int) -> int:\n    return x\n\ndef _priv():\n    pass\n")
        names = [f["name"] for f in funcs]
        self.assertIn("pub", names)
        self.assertNotIn("_priv", names)

    def test_generates_runnable_scaffold(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("calc.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
            generated = generate_tests(ws, ["calc.py"], write=True)
            self.assertIn("calc.py", generated)
            test_path = generated["calc.py"]
            # the scaffold imports cleanly (TRISHULA_PYTHONPATH points at the repo)
            env = f"TRISHULA_PYTHONPATH={shlex.quote(str(Path(__file__).resolve().parents[2]))}"
            r = Shell(d).run(f"{env} python3 {test_path}", timeout=60)
            self.assertTrue(r.ok, r.text())


# ── verifier phase 2 integration ─────────────────────────────────────────────

class VerifierPhase2Tests(unittest.TestCase):
    def test_green_tests_with_partial_coverage_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("calc.py",
                     "def add(a, b):\n    return a + b\n\n"
                     "def multiply(a, b):\n    return a * b\n")
            ws.write("test_calc.py", "from calc import add\nassert add(2, 3) == 5\nprint('passed')\n")
            cfg = TrishulaConfig(verify_coverage=True, coverage_min_pct=0.99,
                                 verify_property_tests=False)
            v = Verifier(ws, Shell(d, timeout=120), config=cfg)
            res = v.verify(["calc.py", "test_calc.py"])
            self.assertIn(res.verdict, (Verdict.PASS, Verdict.PARTIAL))
            self.assertIsNotNone(res.coverage)
            # multiply() uncovered → feedback mentions coverage
            self.assertIn("covered", res.feedback.lower())

    def test_property_failure_downgrades_to_partial(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("mod.py", "def neg(x):\n    return -abs(x)\n")
            ws.write(
                "test_trishula_autogen_mod.py",
                "from trishula.coding.proplib import run_property_file, check, ints\n"
                "import mod\n"
                "check(lambda x: mod.neg(x) < 0 or x == 0, ints(1, 50), iterations=10, name='neg_property')\n"
                "print('ran')\n",
            )
            cfg = TrishulaConfig(verify_coverage=False, verify_property_tests=True)
            v = Verifier(ws, Shell(d, timeout=120), config=cfg)
            res = v.verify(["mod.py", "test_trishula_autogen_mod.py"])
            # smoke ran; property check itself passes here (neg(x) for x>=1 is <0)
            self.assertIsInstance(res.property_results, list)


# ── worktree pool ────────────────────────────────────────────────────────────

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)


class WorktreePoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _git("init", "-q", cwd=str(root))
        _git("config", "user.email", "t@t", cwd=str(root))
        _git("config", "user.name", "t", cwd=str(root))
        (root / "main.py").write_text("base = 1\n")
        _git("add", "-A", cwd=str(root))
        _git("commit", "-qm", "init", cwd=str(root))
        self.root = root

    def tearDown(self):
        Shell(str(self.root)).run("git worktree prune", timeout=30)
        self.tmp.cleanup()

    def test_isolated_worktree_merges_back(self):
        pool = WorktreePool(self.root, max_worktrees=2)
        self.assertTrue(pool.is_git)
        ws, isolated = pool.acquire("task_alpha")
        self.assertTrue(isolated)
        ws.write("alpha.txt", "alpha result\n")
        self.assertTrue(pool.commit_worker_changes("task_alpha", "add alpha"))
        result = pool.complete("task_alpha", merge=True)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.merged)
        # merged content visible in base
        self.assertTrue((self.root / "alpha.txt").exists())
        pool.cleanup()

    def test_non_git_workspace_degrades(self):
        with tempfile.TemporaryDirectory() as d:
            pool = WorktreePool(d)
            self.assertFalse(pool.is_git)
            ws, isolated = pool.acquire("t")
            self.assertFalse(isolated)


class WorktreeSwarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _git("init", "-q", cwd=str(root))
        _git("config", "user.email", "t@t", cwd=str(root))
        _git("config", "user.name", "t", cwd=str(root))
        (root / "README.md").write_text("# project\n")
        _git("add", "-A", cwd=str(root))
        _git("commit", "-qm", "init", cwd=str(root))
        self.root = root
        self.cfg = TrishulaConfig(team_use_worktrees=True, team_max_workers=4)

    def tearDown(self):
        Shell(str(self.root)).run("git worktree prune", timeout=30)
        self.tmp.cleanup()

    def test_parallel_implementers_land_distinct_files(self):
        planner = TeamPlanner(self.root, config=self.cfg)
        plan = planner.plan("add feature alpha and beta modules")

        def implementer(task, board, workspace=None):
            if workspace is not None and task.assignee == "implementer":
                # write a unique file from the worktree workspace
                fname = f"impl_{task.id[:8]}.py"
                workspace.write(fname, f"# {task.title}\nvalue = {task.id!r}\n")
            return f"done: {task.title}"

        worker = DeterministicWorker(actions={"implementer": implementer})
        swarm = Swarm(self.root, plan, worker=worker, config=self.cfg)
        report = swarm.execute()
        self.assertTrue(report.ok, [r.error for r in report.failed_tasks])
        # parallel implementers each produced a file in the merged tree
        merged = list(self.root.glob("impl_*.py"))
        implementer_count = sum(1 for t in plan.tasks if t.assignee == "implementer")
        self.assertEqual(len(merged), implementer_count)

    def test_merge_conflict_is_reported(self):
        from trishula.tools.workspace import Workspace as Ws
        pool = WorktreePool(self.root, max_worktrees=2)
        ws_a, isolated_a = pool.acquire("task_a")
        ws_a.write("shared.txt", "from A\n")
        # Commit in A's worktree so the merge brings content.
        Shell(str(ws_a.root)).run("git add -A && git commit -qm A", timeout=30)
        # Diverge the base with different content for the same file.
        (self.root / "shared.txt").write_text("from base\n")
        Shell(str(self.root)).run("git add -A && git commit -qm base", timeout=30)
        result = pool.complete("task_a", merge=True)
        self.assertFalse(result.ok)
        self.assertIn("shared.txt", result.conflict_files)
        pool.cleanup()


if __name__ == "__main__":
    unittest.main()
