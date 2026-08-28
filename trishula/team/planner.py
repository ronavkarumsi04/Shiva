"""TeamPlanner: compile a goal into an ordered, dependency-linked task DAG.

Devin's edge is *not* a smarter model — it is treating a goal as a software
project: investigate, design, decompose, implement task-by-task, review,
verify, document. This planner produces that skeleton deterministically, then
fills in tasks from either:

* a real model (JSON plan), or
* a four-pass heuristic that inspects the workspace and the goal text:

  1. **Investigate** — scout the repo (structure, tests, conventions);
  2. **Design gate** — architect owns interface decisions;
  3. **Work items** — split on detected project surfaces (frontend/api/
     backend/tests/ci/docs) inferred from goal keywords + repo contents;
  4. **Quality gates** — reviewer then qa; docs-writer when docs are in scope.

Dependencies form a DAG (investigate → implement tasks may parallelize →
review → qa → docs). The planner validates the DAG (unique ids, no unknown
deps, no cycles) before returning it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from trishula.core.config import TrishulaConfig
from trishula.core.errors import PlanningError
from trishula.core.logging import get_logger
from trishula.core.types import Task, TaskPriority, TaskStatus, new_id
from trishula.llm.base import LLMClient
from trishula.llm.stub import StubClient
from trishula.team.roles import RoleCatalog
from trishula.tools.workspace import Workspace

log = get_logger("team.planner")


@dataclass
class Plan:
    goal: str
    tasks: list[Task] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "rationale": self.rationale,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    def ready(self) -> list[Task]:
        done = {t.id for t in self.tasks if t.status == TaskStatus.DONE}
        return [
            t for t in self.tasks
            if t.status in (TaskStatus.PENDING, TaskStatus.READY)
            and all(d in done for d in t.deps)
        ]

    def get(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)

    @property
    def finished(self) -> bool:
        return all(t.status in (TaskStatus.DONE, TaskStatus.SKIPPED) for t in self.tasks)


_PLAN_SYS = """\
You are the orchestrator of a software development team. Given a goal and a \
repository summary, produce a JSON plan. Respond with ONLY a JSON object:
{
  "rationale": "short reasoning",
  "tasks": [
    {"title": "...", "description": "...", "role": "scout|architect|implementer|reviewer|qa|devops|docs-writer",
     "deps": [titles...], "priority": "low|normal|high|critical",
     "accepts": {"criterion": "description"}}
  ]
}
Rules: 3-12 tasks; first task is scout investigation; implement tasks may run \
in parallel; reviewer then qa must gate completion; deps reference titles."""


class TeamPlanner:
    def __init__(
        self,
        workspace: str | Path | Workspace,
        client: LLMClient | None = None,
        *,
        config: TrishulaConfig | None = None,
        roles: RoleCatalog | None = None,
    ):
        self.cfg = config or TrishulaConfig()
        self.ws = workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        self.client = client or StubClient()
        self.roles = roles or RoleCatalog()

    def plan(self, goal: str, *, repo_hint: str = "") -> Plan:
        repo_summary = repo_hint or self._repo_summary()
        if isinstance(self.client, StubClient):
            plan = self._heuristic_plan(goal, repo_summary)
        else:
            try:
                plan = self._model_plan(goal, repo_summary)
            except Exception as exc:  # noqa: BLE001 - model plans are best-effort
                log.warning("model planning failed (%s); using heuristic", exc)
                plan = self._heuristic_plan(goal, repo_summary)
        self._validate(plan)
        log.info("plan for %r: %d tasks", goal[:60], len(plan.tasks))
        return plan

    # ── heuristic planning (the deterministic brain) ────────────────────

    def _heuristic_plan(self, goal: str, repo_summary: str) -> Plan:
        low = goal.lower()
        tasks: list[Task] = []

        scout = Task(
            title="Investigate repository & conventions",
            description=(
                "Map the modules relevant to the goal. Report exact file:line "
                "references, existing tests, build/test commands, and any "
                f"conventions. Repo summary:\n{repo_summary[:2000]}"
            ),
            assignee="scout",
            priority=TaskPriority.HIGH,
            accepts={"findings": "file:line report covering impl, tests, conventions"},
        )
        tasks.append(scout)

        architect = Task(
            title="Design approach & interfaces",
            description=(
                "Decide the module boundaries, function/class signatures, and "
                "data shapes for this goal. List acceptance criteria per work item."
            ),
            assignee="architect",
            deps=[scout.id],
            priority=TaskPriority.HIGH,
            accepts={"design": "names, signatures, and edge cases documented"},
        )
        tasks.append(architect)

        # Surfaces detected from goal + repo.
        surfaces = self._surfaces(low)
        impl_ids: list[str] = []
        for surface in surfaces["work"]:
            t = Task(
                title=f"Implement: {surface['title']}",
                description=surface["desc"],
                assignee="implementer",
                deps=[architect.id],
                priority=surface.get("priority", TaskPriority.NORMAL),
                accepts=surface["accepts"],
            )
            tasks.append(t)
            impl_ids.append(t.id)

        if surfaces.get("devops"):
            d = Task(
                title="Wire CI / build / packaging",
                description="Add or update CI, Docker, or packaging so the change ships: " + surfaces["devops"],
                assignee="devops",
                deps=[architect.id],
                priority=TaskPriority.NORMAL,
                accepts={"ci": "pipeline config present and locally runnable"},
            )
            tasks.append(d)
            impl_ids.append(d.id)

        review = Task(
            title="Review the diff for defects",
            description="Review every changed file for correctness, edge cases, security, and acceptance-criteria fit. Request changes with file:line findings.",
            assignee="reviewer",
            deps=impl_ids,
            priority=TaskPriority.HIGH,
            accepts={"verdict": "approve with findings list, or change requests", "review_gate": True},
        )
        tasks.append(review)

        qa = Task(
            title="Verify: targeted tests then full suite",
            description="Run the cheapest failing test first, then the full suite. Capture commands and output. All tests must pass.",
            assignee="qa",
            deps=[review.id],
            priority=TaskPriority.CRITICAL,
            accepts={"evidence": "commands + results; zero failures", "verdict": "pass"},
        )
        tasks.append(qa)

        # ── Vishvakarma: physical-engineering work items ────────────────
        eng = self._engineering_surfaces(low)
        eng_ids: list[str] = []
        for item in eng:
            t = Task(
                title=item["title"],
                description=item["desc"],
                assignee=item["role"],
                deps=[architect.id],
                priority=item.get("priority", TaskPriority.NORMAL),
                accepts=item["accepts"],
            )
            tasks.append(t)
            eng_ids.append(t.id)

        # Compliance gate for safety-critical / regulated engineering work.
        gate_deps = impl_ids + eng_ids
        if eng_ids:
            gate = Task(
                title="Standards & certification gate (evidence mapping)",
                description=(
                    "Map every requirement to evidence for the relevant standard "
                    "(e.g. DO-178C/ISO 26262/IEC 62304/IEC 60601/IEC 61508/IPC-2221/ASCE 7). "
                    "Use `trishula safety <gate>` for a deterministic evidence check, then "
                    "list exactly what artifacts/tests/studies are missing."
                ),
                assignee="compliance",
                deps=gate_deps,
                priority=TaskPriority.HIGH,
                accepts={"gate_report": "satisfied vs missing evidence enumerated", "review_gate": True},
            )
            tasks.append(gate)
            # Reviewer waits for the compliance gate as well.
            review.deps = impl_ids + eng_ids + [gate.id]

        if surfaces.get("docs"):
            docs = Task(
                title="Document the change",
                description=surfaces["docs"],
                assignee="docs-writer",
                deps=[qa.id],
                priority=TaskPriority.LOW,
                accepts={"docs": "README/docs updated to match behavior"},
            )
            tasks.append(docs)

        rationale = "Deterministic team plan: investigate → design → implement/engineer → compliance → review → verify."
        return Plan(goal=goal, tasks=tasks, rationale=rationale)

    # ── model planning ──────────────────────────────────────────────────

    def _model_plan(self, goal: str, repo_summary: str) -> Plan:
        from trishula.core.types import Message

        resp = self.client.complete(
            [
                Message.system(_PLAN_SYS),
                Message.user(f"GOAL: {goal}\n\nREPO:\n{repo_summary[:4000]}"),
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        data = self._extract_json(resp.content)
        tasks: list[Task] = []
        title_to_id: dict[str, str] = {}
        for raw in data.get("tasks", []):
            t = Task(
                title=raw["title"],
                description=raw.get("description", ""),
                assignee=raw.get("role", "implementer"),
                priority=TaskPriority(raw.get("priority", "normal")),
                accepts=raw.get("accepts", {}),
            )
            title_to_id[t.title] = t.id
            tasks.append(t)
        for raw, t in zip(data.get("tasks", []), tasks):
            t.deps = [title_to_id[d] for d in raw.get("deps", []) if d in title_to_id]
        return Plan(goal=goal, tasks=tasks, rationale=data.get("rationale", ""))

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        text = text.strip()
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
        return json.loads(text)

    # ── repo inspection ─────────────────────────────────────────────────

    def _repo_summary(self) -> str:
        try:
            files = self.ws.walk_files(max_files=120)
        except Exception:  # noqa: BLE001
            return "(workspace unreadable)"
        names = [self.ws.rel(f) for f in files[:80]]
        markers = []
        for marker, note in [
            ("package.json", "Node/JS project"),
            ("pyproject.toml", "Python project (pyproject)"),
            ("Cargo.toml", "Rust project"),
            ("go.mod", "Go project"),
            ("Dockerfile", "containerized"),
            (".github", "GitHub Actions CI"),
        ]:
            if any(marker in n for n in names):
                markers.append(note)
        return f"Files ({len(names)} shown): {', '.join(names[:40])}\nMarkers: {', '.join(markers) or 'none'}"

    def _engineering_surfaces(self, goal_low: str) -> list[dict[str, Any]]:
        """Detect physical-engineering work items (Vishvakarma prong)."""
        items: list[dict[str, Any]] = []
        if any(k in goal_low for k in ("circuit", "pcb", "schematic", "voltage", "current", "spice", "analog", "power supply", "kicad")):
            items.append({
                "title": "Electrical design & analysis (simulate before build)",
                "role": "ee",
                "desc": ("Design the circuit: compute current/voltage margins, RC time "
                         "constants, PCB trace current per IPC-2221; run SPICE (ngspice) and "
                         "ERC/DRC (kicad-cli) where available; report margins with units."),
                "accepts": {"simulation": "SPICE/HDL or ERC/DRC evidence", "margins": "trace math + voltage/current derating"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("firmware", "embedded", "microcontroller", "stm32", "esp32", "arduino", "rtos", "sensor", "can bus")):
            items.append({
                "title": "Embedded/firmware implementation with HIL tests",
                "role": "embedded",
                "desc": ("Implement firmware: comms, timers/ISRs, bounds-checked sensor paths, "
                         "diagnostics; build for the target (PlatformIO); provide unit + HIL tests."),
                "accepts": {"builds": "pio run green", "hil": "hardware-in-loop or QEMU evidence"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("stress", "deflection", "bracket", "beam", "shaft", "gear", "bearing", "fea", "structure", "load", "fatigue", "cad", "mechanism")):
            items.append({
                "title": "Mechanical/structural sizing & FEA plan",
                "role": "mechanical",
                "desc": ("Hand-calculate stress, deflection, buckling and factor of safety (≥1.5–3 "
                         "per application); specify materials/tolerances; produce a CalculiX/FEA plan."),
                "accepts": {"hand_calcs": "stress/deflection/FOS with units and assumptions", "fos": "FOS recorded against yield and buckling"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("aerospace", "rocket", "aircraft", "wing", "airfoil", "drone", "uav", "orbit", "satellite", "thrust", "mach", "lift", "drag")):
            items.append({
                "title": "Aerospace analysis & margins",
                "role": "aerospace",
                "desc": ("Compute aerodynamics (lift/drag/dynamic pressure), propulsion (Δv, mass "
                         "flow) or orbital mechanics as applicable; show mass/power margins; plan CFD."),
                "accepts": {"analysis": "aero/propulsion/orbital math with units", "margins": "mass/power budget with reserve"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("medical", "clinical", "patient", "biomed", "physiolog", "catheter", "implant", "ecg", "ekg", "wearable sensor", "fda")):
            items.append({
                "title": "Biomedical risk & clinical validation",
                "role": "biomedical",
                "desc": ("Define clinical workflow and physiological limits; start the ISO 14971 risk "
                         "file; map IEC 60601/62304 evidence; plan validation against physiological ranges."),
                "accepts": {"risk_file": "hazards + mitigations", "clinical": "physiological range validation plan"},
                "priority": TaskPriority.HIGH,
            })
        return items

    def _surfaces(self, goal_low: str) -> dict[str, Any]:
        work: list[dict[str, Any]] = []
        devops = ""
        docs = ""

        has_frontend = any(
            p.exists() for p in [self.ws.root / "package.json", self.ws.root / "web", self.ws.root / "ui-tui"]
        )
        if any(k in goal_low for k in ("ui", "frontend", "component", "button", "screen", "page", "css")) and has_frontend:
            work.append({
                "title": "frontend changes",
                "desc": "Implement the UI/frontend portion: components, styling, and interaction states. Follow existing component patterns.",
                "accepts": {"renders": "UI builds without errors", "states": "loading/empty/error states handled"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("api", "endpoint", "server", "route", "backend", "handler")):
            work.append({
                "title": "backend/API changes",
                "desc": "Implement the API/backend portion: routes, handlers, validation, and error responses.",
                "accepts": {"endpoint": "handles success and error paths", "validation": "invalid inputs rejected"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("test", "coverage", "qa")):
            work.append({
                "title": "test coverage",
                "desc": "Add tests covering the new behavior and edge cases; ensure the suite is green.",
                "accepts": {"tests": "tests for success + failure paths"},
            })
        if any(k in goal_low for k in ("fix", "bug", "error", "crash", "regression")):
            work.append({
                "title": "root-cause fix",
                "desc": "Reproduce the failure, identify root cause, implement the minimal fix, and add a regression test.",
                "accepts": {"regression_test": "a test that fails before and passes after", "root_cause": "documented"},
                "priority": TaskPriority.CRITICAL,
            })
        if not work:
            work.append({
                "title": "core implementation",
                "desc": "Implement the goal directly in the appropriate module, following existing conventions. Add or update tests for the behavior.",
                "accepts": {"behavior": "goal behavior implemented", "tests": "tests pass"},
                "priority": TaskPriority.HIGH,
            })
        if any(k in goal_low for k in ("ci", "deploy", "docker", "pipeline", "release", "packag")):
            devops = "Goal explicitly requires CI/deploy/packaging work."
        if any(k in goal_low for k in ("doc", "readme", "guide", "tutorial")):
            docs = "Update README/docs with the new behavior, usage examples, and configuration."
        return {"work": work, "devops": devops, "docs": docs}

    # ── DAG validation ──────────────────────────────────────────────────

    @staticmethod
    def _validate(plan: Plan) -> None:
        ids = [t.id for t in plan.tasks]
        if len(ids) != len(set(ids)):
            raise PlanningError("duplicate task ids in plan")
        idset = set(ids)
        for t in plan.tasks:
            for d in t.deps:
                if d not in idset:
                    raise PlanningError(f"task {t.title!r} depends on unknown task id {d}")
            if t.assignee and t.assignee not in RoleCatalog().names():
                raise PlanningError(f"task {t.title!r} assigned to unknown role {t.assignee!r}")
        # cycle detection (Kahn)
        indeg = {t.id: len(t.deps) for t in plan.tasks}
        deps_of: dict[str, list[str]] = {t.id: list(t.deps) for t in plan.tasks}
        # children map
        children: dict[str, list[str]] = {t.id: [] for t in plan.tasks}
        for t in plan.tasks:
            for d in t.deps:
                children[d].append(t.id)
        queue = [i for i, n in indeg.items() if n == 0]
        seen = 0
        while queue:
            node = queue.pop()
            seen += 1
            for c in children[node]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    queue.append(c)
        if seen != len(plan.tasks):
            raise PlanningError("task plan contains a dependency cycle")
        if len(plan.tasks) > (TrishulaConfig().team_max_tasks):
            raise PlanningError("plan exceeds team_max_tasks")
