"""Shiva agent-facing tools for the Trishula engineering engine.

These four tools let the Shiva agent invoke the trishula engine directly
from any conversation — the same capabilities the `shiva trishula` CLI
exposes, but as model-callable functions:

* ``trishula_code``  — run one autonomous coding task (plan → edit → verify →
  reflect → distill skill). Returns the verdict, changed files, and the
  retrospective score.
* ``trishula_team``  — compile a goal into a Devin-style team plan and
  optionally execute the parallel swarm; ``plan_only`` returns just the DAG.
* ``trishula_skills``— search/list the self-improving skill library.
* ``trishula_runs``  — history of autonomous runs and their scores.

Design constraints, matching the rest of tools/:

* the module must import without any third-party dependency (trishula is
  stdlib-only), so registration never fails on a fresh install;
* heavy work is imported lazily inside handlers;
* results are bounded JSON strings via ``tool_result`` / ``tool_error``;
* the workspace defaults to the agent's current working directory.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from tools.registry import registry, tool_error, tool_result

_EMOJI = "🔱"
_TOOLSET = "trishula"
_MAX_OUTPUT = 6000


def _resolve_workspace(path: str) -> str:
    if not path:
        return os.getcwd()
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.abspath(path)


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 200] + f"\n...[truncated {len(text) - limit} chars]"


def check_trishula_requirements() -> bool:
    """Trishula is stdlib-only — always available."""
    try:
        import trishula  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# =============================================================================
# Handlers
# =============================================================================


def trishula_code_tool(goal: str, path: str = "", max_steps: int = 60) -> str:
    """Run an autonomous coding task through the full learning loop."""
    if not goal or not str(goal).strip():
        return tool_error("`goal` is required: describe the coding task")
    try:
        from trishula.autonomy.loop import AutonomyLoop
        from trishula.core.config import TrishulaConfig

        cfg = TrishulaConfig()
        loop = AutonomyLoop(_resolve_workspace(path), config=cfg)
        run = loop.coding_task(str(goal), max_steps=int(max_steps or 60))
        report = run.report
        retro = run.retrospective
        return tool_result({
            "ok": report.get("ok"),
            "verdict": report.get("verdict"),
            "steps": report.get("steps"),
            "changed_files": report.get("changed_files", []),
            "summary": _truncate(str(report.get("summary", "")), 1500),
            "retrospective_score": retro.get("score"),
            "retrospective_lessons": retro.get("lessons", [])[:6],
            "skills_used": run.skills_used,
            "skills_created": run.skills_created,
        })
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"trishula_code failed: {type(exc).__name__}: {exc}")


def trishula_team_tool(goal: str, path: str = "", plan_only: bool = True,
                       execute: bool = False) -> str:
    """Plan (and optionally execute) a goal as a Devin-style dev team."""
    if not goal or not str(goal).strip():
        return tool_error("`goal` is required: describe the project goal")
    try:
        from trishula.core.config import TrishulaConfig
        from trishula.llm import get_client
        from trishula.team.planner import TeamPlanner
        from trishula.team.swarm import DeterministicWorker, Swarm

        cfg = TrishulaConfig()
        workspace = _resolve_workspace(path)
        client = get_client(cfg)
        planner = TeamPlanner(workspace, client=client, config=cfg)
        plan = planner.plan(str(goal))

        tasks = [
            {
                "title": t.title,
                "role": t.assignee,
                "deps": [plan.get(d).title for d in t.deps],
                "priority": t.priority.value,
                "accepts": t.accepts,
            }
            for t in plan.tasks
        ]
        payload: Dict[str, Any] = {
            "rationale": plan.rationale,
            "task_count": len(tasks),
            "tasks": tasks,
        }

        if execute or (not plan_only and execute is not False):
            worker = DeterministicWorker() if cfg.deterministic else None
            swarm = Swarm(
                workspace, plan,
                worker=worker, client=None if worker else client,
                config=cfg,
            )
            report = swarm.execute()
            payload["executed"] = True
            payload["swarm_ok"] = report.ok
            payload["results"] = [
                {"role": r.assignee, "task": r.title,
                 "status": r.status.value, "attempts": r.attempts,
                 "error": r.error}
                for r in report.results
            ]
            payload["artifacts"] = report.artifacts
        else:
            payload["executed"] = False
        return tool_result(payload)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"trishula_team failed: {type(exc).__name__}: {exc}")


def trishula_skills_tool(action: str = "list", query: str = "") -> str:
    """List or search the self-improving skill library."""
    try:
        from trishula.autonomy.skills import SkillLibrary
        from trishula.core.config import TrishulaConfig

        lib = SkillLibrary(TrishulaConfig())
        if action == "search":
            if not query:
                return tool_error("`query` is required for action=search")
            hits = lib.search(str(query))
            return tool_result({
                "action": "search",
                "query": query,
                "results": [
                    {
                        "name": s.name,
                        "quality": s.quality,
                        "uses": s.uses,
                        "when_to_use": s.when_to_use[:300],
                        "steps": s.steps[:8],
                        "score": round(score, 3),
                    }
                    for s, score in hits
                ],
            })
        stats = lib.usage_stats()
        return tool_result({
            "action": "list",
            "count": len(stats),
            "skills": stats[:50],
        })
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"trishula_skills failed: {type(exc).__name__}: {exc}")


def trishula_runs_tool(limit: int = 20) -> str:
    """Show recent autonomous runs with verdict and retrospective score."""
    try:
        from pathlib import Path

        from trishula.autonomy.loop import AutonomyLoop
        from trishula.core.config import TrishulaConfig

        loop = AutonomyLoop(Path.cwd(), config=TrishulaConfig())
        history = loop.history(limit=max(1, min(int(limit or 20), 200)))
        return tool_result({"count": len(history), "runs": history})
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"trishula_runs failed: {type(exc).__name__}: {exc}")


# =============================================================================
# Schemas
# =============================================================================

TRISHULA_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trishula_code",
        "description": (
            "Run an autonomous software-engineering task in a sandboxed "
            "workspace using the Trishula engine: it curates repository "
            "context, makes precise verified edits, runs the project's tests "
            "(red->green), then reflects and distills a reusable skill from "
            "the winning tactic. Use for real code changes — bug fixes, "
            "features, refactors — where you want guaranteed test verification "
            "and self-improvement. Returns verdict, changed files, and score."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The coding task, e.g. 'fix the off-by-one in pagination and add a regression test'",
                },
                "path": {
                    "type": "string",
                    "description": "Workspace/project root (default: current working directory)",
                    "default": "",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum agentic steps (default 60)",
                    "default": 60,
                },
            },
            "required": ["goal"],
        },
    },
}

TRISHULA_TEAM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trishula_team",
        "description": (
            "Compile a goal into a Devin-style software-team plan: a "
            "dependency-linked task DAG assigned to specialist roles (scout, "
            "architect, implementers in parallel, reviewer, QA, devops, "
            "docs-writer) with review gates. By default returns the plan only; "
            "set execute=true to run the parallel swarm and collect results. "
            "Use for multi-part projects that need coordinated roles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The project goal"},
                "path": {"type": "string", "description": "Project root (default cwd)", "default": ""},
                "plan_only": {
                    "type": "boolean",
                    "description": "Return only the plan DAG without executing (default true)",
                    "default": True,
                },
                "execute": {
                    "type": "boolean",
                    "description": "Execute the swarm after planning (default false)",
                    "default": False,
                },
            },
            "required": ["goal"],
        },
    },
}

TRISHULA_SKILLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trishula_skills",
        "description": (
            "Inspect Trishula's self-improving skill library: list all skills "
            "with quality/usage stats, or search (BM25 + quality) for tactics "
            "distilled from past successful runs. Check this before a complex "
            "task — a reusable skill may already exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "search"],
                    "description": "list all skills, or search by query",
                    "default": "list",
                },
                "query": {
                    "type": "string",
                    "description": "Natural-language search (required for action=search)",
                    "default": "",
                },
            },
        },
    },
}

TRISHULA_RUNS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trishula_runs",
        "description": (
            "Show the history of autonomous Trishula runs with their verdicts "
            "and retrospective scores — useful to review what was attempted, "
            "what passed verification, and what was learned."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max runs to return (default 20)", "default": 20},
            },
        },
    },
}


# =============================================================================
# Registry
# =============================================================================

registry.register(
    name="trishula_code",
    toolset=_TOOLSET,
    schema=TRISHULA_CODE_SCHEMA,
    handler=lambda args, **kw: trishula_code_tool(
        goal=args.get("goal", ""),
        path=args.get("path", ""),
        max_steps=args.get("max_steps", 60),
    ),
    check_fn=check_trishula_requirements,
    emoji=_EMOJI,
    description="Autonomous verified coding task with the Trishula engine",
)

registry.register(
    name="trishula_team",
    toolset=_TOOLSET,
    schema=TRISHULA_TEAM_SCHEMA,
    handler=lambda args, **kw: trishula_team_tool(
        goal=args.get("goal", ""),
        path=args.get("path", ""),
        plan_only=args.get("plan_only", True),
        execute=args.get("execute", False),
    ),
    check_fn=check_trishula_requirements,
    emoji=_EMOJI,
    description="Plan/execute a goal as a Devin-style specialist dev team",
)

registry.register(
    name="trishula_skills",
    toolset=_TOOLSET,
    schema=TRISHULA_SKILLS_SCHEMA,
    handler=lambda args, **kw: trishula_skills_tool(
        action=args.get("action", "list"),
        query=args.get("query", ""),
    ),
    check_fn=check_trishula_requirements,
    emoji=_EMOJI,
    description="Search/list Trishula's self-improving skill library",
)

registry.register(
    name="trishula_runs",
    toolset=_TOOLSET,
    schema=TRISHULA_RUNS_SCHEMA,
    handler=lambda args, **kw: trishula_runs_tool(limit=args.get("limit", 20)),
    check_fn=check_trishula_requirements,
    emoji=_EMOJI,
    description="History of autonomous Trishula runs with scores",
)
