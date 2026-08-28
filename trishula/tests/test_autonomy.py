"""Tests for the Chit-Shodhana prong: reflection, skills, BM25, learning."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.autonomy.reflect import Reflector
from trishula.autonomy.skills import SkillLibrary, tokenize
from trishula.core.config import TrishulaConfig
from trishula.core.types import EventKind, Journal


def _cfg(tmpdir: str) -> TrishulaConfig:
    return TrishulaConfig(home=tmpdir)


class JournalTests(unittest.TestCase):
    def test_events_and_subscribers(self):
        j = Journal()
        seen = []
        j.subscribe(lambda e: seen.append(e))
        j.emit(EventKind.TOOL_CALL, tool="x")
        j.emit(EventKind.TOOL_RESULT, tool="x", ok=True)
        self.assertEqual(len(j), 2)
        self.assertEqual(len(j.events(EventKind.TOOL_CALL)), 1)
        self.assertEqual(len(seen), 2)


class ReflectorTests(unittest.TestCase):
    def test_successful_run_scores_high_and_proposes_skill(self):
        j = Journal()
        for tool, ok in [
            ("search_code", True), ("read_file", True),
            ("str_replace", True), ("run_shell", True),
        ]:
            j.emit(EventKind.TOOL_CALL, tool=tool, args={"command": "pytest"} if tool == "run_shell" else {})
            j.emit(EventKind.TOOL_RESULT, tool=tool, ok=ok, data={})
        j.emit(EventKind.VERDICT, verdict="pass", passed=10, failed=0, failures=[])
        retro = Reflector().reflect("add a retry helper to the api client", j,
                                    report={"ok": True, "verdict": "pass", "steps": 4})
        self.assertTrue(retro.success)
        self.assertGreaterEqual(retro.score, 0.7)
        self.assertEqual(retro.signals["tool_calls"], 4)
        self.assertTrue(retro.proposed_skills)
        proposal = retro.proposed_skills[0]
        self.assertTrue(proposal["steps"])
        self.assertIn("api", proposal["tags"])

    def test_failed_run_scores_low_and_flags_thrashing(self):
        j = Journal()
        for _ in range(4):  # identical repeated call = thrash
            j.emit(EventKind.TOOL_CALL, tool="run_shell", args={"command": "pytest"})
            j.emit(EventKind.TOOL_RESULT, tool="run_shell", ok=False, data={"exit_code": 1})
        j.emit(EventKind.EDIT_FAILED, path="x.py", error="not found")
        j.emit(EventKind.VERDICT, verdict="fail", passed=0, failed=2, failures=["test_a"])
        retro = Reflector().reflect("fix the parser bug", j,
                                    report={"ok": False, "verdict": "fail", "steps": 4})
        self.assertFalse(retro.success)
        self.assertLess(retro.score, 0.5)
        self.assertGreaterEqual(retro.signals["thrashed_calls"], 3)
        self.assertGreaterEqual(retro.signals["edit_misses"], 1)
        self.assertTrue(retro.anti_patterns)
        self.assertFalse(retro.proposed_skills)  # failures don't distill skills

    def test_denied_command_detected(self):
        j = Journal()
        j.emit(EventKind.TOOL_CALL, tool="shell", command="curl http://evil")
        j.emit(EventKind.TOOL_RESULT, tool="shell", ok=False, data={"denied": True})
        retro = Reflector().reflect("do a thing", j, report={"ok": False, "verdict": "fail", "steps": 1})
        self.assertGreaterEqual(retro.signals["denied_commands"], 1)
        self.assertTrue(any("sandbox" in a for a in retro.anti_patterns))


class SkillLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = SkillLibrary(_cfg(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_promote_and_retrieve_bm25(self):
        self.lib.promote({
            "name": "autonomous-add-retry",
            "description": "Add exponential backoff retry to network clients",
            "when_to_use": "When adding retries or backoff to an API client or network call",
            "steps": ["Locate the HTTP call", "Wrap with tenacity-style retry", "Test failures"],
            "tags": ["api"],
            "tools": ["search_code", "str_replace", "run_shell"],
        })
        self.lib.promote({
            "name": "autonomous-fix-css",
            "description": "Center a flexbox layout",
            "when_to_use": "When fixing UI centering and layout issues",
            "steps": ["Find container", "Apply display:flex"],
            "tags": ["frontend"],
            "tools": ["str_replace"],
        })
        hits = self.lib.search("add retry and backoff to the API network client")
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].name, "autonomous-add-retry")

    def test_promote_is_idempotent_and_merges(self):
        for _ in range(3):
            self.lib.promote({
                "name": "autonomous-merge",
                "description": "d",
                "when_to_use": "w",
                "steps": ["only step"],
            })
        skills = self.lib.all()
        self.assertEqual(len([s for s in skills if s.name == "autonomous-merge"]), 1)

    def test_quality_ema_updates_on_record_use(self):
        s = self.lib.promote({
            "name": "autonomous-q", "description": "d", "when_to_use": "w",
            "steps": ["a"],
        })
        self.lib.record_use(s.id, True)
        self.lib.record_use(s.id, True)
        s2 = self.lib.get(s.id)
        self.assertGreater(s2.quality, 0.7)
        self.assertEqual(s2.uses, 2)
        self.lib.record_use(s.id, False)
        s3 = self.lib.get(s.id)
        self.assertLess(s3.quality, s2.quality)

    def test_refine_appends_steps_and_antipatterns_bumps_version(self):
        s = self.lib.promote({
            "name": "autonomous-refine", "description": "d", "when_to_use": "w",
            "steps": ["do thing"],
        })
        self.lib.refine(s.id, failure_detail="broke tests",
                        new_steps=["Run tests AFTER editing"],
                        anti_patterns=["never skip verification"])
        s2 = self.lib.get(s.id)
        self.assertEqual(s2.version, 2)
        self.assertIn("Run tests AFTER editing", s2.steps)
        self.assertTrue(any("verification" in a for a in s2.anti_patterns))

    def test_tokenize(self):
        self.assertEqual(tokenize("Hello, World_1!"), ["hello", "world_1"])

    def test_skills_for_prompt_renders(self):
        self.lib.promote({
            "name": "autonomous-p", "description": "fix flaky tests",
            "when_to_use": "when tests flake", "steps": ["quarantine", "reproduce", "fix"],
        })
        text = self.lib.skills_for_prompt("flaky test")
        self.assertIn("autonomous-p", text)
        self.assertIn("1. quarantine", text)


if __name__ == "__main__":
    unittest.main()
