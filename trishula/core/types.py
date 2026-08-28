"""Core data types shared across all Trishula prongs.

Everything here is a frozen-or-plain dataclass with JSON round-tripping so
that:

* the deterministic fallback mode and the LLM-driven mode speak the *same*
  shapes (a plan is a plan whether a model or the rule engine produced it);
* objects can land in SQLite / on the blackboard / in a trajectory file
  without an ORM;
* tests can build fixtures with kwargs only.
"""

from __future__ import annotations

import dataclasses
import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Identifiers & time
# ─────────────────────────────────────────────────────────────────────────────


def new_id(prefix: str = "id") -> str:
    """Return a short, prefixed, collision-resistant id."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Journal / events — the nervous system every prong reads and writes
# ─────────────────────────────────────────────────────────────────────────────


class EventKind(str, enum.Enum):
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    EDIT_APPLIED = "edit.applied"
    EDIT_FAILED = "edit.failed"
    VERDICT = "verify.verdict"
    PLAN_MADE = "plan.made"
    PLAN_STEP = "plan.step"
    TASK_STARTED = "task.started"
    TASK_FINISHED = "task.finished"
    TASK_FAILED = "task.failed"
    MESSAGE = "message"
    REFLECT = "autonomy.reflect"
    SKILL_SAVED = "autonomy.skill_saved"
    SKILL_USED = "autonomy.skill_used"
    SKILL_PATCHED = "autonomy.skill_patched"
    TEAM_SPAWN = "team.spawn"
    TEAM_JOIN = "team.join"
    ERROR = "error"


@dataclass
class Event:
    """A single thing that happened, tagged for the learning loop."""

    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=now)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if isinstance(self.kind, EventKind) else self.kind,
            "payload": self.payload,
            "at": self.at,
            "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            kind=EventKind(d["kind"]),
            payload=d.get("payload", {}),
            at=d.get("at", now()),
            seq=d.get("seq", 0),
        )


class Journal:
    """Append-only event log.

    Subscribers (the reflector, the CLI spinner, a trace uploader) register
    callbacks; the autonomy loop reads events back when scoring a run.
    Journals are deliberately synchronous and in-memory — a process crash
    mid-run loses nothing that SQLite state did not already hold.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._listeners: list[Callable[[Event], None]] = []
        self._seq = 0

    def emit(self, kind: EventKind, **payload: Any) -> Event:
        self._seq += 1
        ev = Event(kind=kind, payload=payload, seq=self._seq)
        self._events.append(ev)
        for listener in list(self._listeners):
            try:
                listener(ev)
            except Exception:  # noqa: BLE001 - a bad listener must not break the run
                pass
        return ev

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsub() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub

    def events(
        self,
        kind: EventKind | Iterable[EventKind] | None = None,
    ) -> list[Event]:
        if kind is None:
            return list(self._events)
        kinds = {kind} if isinstance(kind, EventKind) else set(kind)
        return [e for e in self._events if e.kind in kinds]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._events]

    def __len__(self) -> int:
        return len(self._events)

    def __bool__(self) -> bool:
        # A journal with zero events is still a valid, attached journal —
        # without this, `if self.journal:` fails on empty logs because
        # __len__ is defined.
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Chat messages (model I/O)
# ─────────────────────────────────────────────────────────────────────────────


class Role(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role.value if isinstance(self.role, Role) else self.role,
            "content": self.content,
        }
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(Role.SYSTEM, content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(Role.USER, content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list[dict] | None = None) -> "Message":
        return cls(Role.ASSISTANT, content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, content: str, call_id: str, name: str = "") -> "Message":
        return cls(Role.TOOL, content, tool_call_id=call_id, name=name)


# ─────────────────────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolResult:
    """What every tool invocation returns.

    ``ok=False`` is a *normal* outcome the agent should reason about (a test
    failed, a file was missing).  Exceptions are reserved for harness bugs.
    """

    ok: bool
    output: str = ""
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    tool: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "data": self.data,
            "duration_ms": self.duration_ms,
            "tool": self.tool,
        }


@dataclass
class ToolCall:
    """A request to invoke a tool, from an agent or a plan step."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("call"))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tool": self.tool, "args": self.args}


@dataclass
class Tool:
    """Declarative tool definition.

    The ``parameters`` dict is JSON-Schema-shaped so the same definition
    feeds (a) the model-facing tool list, (b) runtime argument validation,
    and (c) ``MCP``-style discovery. Handlers receive validated kwargs and
    return a :class:`ToolResult` (or a plain string, or a dict).
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any] = field(repr=False, default=lambda **_: ToolResult(False, error="no handler"))
    tags: tuple[str, ...] = ()
    dangerous: bool = False
    read_only: bool = False

    def schema(self) -> dict[str, Any]:
        """JSON-schema fragment for model function-calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Tasks & plans (used by both CodingLoop and the Devas swarm)
# ─────────────────────────────────────────────────────────────────────────────


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "normal": 1, "high": 2, "critical": 3}[self.value]


@dataclass
class Task:
    """A unit of work for the team swarm or the coding loop."""

    title: str
    description: str = ""
    id: str = field(default_factory=lambda: new_id("task"))
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    assignee: str = ""  # role name, e.g. "architect"
    deps: list[str] = field(default_factory=list)
    accepts: dict[str, Any] = field(default_factory=dict)  # acceptance criteria
    result: str = ""
    artifacts: list[str] = field(default_factory=list)  # file paths / urls
    attempts: int = 0
    max_attempts: int = 3
    created_at: float = field(default_factory=now)
    finished_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        d = dict(d)
        d["status"] = TaskStatus(d.get("status", "pending"))
        d["priority"] = TaskPriority(d.get("priority", "normal"))
        return cls(**d)


# ─────────────────────────────────────────────────────────────────────────────
# Skills (the unit of self-improvement)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Skill:
    """A distilled, reusable tactic.

    Skills are the *output* of Hermes-style learning: after a hard-won
    success the reflector writes one of these.  They are plain JSON on disk
    (agentskills.io-compatible in spirit) so the existing Shiva skills
    ecosystem can consume them.
    """

    name: str
    description: str
    when_to_use: str  # trigger description for retrieval
    steps: list[str]  # ordered tactic steps (imperative, model-readable)
    id: str = field(default_factory=lambda: new_id("skill"))
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    origin: str = "autonomy"  # "autonomy" | "human" | "imported"
    uses: int = 0
    successes: int = 0
    failures: int = 0
    quality: float = 0.5  # 0..1 EMA success rate
    examples: list[dict[str, str]] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    version: int = 1
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Skill":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def record_outcome(self, success: bool, alpha: float = 0.3) -> None:
        """Update the EMA quality score after a use."""
        self.uses += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1
        self.quality = round(alpha * (1.0 if success else 0.0) + (1 - alpha) * self.quality, 4)
        self.updated_at = now()
