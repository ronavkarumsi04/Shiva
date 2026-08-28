"""Swarm: execute a Plan as a parallel team of role-specialist workers.

A Devin-style team is more than delegation — it is coordination. The swarm
provides:

* **Shared blackboard** — a dict every worker reads and appends findings to;
  later tasks see earlier results (the scout's report, architect's design).
* **DAG-aware scheduling** — tasks become ready when all deps are DONE; ready
  tasks run in a bounded thread pool (``team_max_workers``), so independent
  implementers parallelize while gates (review→qa) stay ordered.
* **Worker abstraction** — a :class:`Worker` performs one task. The default
  :class:`LocalAgentWorker` runs a mini :class:`CodingLoop` with a role-scoped
  system prompt and role-restricted tools. :class:`DeterministicWorker`
  performs scriptable offline actions (used by the stub and tests). Custom
  workers can delegate to remote agents, MCP servers, or humans.
* **Review gates** — reviewer tasks can REJECT a task, which re-opens its
  dependencies for one more attempt (bounded by ``max_attempts``).
* **Resilience** — a task failure is retried up to ``max_attempts``; the
  plan continues around independent branches and the final report marks
  what failed instead of crashing the run.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from trishula.core.config import TrishulaConfig
from trishula.core.errors import TeamError
from trishula.core.logging import get_logger
from trishula.core.types import EventKind, Journal, Task, TaskStatus, new_id, now
from trishula.team.planner import Plan
from trishula.team.roles import RoleCatalog

log = get_logger("team.swarm")


# ── blackboard ───────────────────────────────────────────────────────────────


class Blackboard:
    """Thread-safe shared scratch space for the swarm."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "findings": "",
            "artifacts": [],
            "decisions": [],
            "reviews": [],
        }

    def read(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def append_findings(self, who: str, text: str) -> None:
        with self._lock:
            self._data["findings"] += f"\n## {who}\n{text}\n"

    def add_artifact(self, path: str) -> None:
        with self._lock:
            if path not in self._data["artifacts"]:
                self._data["artifacts"].append(path)

    def decide(self, decision: str) -> None:
        with self._lock:
            self._data["decisions"].append(decision)

    def review(self, task_id: str, verdict: str, detail: str) -> None:
        with self._lock:
            self._data["reviews"].append(
                {"task": task_id, "verdict": verdict, "detail": detail, "at": now()}
            )

    @property
    def findings(self) -> str:
        with self._lock:
            return self._data["findings"]


# ── workers ──────────────────────────────────────────────────────────────────


class Worker(Protocol):
    name: str
    def perform(self, task: Task, plan: Plan, board: Blackboard, *,
                workspace: "object | None" = None) -> str: ...


@dataclass
class DeterministicWorker:
    """Offline worker: maps role → a callable action (used in stub mode/tests)."""

    name: str = "deterministic"
    actions: dict[str, Callable[..., str]] = field(default_factory=dict)

    def perform(self, task: Task, plan: Plan, board: Blackboard, *,
                workspace: "object | None" = None) -> str:
        action = self.actions.get(task.assignee)
        if action is not None:
            try:
                out = action(task, board, workspace)
            except TypeError as exc:
                # tolerate action callbacks written for the (task, board)
                # signature before workspaces existed
                if "positional argument" in str(exc):
                    out = action(task, board)
                else:
                    raise
        else:
            out = self._default(task, board)
        board.append_findings(f"{task.assignee}:{task.title[:40]}", out)
        return out

    @staticmethod
    def _default(task: Task, board: Blackboard) -> str:
        # Produce a role-appropriate but deterministic artifact string.
        role = task.assignee
        if role == "qa":
            return "VERDICT: PASS — targeted checks and full suite completed locally (deterministic)."
        if role == "reviewer":
            return "REVIEW: APPROVE — no defects found in the described diff (deterministic review)."
        if role == "architect":
            board.decide(f"Design for {task.title[:60]}: minimal modular change behind existing interfaces.")
            return "Design recorded: modules, signatures, and acceptance criteria defined."
        return f"Completed: {task.title} (deterministic {role} action)."


class LocalAgentWorker:
    """Real worker: a role-scoped mini coding loop.

    Built lazily so importing the team package never forces the coding stack.
    The worker receives only the tools its role is allowed (everything else
    is hidden from the model), and its results land on the blackboard.
    """

    name = "local-agent"

    def __init__(self, workspace: str | Path, client, config: TrishulaConfig | None = None,  # noqa: ANN001
                 journal: Journal | None = None):
        self.workspace = workspace
        self.client = client
        self.cfg = config or TrishulaConfig()
        self.journal = journal

    def perform(self, task: Task, plan: Plan, board: Blackboard, *,
                workspace: Workspace | None = None) -> str:
        from trishula.coding.loop import CodingLoop
        from trishula.llm import get_client
        from trishula.tools.builtin import build_registry
        from trishula.tools.shell import Shell

        roles = RoleCatalog()
        role = roles.get(task.assignee)
        ws = workspace or Workspace(self.workspace)
        shell = Shell(
            ws.root,
            timeout=self.cfg.shell_timeout_default,
            allow_network=self.cfg.shell_allow_network,
            deny_commands=self.cfg.shell_deny_commands,
            journal=self.journal,
        )
        registry = build_registry(ws, shell, config=self.cfg, journal=self.journal)
        self._restrict_tools(registry, role.tools)

        loop = CodingLoop(
            ws, client=self.client or get_client(self.cfg),
            config=self.cfg, journal=self.journal or Journal(), registry=registry, shell=shell,
        )
        prompt = roles.role_prompt(role.name, task.title, task.description, board.read())
        report = loop.run(prompt, max_steps=max(8, self.cfg.coding_max_steps // 2))
        summary = report.summary or ("done" if report.ok else "incomplete")
        for f in report.changed_files:
            board.add_artifact(f)
        return (
            f"{summary}\nchanged: {', '.join(report.changed_files) or '(none)'}\n"
            f"verification: {report.verification.verdict.value if report.verification else 'skipped'}"
        )

    @staticmethod
    def _restrict_tools(registry, allowed: tuple[str, ...]) -> None:  # noqa: ANN001
        if "*" in allowed:
            return
        keep = set(allowed) | {"finish", "todo"}
        for name in list(registry.names()):
            if name not in keep:
                registry._tools.pop(name, None)


# ── swarm ────────────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    task_id: str
    title: str
    assignee: str
    status: TaskStatus
    output: str = ""
    attempts: int = 0
    duration_ms: float = 0.0
    error: str = ""


@dataclass
class SwarmReport:
    goal: str
    ok: bool
    results: list[TaskResult]
    artifacts: list[str]
    decisions: list[str]
    board: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "ok": self.ok,
            "results": [
                {
                    "task": r.title, "assignee": r.assignee,
                    "status": r.status.value, "attempts": r.attempts,
                    "error": r.error,
                }
                for r in self.results
            ],
            "artifacts": self.artifacts,
            "decisions": self.decisions,
        }

    @property
    def failed_tasks(self) -> list[TaskResult]:
        return [r for r in self.results if r.status == TaskStatus.FAILED]


class Swarm:
    def __init__(
        self,
        workspace: str | Path,
        plan: Plan,
        *,
        worker: Worker | None = None,
        client=None,  # noqa: ANN001
        config: TrishulaConfig | None = None,
        journal: Journal | None = None,
        roles: RoleCatalog | None = None,
    ):
        self.cfg = config or TrishulaConfig()
        self.workspace = Path(workspace)
        self.plan = plan
        self.journal = journal or Journal()
        self.roles = roles or RoleCatalog()
        self.board = Blackboard()
        self._client = client
        self.worker = worker or LocalAgentWorker(
            self.workspace, client=client, config=self.cfg, journal=self.journal
        )
        self.results: dict[str, TaskResult] = {}
        self._pool_lock = threading.Lock()
        self._pool = None
        if self.cfg.team_use_worktrees:
            try:
                from trishula.team.worktree import WorktreePool

                self._pool = WorktreePool(
                    self.workspace, max_worktrees=max(1, self.cfg.team_max_workers),
                    journal=self.journal, client=self._client,
                )
                if not self._pool.is_git:
                    self._pool = None  # degrade to in-place on non-git repos
            except Exception:  # noqa: BLE001
                self._pool = None

    def _llm_client(self):
        """Return an LLM client for the merge arbiter (None in offline mode)."""
        if self._client is not None:
            return self._client
        try:
            from trishula.llm import get_client

            client = get_client(self.cfg)
            # The deterministic stub cannot reconcile conflicts; leave None so
            # the arbiter uses safe deterministic rules only.
            if client.name != "stub":
                return client
        except Exception:  # noqa: BLE001
            pass
        return None

    def execute(self) -> SwarmReport:
        start = time.monotonic()
        max_workers = max(1, self.cfg.team_max_workers) if self.cfg.team_parallel else 1
        log.info(
            "swarm starting: %d tasks, parallel=%s, max_workers=%d",
            len(self.plan.tasks), self.cfg.team_parallel, max_workers,
        )
        self.journal.emit(EventKind.TEAM_SPAWN, tasks=len(self.plan.tasks), worker=self.worker.name)

        rounds = 0
        max_rounds = len(self.plan.tasks) * self.cfg.team_max_attempts + 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            while not self.plan.finished and rounds < max_rounds:
                rounds += 1
                ready = self.plan.ready()
                if not ready:
                    break
                futures = {
                    pool.submit(self._run_task, task): task for task in ready
                }
                for fut in concurrent.futures.as_completed(futures):
                    task = futures[fut]
                    try:
                        fut.result()
                    except Exception as exc:  # noqa: BLE001 - swarm survives worker death
                        log.exception("worker for %r crashed", task.title)
                        self._fail(task, f"worker crashed: {exc}")

        ok = all(
            t.status in (TaskStatus.DONE, TaskStatus.SKIPPED) for t in self.plan.tasks
        )
        report = SwarmReport(
            goal=self.plan.goal,
            ok=ok,
            results=[self.results[t.id] for t in self.plan.tasks if t.id in self.results],
            artifacts=self.board.read()["artifacts"],
            decisions=self.board.read()["decisions"],
            board=self.board.read(),
        )
        if self._pool is not None:
            self._pool.cleanup()
        log.info("swarm finished in %.1fs ok=%s failed=%d", time.monotonic() - start, ok, len(report.failed_tasks))
        return report

    # ── task lifecycle ──────────────────────────────────────────────────

    def _run_task(self, task: Task) -> None:
        task.status = TaskStatus.RUNNING
        task.attempts += 1
        self.journal.emit(
            EventKind.TASK_STARTED, task=task.title, role=task.assignee, attempt=task.attempts
        )
        start = time.monotonic()
        isolated = False
        wt = None
        try:
            ws = None
            if self._pool is not None:
                with self._pool_lock:
                    ws, isolated = self._pool.acquire(task.id)
                wt = ws
            output = self.worker.perform(task, self.plan, self.board, workspace=ws)
            duration = (time.monotonic() - start) * 1000
            if isolated and self._pool is not None:
                with self._pool_lock:
                    self._pool.commit_worker_changes(task.id, message=f"Trishula {task.assignee}: {task.title[:60]}")
                    wtres = self._pool.complete(task.id, merge=True)
                if not wtres.ok and wtres.conflict_files:
                    raise TeamError(
                        f"worktree merge conflict in {', '.join(wtres.conflict_files[:5])}"
                    )

            # Review gate: reviewer may request changes -> reopen deps.
            if task.assignee == "reviewer" and output.upper().startswith("REVIEW: REJECT"):
                self._reopen_deps(task)
                # Requeue the review itself once repairs land; it is not a
                # terminal failure.
                task.status = TaskStatus.PENDING
                task.error = "review requested changes; repairs requeued"
                self.board.review(task.id, "reject", output[:500])
                log.info("review gate rejected task set; repairs + re-review scheduled")
                return
            else:
                task.status = TaskStatus.DONE
                task.finished_at = now()
                task.result = output
                result = TaskResult(
                    task.id, task.title, task.assignee, TaskStatus.DONE,
                    output=output, attempts=task.attempts, duration_ms=duration,
                )
            self.results[task.id] = result
            self.journal.emit(
                EventKind.TASK_FINISHED, task=task.title, role=task.assignee,
                ok=result.status == TaskStatus.DONE,
            )
        except Exception as exc:  # noqa: BLE001
            if isolated and self._pool is not None and task.id in self._pool._active:
                with self._pool_lock:
                    try:
                        self._pool.complete(task.id, merge=False)
                    except Exception:  # noqa: BLE001
                        pass
            self._fail(task, f"{type(exc).__name__}: {exc}")

    def _fail(self, task: Task, error: str) -> None:
        if task.attempts < task.max_attempts and task.attempts < self.cfg.team_max_attempts:
            # Requeue: back to PENDING so it becomes ready again next round.
            task.status = TaskStatus.PENDING
            task.error = error
            log.warning("task %r attempt %d failed: %s (will retry)", task.title, task.attempts, error)
        else:
            task.status = TaskStatus.FAILED
            task.error = error
            task.finished_at = now()
            self.results[task.id] = TaskResult(
                task.id, task.title, task.assignee, TaskStatus.FAILED,
                attempts=task.attempts, error=error,
            )
            self.journal.emit(EventKind.TASK_FAILED, task=task.title, error=error)

    def _reopen_deps(self, review_task: Task) -> None:
        """Re-open the tasks the reviewer gate depends on (one repair round)."""
        reopened = 0
        for dep_id in review_task.deps:
            dep = self.plan.get(dep_id)
            if dep.status in (TaskStatus.DONE, TaskStatus.FAILED) and dep.attempts < max(
                dep.max_attempts, self.cfg.team_max_attempts
            ):
                dep.status = TaskStatus.PENDING
                dep.error = ""
                reopened += 1
        log.info("review rejected; reopened %d task(s) for repair", reopened)
