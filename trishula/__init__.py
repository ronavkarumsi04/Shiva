"""TRISHULA — the Shiva Agent upgrade core.

The three prongs of Shiva's trident, plus the hand that wields it:

1. **KARANA** (``trishula.coding``)  — Claude-Code-grade coding engine.
   Precise edit primitives (str_replace / insert / undo), a ctags-free
   repository map, a rank-the-files-then-grep context engine, an edit->test
   verifier, and the Plan-Act-Verify agentic coding loop.

2. **AYUDHA** (``trishula.tools``)  — Codex-grade tooling & sandboxing.
   A declarative tool registry with JSON-schema generation, a virtual
   filesystem workspace with path-escape guards, and a shell executor with
   timeouts, output caps, command denylists, network denial and (where
   available) OS-level isolation.

3. **CHIT-SHODHANA** (``trishula.autonomy``) — Hermes-grade autonomy and
   self-improvement. A retrospective loop that scores trajectories, an
   extractor that distills repeatable tactics into reusable Skills, a BM25
   skill search, and a refinement loop that patches a skill the moment it
   fails or succeeds in a new way.

4. **DEVAS** (``trishula.team``)  — Devin-grade software teams.
   A planner that compiles a goal into an ordered, dependency-linked task
   DAG, a catalogue of specialist roles, and a swarm executor that runs
   role-assigned workers in parallel with a shared blackboard and a
   deterministic fallback when no LLM is available.

Everything in this package is **stdlib-only** so it degrades gracefully on
Termux, sandboxes, and fresh machines with no extras installed. Heavy
capabilities are enabled, never required: when a model client is absent the
engines run in *deterministic* mode (rule-based planning, shell-only
verification, scriptable fake agents in tests).
"""

from __future__ import annotations

__version__ = "1.0.0"
__codename__ = "trishula"

from trishula.core.types import (
    Tool, ToolResult, ToolCall,
    Task, TaskStatus, TaskPriority,
    Skill,
    Message, Role as MessageRole,
    Event, EventKind,
    Journal,
)
from trishula.core.errors import TrishulaError, ToolError, SandboxError, EditError
from trishula.core.logging import get_logger
from trishula.core.config import TrishulaConfig
from trishula.tools.registry import ToolRegistry
from trishula.tools.workspace import Workspace
from trishula.tools.shell import Shell
from trishula.coding.edits import EditEngine, Edit
from trishula.coding.repomap import RepoMap
from trishula.coding.context import ContextEngine
from trishula.coding.verifier import Verifier, Verdict
from trishula.coding.loop import CodingLoop
from trishula.autonomy.reflect import Reflector, Retrospective
from trishula.autonomy.skills import SkillLibrary
from trishula.autonomy.loop import AutonomyLoop
from trishula.team.roles import RoleCatalog, Role
from trishula.team.planner import TeamPlanner, Plan
from trishula.team.swarm import Swarm, SwarmReport, Blackboard

__all__ = [
    "__version__", "__codename__",
    # core
    "Tool", "ToolResult", "ToolCall",
    "Task", "TaskStatus", "TaskPriority",
    "Skill", "Message", "MessageRole", "Event", "EventKind", "Journal",
    "TrishulaError", "ToolError", "SandboxError", "EditError",
    "get_logger", "TrishulaConfig",
    # ayudha (tools)
    "ToolRegistry", "Workspace", "Shell",
    # karana (coding)
    "EditEngine", "Edit", "RepoMap", "ContextEngine", "Verifier", "Verdict",
    "CodingLoop",
    # chit-shodhana (autonomy)
    "Reflector", "Retrospective", "SkillLibrary", "AutonomyLoop",
    # devas (teams)
    "RoleCatalog", "Role", "TeamPlanner", "Plan", "Swarm", "SwarmReport",
    "Blackboard",
]
