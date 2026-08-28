"""Tests for the Karana prong: edits, repo map, context, verifier."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.coding.edits import EditEngine
from trishula.coding.repomap import RepoMap
from trishula.coding.context import ContextEngine
from trishula.coding.verifier import Verifier, Verdict
from trishula.core.config import TrishulaConfig
from trishula.core.errors import EditError
from trishula.tools.builtin import build_registry
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace


class EditEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.ws.write("app.py", "def add(a, b):\n    return a - b\n\nprint('ok')\n")
        self.engine = EditEngine(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unique_replace_applies(self):
        edit = self.engine.str_replace("app.py", "return a - b", "return a + b")
        self.assertTrue(edit.applied)
        self.assertIn("return a + b", self.ws.read("app.py"))
        self.assertIn("-    return a - b", edit.diff)
        self.assertIn("+    return a + b", edit.diff)

    def test_missing_old_string_raises_with_hint(self):
        with self.assertRaises(EditError) as ctx:
            self.engine.str_replace("app.py", "return a * b", "return a + b")
        self.assertIn("not found", str(ctx.exception))

    def test_duplicate_match_refused_with_line_hints(self):
        self.ws.write("dup.py", "x = 1\nx = 1\n")
        with self.assertRaises(EditError) as ctx:
            self.engine.str_replace("dup.py", "x = 1", "x = 2")
        self.assertIn("2 times", str(ctx.exception))

    def test_insert_at_line(self):
        self.engine.insert_at_line("app.py", 2, "    # sum two numbers")
        self.assertIn("# sum two numbers", self.ws.read("app.py"))

    def test_insert_out_of_range(self):
        with self.assertRaises(EditError):
            self.engine.insert_at_line("app.py", 99, "nope")

    def test_undo_restores_original(self):
        self.engine.str_replace("app.py", "return a - b", "return a + b")
        self.assertEqual(self.engine.changed_files, ["app.py"])
        self.engine.undo("app.py")
        self.assertIn("return a - b", self.ws.read("app.py"))

    def test_edit_tools_via_registry(self):
        reg = build_registry(self.ws)
        r = reg.call("str_replace", {
            "path": "app.py", "old_string": "return a - b", "new_string": "return a + b",
        })
        self.assertTrue(r.ok, r.error)
        self.assertIn("edited app.py", r.output)
        bad = reg.call("str_replace", {
            "path": "app.py", "old_string": "NOPE_NOPE", "new_string": "x",
        })
        self.assertFalse(bad.ok)


class RepoMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.ws.write("mathops/__init__.py", "")
        self.ws.write("mathops/calc.py",
                      "def add(a, b):\n    return a + b\n\n"
                      "class Calculator:\n    def mul(self, a, b):\n        return a * b\n")
        self.ws.write("mathops/helpers.py", "def noop():\n    pass\n")
        self.ws.write("README.md", "# Project\n\n## Install\n\nstuff\n")
        self.rm = RepoMap(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extracts_python_symbols(self):
        syms = self.rm.extract_symbols(self.ws.resolve("mathops/calc.py"))
        names = [s.name for s in syms]
        self.assertIn("add", names)
        self.assertIn("Calculator", names)
        self.assertIn("mul", names)

    def test_markdown_headings(self):
        syms = self.rm.extract_symbols(self.ws.resolve("README.md"))
        self.assertTrue(any("Project" in s.name for s in syms))

    def test_build_scores_and_renders(self):
        maps = self.rm.build()
        self.assertIn("mathops/calc.py", maps)
        text = self.rm.render(max_chars=4000)
        self.assertIn("calc.py", text)
        self.assertIn("class", text)

    def test_top_files_bounded(self):
        top = self.rm.top_files(n=2)
        self.assertLessEqual(len(top), 2)


class ContextEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.ws.write("inventory.py", "WIDGETS = ['a']\n\ndef list_widgets():\n    return WIDGETS\n")
        self.ws.write("orders.py", "# orders module unrelated\nprint('hello world')\n")
        self.ws.write("test_inventory.py", "from inventory import list_widgets\ndef test_it():\n    assert list_widgets()\n")
        self.ce = ContextEngine(self.ws, config=TrishulaConfig(context_token_budget=8000))

    def tearDown(self):
        self.tmp.cleanup()

    def test_keyword_extraction(self):
        kws = self.ce.keywords_for('Rename the "list_widgets" function in inventory.py')
        self.assertIn("inventory.py", kws)
        self.assertTrue(any("list_widgets" in k for k in kws))

    def test_ranks_relevant_file_first(self):
        bundle = self.ce.build_context("fix the list_widgets function in inventory.py")
        paths = [f.path for f in bundle.files]
        self.assertIn("inventory.py", paths)
        self.assertEqual(paths[0], "inventory.py")

    def test_budget_respected(self):
        big = "x = 1\n" * 5000
        self.ws.write("big.py", big)
        bundle = self.ce.build_context("explain x in big.py", budget_tokens=500)
        self.assertLessEqual(bundle.estimated_tokens, 520)


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.cfg = TrishulaConfig(verify_test_timeout=60)
        self.shell = Shell(self.tmp.name, timeout=60, allow_network=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_python_syntax_failure_detected(self):
        self.ws.write("broken.py", "def oops(\n")
        v = Verifier(self.ws, self.shell, config=self.cfg)
        result = v.verify(["broken.py"])
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertFalse(result.syntax_ok)

    def test_stdlib_test_runner_pass(self):
        self.ws.write("mathops.py", "def square(x):\n    return x * x\n")
        self.ws.write("test_mathops.py",
                      "from mathops import square\n"
                      "assert square(3) == 9\n"
                      "print('tests passed')\n")
        v = Verifier(self.ws, self.shell, config=self.cfg)
        result = v.verify(["mathops.py", "test_mathops.py"])
        self.assertEqual(result.verdict, Verdict.PASS, result.summary)
        self.assertGreaterEqual(result.tests_passed, 1)

    def test_stdlib_test_runner_failure(self):
        self.ws.write("mathops.py", "def square(x):\n    return x + x\n")  # wrong
        self.ws.write("test_mathops.py",
                      "from mathops import square\n"
                      "assert square(3) == 9\n")
        v = Verifier(self.ws, self.shell, config=self.cfg)
        result = v.verify(["mathops.py", "test_mathops.py"])
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertGreaterEqual(result.tests_failed, 1)
        self.assertTrue(result.failures)

    def test_no_tests_but_syntax_ok_is_pass(self):
        self.ws.write("clean.py", "x = 1\n")
        v = Verifier(self.ws, self.shell, config=self.cfg)
        result = v.verify(["clean.py"])
        self.assertIn(result.verdict, {Verdict.PASS, Verdict.SKIPPED})


if __name__ == "__main__":
    unittest.main()
