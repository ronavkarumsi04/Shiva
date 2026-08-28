"""Tests for the Devas prong: roles, planner DAG, and swarm execution."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trishula.core.config import TrishulaConfig
from trishula.core.errors import PlanningError
from trishula.core.types import Task, TaskPriority, TaskStatus
from trishula.team.roles import RoleCatalog
from trishula.team.planner import TeamPlanner, Plan
from trishula.team.swarm import Swarm, DeterministicWorker, Blackboard


class RoleCatalogTests(unittest.TestCase):
    def test_default_roster_present(self):
        cat = RoleCatalog()
        for role in ("architect", "scout", "implementer", "reviewer", "qa", "devops", "docs-writer"):
            self.assertIn(role, cat.names())

    def test_scout_is_read_only(self):
        cat = RoleCatalog()
        self.assertTrue(cat.get("scout").read_only)
        self.assertFalse(cat.get("implementer").read_only)

    def test_role_prompt_mentions_mission_and_task(self):
        cat = RoleCatalog()
        prompt = cat.role_prompt("qa", "Verify login", "run tests", {"findings": "found X"})
        self.assertIn("QA", prompt)
        self.assertIn("Verify login", prompt)
        self.assertIn("found X", prompt)

    def test_register_custom_role(self):
        cat = RoleCatalog()
        from trishula.team.roles import Role
        cat.register(Role(name="security", title="Sec", mission="hacks", tools=("*",), produces="audit"))
        self.assertIn("security", cat.names())


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        (Path(self.tmp.name) / "package.json").write_text('{"scripts": {"test": "jest"}}')
        self.planner = TeamPlanner(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_heuristic_plan_has_gates_in_order(self):
        plan = self.planner.plan("fix the login button bug in the frontend")
        roles_in_order = [t.assignee for t in plan.tasks]
        self.assertEqual(roles_in_order[0], "scout")
        self.assertIn("architect", roles_in_order)
        self.assertIn("implementer", roles_in_order)
        self.assertIn("reviewer", roles_in_order)
        self.assertIn("qa", roles_in_order)
        self.assertEqual(roles_in_order[-1], "qa")  # qa gates completion

    def test_bug_goal_creates_root_cause_task(self):
        plan = self.planner.plan("fix a crash in the parser")
        titles = " ".join(t.title for t in plan.tasks).lower()
        self.assertIn("root-cause", titles)

    def test_docs_goal_adds_writer_at_end(self):
        plan = self.planner.plan("write README docs for the new API")
        self.assertEqual(plan.tasks[-1].assignee, "docs-writer")

    def test_ci_goal_adds_devops(self):
        plan = self.planner.plan("set up the CI deploy pipeline with docker")
        self.assertIn("devops", [t.assignee for t in plan.tasks])

    def test_dag_acyclic_and_deps_valid(self):
        plan = self.planner.plan("add an API endpoint for orders")
        ids = {t.id for t in plan.tasks}
        for t in plan.tasks:
            for d in t.deps:
                self.assertIn(d, ids)
        # ready() initially returns exactly the no-dep tasks
        ready = plan.ready()
        self.assertTrue(ready)
        self.assertTrue(all(not t.deps for t in ready))

    def test_cycle_detection_rejects_bad_plan(self):
        a = Task(title="a", assignee="scout")
        b = Task(title="b", assignee="qa")
        c = Task(title="c", assignee="implementer")
        a.deps = [c.id]
        b.deps = [a.id]
        c.deps = [b.id]
        with self.assertRaises(PlanningError):
            TeamPlanner._validate(Plan(goal="x", tasks=[a, b, c]))

    def test_unknown_role_rejected(self):
        a = Task(title="a", assignee="wizard")
        with self.assertRaises(PlanningError):
            TeamPlanner._validate(Plan(goal="x", tasks=[a]))


class BlackboardTests(unittest.TestCase):
    def test_append_and_artifacts(self):
        b = Blackboard()
        b.append_findings("scout", "found it")
        b.add_artifact("a.py")
        b.add_artifact("a.py")  # dedup
        b.decide("use option B")
        self.assertIn("found it", b.findings)
        self.assertEqual(b.read()["artifacts"], ["a.py"])
        self.assertEqual(b.read()["decisions"], ["use option B"])


class SwarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        (Path(self.tmp.name) / "pyproject.toml").write_text("[project]\nname='x'\n")
        self.cfg = TrishulaConfig(home=self.tmp.name + "/.state", team_max_workers=4)
        self.planner = TeamPlanner(self.tmp.name, config=self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_deterministic_swarm_completes_all_tasks(self):
        plan = self.planner.plan("add an orders API endpoint")
        swarm = Swarm(self.tmp.name, plan, worker=DeterministicWorker(), config=self.cfg)
        report = swarm.execute()
        self.assertTrue(report.ok, [r.error for r in report.failed_tasks])
        self.assertEqual(
            len(report.results), len(plan.tasks)
        )
        self.assertTrue(all(r.status == TaskStatus.DONE for r in report.results))
        # findings accumulated for every role
        self.assertIn("scout", report.board["findings"])

    def test_worker_failure_retries_then_marks_failed(self):
        calls = {"n": 0}

        def flaky_implementer(task, board):
            calls["n"] += 1
            raise RuntimeError("boom")

        worker = DeterministicWorker(actions={"implementer": flaky_implementer})
        plan = self.planner.plan("add feature X")
        cfg = TrishulaConfig(home=self.tmp.name + "/.state2", team_max_attempts=2)
        swarm = Swarm(self.tmp.name, plan, worker=worker, config=cfg)
        report = swarm.execute()
        self.assertFalse(report.ok)
        failed = report.failed_tasks
        self.assertTrue(failed)
        self.assertGreaterEqual(calls["n"], 2)  # retried

    def test_review_rejection_reopens_and_reruns(self):
        state = {"reviews": 0}

        def reviewer(task, board):
            state["reviews"] += 1
            if state["reviews"] == 1:
                return "REVIEW: REJECT — missing edge case at parser.py:10"
            return "REVIEW: APPROVE — fixed"

        worker = DeterministicWorker(actions={"reviewer": reviewer})
        plan = self.planner.plan("fix parser bug")
        swarm = Swarm(self.tmp.name, plan, worker=worker, config=self.cfg)
        report = swarm.execute()
        self.assertTrue(report.ok, [r.error for r in report.failed_tasks])
        self.assertEqual(state["reviews"], 2)  # re-reviewed after repairs

    def test_parallel_and_sequential_modes_agree(self):
        goal = "add an API endpoint"
        p1 = self.planner.plan(goal)
        p2 = TeamPlanner(self.tmp.name, config=self.cfg).plan(goal)
        cfg_seq = TrishulaConfig(home=self.tmp.name + "/.seq", team_parallel=False)
        r1 = Swarm(self.tmp.name, p1, worker=DeterministicWorker(), config=self.cfg).execute()
        r2 = Swarm(self.tmp.name, p2, worker=DeterministicWorker(), config=cfg_seq).execute()
        self.assertEqual(r1.ok, r2.ok)
        self.assertEqual(len(r1.results), len(r2.results))


if __name__ == "__main__":
    unittest.main()
