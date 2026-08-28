"""Tests for the Ayudha prong: workspace confinement + shell guardrails."""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.core.config import TrishulaConfig
from trishula.core.errors import SandboxError
from trishula.tools.workspace import Workspace
from trishula.tools.shell import Shell
from trishula.tools.registry import ToolRegistry
from trishula.core.types import ToolResult


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_read_roundtrip(self):
        self.ws.write("a/b/c.txt", "hello\nworld\n")
        self.assertEqual(self.ws.read("a/b/c.txt"), "hello\nworld\n")

    def test_read_lines_are_numbered(self):
        self.ws.write("f.py", "one\ntwo\nthree\n")
        out = self.ws.read_lines("f.py", 2, 3)
        self.assertIn("2", out)
        self.assertIn("two", out)
        self.assertIn("three", out)
        self.assertNotIn("one", out)

    def test_path_escape_blocked(self):
        with self.assertRaises(SandboxError):
            self.ws.read("../../../etc/passwd")
        with self.assertRaises(SandboxError):
            self.ws.write("/tmp/outside_trishula.txt", "x")

    def test_readonly_workspace_refuses_writes(self):
        ro = Workspace(self.tmp.name, readonly=True)
        with self.assertRaises(SandboxError):
            ro.write("x.txt", "no")

    def test_walk_skips_noise_dirs(self):
        self.ws.write("src/app.py", "x = 1\n")
        self.ws.write("node_modules/evil/index.js", "bad")
        self.ws.write(".git/config", "secret")
        files = [self.ws.rel(f) for f in self.ws.walk_files()]
        self.assertIn("src/app.py", files)
        self.assertFalse(any("node_modules" in f for f in files))
        self.assertFalse(any(".git" in f for f in files))

    def test_change_tracking(self):
        self.ws.write("a.txt", "1")
        self.ws.write("b.txt", "2")
        self.ws.write("a.txt", "3")
        self.assertEqual(sorted(self.ws.changed_files), ["a.txt", "b.txt"])


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.cfg = TrishulaConfig()
        self.shell = Shell(
            self.tmp.name,
            deny_commands=self.cfg.shell_deny_commands,
            allow_network=False,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_simple_command_ok(self):
        r = self.shell.run("echo hello")
        self.assertTrue(r.ok)
        self.assertIn("hello", r.stdout)

    def test_nonzero_exit_is_result_not_raise(self):
        r = self.shell.run("exit 3")
        self.assertFalse(r.ok)
        self.assertEqual(r.exit_code, 3)

    def test_denylist_blocks_destructive_commands(self):
        r = self.shell.run("rm -rf /")
        self.assertTrue(r.denied)
        self.assertFalse(r.ok)

    def test_timeout_kills_process(self):
        r = self.shell.run("sleep 5", timeout=1)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)

    def test_cwd_confined_to_workspace(self):
        r = self.shell.run("pwd")
        self.assertTrue(r.ok)
        self.assertEqual(Path(r.stdout.strip()).resolve(), Path(self.tmp.name).resolve())

    def test_secrets_not_leaked_to_child(self):
        os.environ["TRISHULA_TEST_SECRET_TOKEN"] = "supersecret"
        try:
            r = self.shell.run("env")
            self.assertNotIn("supersecret", r.stdout + r.stderr)
        finally:
            del os.environ["TRISHULA_TEST_SECRET_TOKEN"]

    def test_network_denied_by_default(self):
        # Proxy is pointed at a black hole; we can only assert env config here
        # (a real connection attempt is platform dependent).
        r = self.shell.run("echo $https_proxy")
        self.assertIn("127.0.0.1:9", r.stdout)

    def test_output_cap_truncation(self):
        small = Shell(self.tmp.name, output_cap=100)
        r = small.run("seq 1 1000")
        self.assertIn("truncated", r.stdout)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()

    def test_register_and_call_normalizes(self):
        self.reg.register(
            "greet", "say hi",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=lambda name: f"hi {name}",
        )
        result = self.reg.call("greet", {"name": "shiva"})
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "hi shiva")

    def test_missing_required_argument(self):
        self.reg.register(
            "need", "needs x",
            {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            handler=lambda x: ToolResult(True, output=str(x)),
        )
        result = self.reg.call("need", {})
        self.assertFalse(result.ok)
        self.assertIn("missing required", result.error)

    def test_type_coercion(self):
        captured = {}

        def h(n: int = 0, flag: bool = False):
            captured["n"] = n
            captured["flag"] = flag
            return "ok"

        self.reg.register(
            "h", "h",
            {"type": "object",
             "properties": {"n": {"type": "integer"}, "flag": {"type": "boolean"}}},
            handler=h,
        )
        self.reg.call("h", {"n": "42", "flag": "true"})
        self.assertEqual(captured["n"], 42)
        self.assertIs(captured["flag"], True)

    def test_unknown_argument_rejected(self):
        self.reg.register("strict", "s", {"type": "object", "properties": {}}, handler=lambda: "x")
        r = self.reg.call("strict", {"bogus": 1})
        self.assertFalse(r.ok)
        self.assertIn("unexpected argument", r.error)

    def test_unknown_tool(self):
        r = self.reg.call("ghost", {})
        self.assertFalse(r.ok)
        self.assertIn("unknown tool", r.error)

    def test_schema_shape(self):
        self.reg.register(
            "t", "d",
            {"type": "object", "properties": {"a": {"type": "string"}}},
            handler=lambda **kw: "x",
        )
        schema = self.reg.schemas()[0]
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "t")


if __name__ == "__main__":
    unittest.main()
