"""Tests for the merge arbiter and the bounded verify→repair loop."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.core.config import TrishulaConfig
from trishula.core.types import Message
from trishula.coding.loop import CodingLoop
from trishula.llm.base import LLMClient, LLMResponse
from trishula.team.arbiter import (
    MergeArbiter, parse_conflicts, apply_region_resolution, _resolve_region_deterministic,
    Region,
)
from trishula.team.worktree import WorktreePool
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)


# ── arbiter unit tests ──────────────────────────────────────────────────────

class ConflictParsingTests(unittest.TestCase):
    def test_parses_regions(self):
        text = (
            "common\n"
            "<<<<<<< HEAD\nours line\n=======\ntheirs line\n>>>>>>> branch\n"
            "tail\n"
        )
        regions, has = parse_conflicts(text)
        self.assertTrue(has)
        self.assertEqual(len(regions), 1)
        self.assertIn("ours line", regions[0].ours)
        self.assertIn("theirs line", regions[0].theirs)

    def test_no_conflict(self):
        regions, has = parse_conflicts("clean file\n")
        self.assertFalse(has)
        self.assertEqual(regions, [])


class DeterministicResolutionTests(unittest.TestCase):
    def test_identical_sides_keep_one(self):
        r = Region(ours="import os\n", theirs="import os\n")
        self.assertIn("import os", _resolve_region_deterministic(r))

    def test_one_side_empty(self):
        r = Region(ours="", theirs="def new():\n    return 1\n")
        out = _resolve_region_deterministic(r)
        self.assertIn("def new", out)

    def test_disjoint_imports_union(self):
        r = Region(ours="import os\n", theirs="import sys\n")
        out = _resolve_region_deterministic(r, "mod.py")
        self.assertIn("import os", out)
        self.assertIn("import sys", out)

    def test_import_rule_does_not_fire_on_non_python(self):
        # 'from A' in prose must not be read as a Python import.
        r = Region(ours="from A\n", theirs="from B\n")
        self.assertIsNone(_resolve_region_deterministic(r, "notes.txt"))
        # and malformed python lines are not treated as imports
        r2 = Region(ours="from os import\n", theirs="x = 1\n")
        self.assertIsNone(_resolve_region_deterministic(r2, "mod.py"))

    def test_overlapping_logic_not_auto_resolved(self):
        r = Region(ours="x = 1\n", theirs="x = 2\n")
        self.assertIsNone(_resolve_region_deterministic(r, "mod.py"))

    def test_apply_rebuilds_file(self):
        text = (
            "a\n<<<<<<< H\nx = 1\n=======\nx = 2\n>>>>>>> B\nb\n"
        )
        regions, _ = parse_conflicts(text)
        rebuilt = apply_region_resolution(text, ["x = 99\n"])
        self.assertNotIn("<<<<<<<", rebuilt)
        self.assertIn("x = 99", rebuilt)
        self.assertIn("a\n", rebuilt)
        self.assertIn("b\n", rebuilt)


# ── end-to-end conflict resolution in a real git repo ───────────────────────

class ArbiterGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _git("init", "-q", cwd=str(root))
        _git("config", "user.email", "t@t", cwd=str(root))
        _git("config", "user.name", "t", cwd=str(root))
        (root / "mod.py").write_text("import os\n\nx = 1\n")
        _git("add", "-A", cwd=str(root)); _git("commit", "-qm", "base", cwd=str(root))
        self.root = root

    def tearDown(self):
        Shell(str(self.root)).run("git worktree prune", timeout=30)
        self.tmp.cleanup()

    def test_deterministic_import_conflict_auto_merges(self):
        pool = WorktreePool(self.root, max_worktrees=2)
        ws, isolated = pool.acquire("task_imports")
        self.assertTrue(isolated)
        # worker adds an import on its branch
        f = ws.resolve("mod.py")
        text = f.read_text()
        f.write_text(text.replace("import os\n", "import os\nimport sys\n"))
        pool.commit_worker_changes("task_imports", "add sys import")
        # base diverges by adding a *different* import → add/add import conflict
        (self.root / "mod.py").write_text("import os\nimport json\n\nx = 1\n")
        Shell(str(self.root)).run("git add -A && git commit -qm 'add json import'", timeout=30)

        result = pool.complete("task_imports", merge=True)
        # deterministic arbiter should union the imports and merge cleanly
        self.assertTrue(result.ok, result.error)
        merged = (self.root / "mod.py").read_text()
        self.assertIn("import sys", merged)
        self.assertIn("import json", merged)
        pool.cleanup()

    def test_true_logic_conflict_stays_unresolved_without_llm(self):
        pool = WorktreePool(self.root, max_worktrees=2)
        ws, _ = pool.acquire("task_logic")
        f = ws.resolve("mod.py")
        f.write_text("import os\n\nx = 100\n")
        pool.commit_worker_changes("task_logic", "set x=100")
        (self.root / "mod.py").write_text("import os\n\nx = 200\n")
        Shell(str(self.root)).run("git add -A && git commit -qm 'set x=200'", timeout=30)

        result = pool.complete("task_logic", merge=True)
        self.assertFalse(result.ok)
        self.assertTrue(result.conflict_files)
        # tree was restored cleanly (no markers left in base)
        self.assertNotIn("<<<<<<<", (self.root / "mod.py").read_text())
        pool.cleanup()

    def test_llm_arbiter_resolves_logic_conflict(self):
        class FakeLLM:
            name = "fake-llm"
            def complete(self, messages, **kw):
                return LLMResponse(
                    content="import os\n\nx = 300  # reconciled\n", model="fake"
                )
        pool = WorktreePool(self.root, max_worktrees=2, client=FakeLLM())
        ws, _ = pool.acquire("task_llm")
        f = ws.resolve("mod.py")
        f.write_text("import os\n\nx = 100\n")
        pool.commit_worker_changes("task_llm", "set x=100")
        (self.root / "mod.py").write_text("import os\n\nx = 200\n")
        Shell(str(self.root)).run("git add -A && git commit -qm 'set x=200'", timeout=30)

        result = pool.complete("task_llm", merge=True)
        self.assertTrue(result.ok, result.error)
        self.assertIn("300", (self.root / "mod.py").read_text())
        pool.cleanup()


# ── verify → repair loop ────────────────────────────────────────────────────

class RepairClient(LLMClient):
    """Plays a script across ALL turns (initial + repair rounds)."""
    name = "repair-script"

    def __init__(self, script):
        self.script = list(script)
        self.n = 0

    def complete(self, messages, *, tools=None, **kw):
        if self.script:
            name, args = self.script.pop(0)
            self.n += 1
            return LLMResponse(
                content="", tool_calls=[{"id": f"c{self.n}", "name": name, "arguments": args}],
                model=self.name,
            )
        return LLMResponse(content="done", model=self.name)


class RepairLoopTests(unittest.TestCase):
    def test_failing_then_fixing_closes_in_repair_round(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            # Buggy function + failing test
            ws.write("calc.py", "def double(x):\n    return x + 1\n")
            ws.write("test_calc.py",
                     "from calc import double\n"
                     "assert double(3) == 6, f'double broken {double(3)}'\n"
                     "assert double(5) == 10, f'double broken {double(5)}'\n"
                     "print('ok')\n")
            # Initial round applies a WRONG fix (x+3): double(3)==6 passes but
            # double(5)==8 fails; repair applies the RIGHT fix (x*2).
            client = RepairClient([
                ("str_replace", {"path": "calc.py",
                                 "old_string": "def double(x):\n    return x + 1",
                                 "new_string": "def double(x):\n    return x + 3"}),
                ("finish", {"summary": "attempt 1"}),
                # repair round:
                ("str_replace", {"path": "calc.py",
                                 "old_string": "def double(x):\n    return x + 3",
                                 "new_string": "def double(x):\n    return x * 2"}),
                ("finish", {"summary": "fixed correctly"}),
            ])
            cfg = TrishulaConfig(
                home=str(Path(d) / ".home"),
                coding_max_steps=20, coding_repair_rounds=2,
                verify_coverage=False, verify_property_tests=False,
            )
            loop = CodingLoop(d, client=client, config=cfg)
            report = loop.run("fix double() in calc.py")
            self.assertTrue(report.ok, report.summary)
            self.assertEqual(report.verification.verdict.value, "pass")
            self.assertGreaterEqual(report.repair_rounds, 1)
            self.assertIn("return x * 2", Path(d, "calc.py").read_text())

    def test_persistent_failure_exhausts_repair_budget(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write("calc.py", "def double(x):\n    return x + 1\n")
            ws.write("test_calc.py",
                     "from calc import double\nassert double(3) == 6\nassert double(5) == 10\nprint('ok')\n")
            client = RepairClient([
                # initial: x+3 makes double(3)==6 pass the first assert but
                # double(5)==8 fails the second → FAIL.
                ("str_replace", {"path": "calc.py",
                                 "old_string": "return x + 1",
                                 "new_string": "return x + 3"}),
                ("finish", {"summary": "try"}),
                # repair: still wrong (x+5: double(3)==8 fails) → stays FAIL.
                ("str_replace", {"path": "calc.py",
                                 "old_string": "return x + 3",
                                 "new_string": "return x + 5"}),
                ("finish", {"summary": "try2"}),
            ])
            cfg = TrishulaConfig(
                home=str(Path(d) / ".home"), coding_max_steps=20,
                coding_repair_rounds=1, verify_coverage=False, verify_property_tests=False,
            )
            loop = CodingLoop(d, client=client, config=cfg)
            report = loop.run("fix double()")
            self.assertFalse(report.ok)
            self.assertEqual(report.verification.verdict.value, "fail")


if __name__ == "__main__":
    unittest.main()
