"""Tests for the Shiva↔Trishula bridge surfaces.

These cover the two integration points without importing Shiva's heavy
runtime (which needs third-party deps):

* the CLI subcommand parser forwards args to ``trishula.cli.main``;
* the agent tool handlers (``tools/trishula_tools.py``) produce correct
  structured results against a stubbed ``tools.registry``.
"""

import argparse
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class CliBridgeTests(unittest.TestCase):
    def test_parser_builds_and_holds_handler(self):
        from shiva_cli.subcommands.trishula import (
            build_trishula_parser,
            cmd_trishula,
        )

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        build_trishula_parser(sub, cmd_trishula=cmd_trishula)
        args = parser.parse_args(["trishula", "selftest"])
        self.assertTrue(callable(args.func))
        self.assertIn("selftest", args.tri_args)

    def test_no_args_becomes_help(self):
        from shiva_cli.subcommands.trishula import cmd_trishula

        captured = {}

        class FakeArgs:
            tri_args = []

        # Patch trishula.cli.main to capture the forwarded argv.
        import trishula.cli as tri_cli

        orig = tri_cli.main
        tri_cli.main = lambda argv: captured.setdefault("argv", list(argv)) or 0
        try:
            rc = cmd_trishula(FakeArgs())
        finally:
            tri_cli.main = orig
        self.assertEqual(captured["argv"], ["--help"])

    def test_leading_double_dash_stripped(self):
        from shiva_cli.subcommands.trishula import cmd_trishula
        import trishula.cli as tri_cli

        captured = {}
        orig = tri_cli.main
        tri_cli.main = lambda argv: captured.setdefault("argv", list(argv)) or 0
        try:

            class FakeArgs:
                tri_args = ["--", "skills", "list"]

            cmd_trishula(FakeArgs())
        finally:
            tri_cli.main = orig
        self.assertEqual(captured["argv"], ["skills", "list"])


def _install_registry_stub():
    """Install a fake tools.registry capturing registrations (no Shiva deps)."""
    stub = types.ModuleType("tools.registry")
    registered = []

    class _Reg:
        def register(self, **kw):
            registered.append(kw)

    stub.registry = _Reg()
    stub.tool_error = lambda m, **e: json.dumps({"error": m, **e})
    stub.tool_result = lambda d=None, **k: json.dumps({"ok": True, **(d or {}), **k})
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules.setdefault("tools", pkg)
    sys.modules["tools.registry"] = stub
    return registered


class ToolBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered = _install_registry_stub()
        sys.modules.pop("tools.trishula_tools", None)
        import tools.trishula_tools as tt  # noqa: E402

        cls.tt = tt

    def test_all_four_tools_registered(self):
        names = {r["name"] for r in self.registered}
        self.assertEqual(
            names, {"trishula_code", "trishula_team", "trishula_skills", "trishula_runs"}
        )

    def test_check_fn_reports_available(self):
        for r in self.registered:
            self.assertTrue(r["check_fn"](), f"{r['name']} should be available")

    def test_team_plan_tool_offline(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "package.json").write_text("{}")
            out = json.loads(self.tt.trishula_team_tool(
                "add an orders API endpoint and fix the bug", path=d, plan_only=True
            ))
            self.assertTrue(out["ok"])
            self.assertFalse(out["executed"])
            self.assertGreaterEqual(out["task_count"], 5)
            roles = [t["role"] for t in out["tasks"]]
            self.assertEqual(roles[0], "scout")
            self.assertIn("qa", roles)
            self.assertIn("reviewer", roles)
            # deps reference real titles
            titles = {t["title"] for t in out["tasks"]}
            for t in out["tasks"]:
                for dep in t["deps"]:
                    self.assertIn(dep, titles)

    def test_team_execute_runs_swarm(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "pyproject.toml").write_text("[project]\nname='x'\n")
            out = json.loads(self.tt.trishula_team_tool(
                "add feature X with tests", path=d, plan_only=False, execute=True
            ))
            self.assertTrue(out["ok"])
            self.assertTrue(out["executed"])
            self.assertTrue(out["swarm_ok"])
            self.assertTrue(all(r["status"] == "done" for r in out["results"]))

    def test_skills_list_and_search(self):
        out = json.loads(self.tt.trishula_skills_tool("list"))
        self.assertTrue(out["ok"])
        self.assertIn("count", out)
        err = json.loads(self.tt.trishula_skills_tool("search", ""))
        self.assertIn("error", err)

    def test_code_tool_validates_goal(self):
        err = json.loads(self.tt.trishula_code_tool(""))
        self.assertIn("error", err)

    def test_code_tool_returns_structured_report(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["TRISHULA_HOME"] = str(Path(d) / ".home")
            Path(d, "mod.py").write_text("x = 1\n")
            out = json.loads(self.tt.trishula_code_tool(
                "inspect the mod module", path=d, max_steps=3
            ))
            self.assertIn("ok", out)
            self.assertIn("verdict", out)
            self.assertIn("retrospective_score", out)


if __name__ == "__main__":
    unittest.main()
