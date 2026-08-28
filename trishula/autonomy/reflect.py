"""Reflector: turn a run's journal into a scored retrospective.

After every coding/team run the reflector reads the journaled events and the
run report and answers the questions an engineer asks in a post-mortem:

* **Did it work?** — verdict + whether ``finish`` was reached within budget.
* **What slowed it down?** — failed edits (code drift), repeated identical
  tool calls (thrashing), denied shell commands (wrong assumptions about the
  environment), tool errors, timeouts.
* **What tactics mattered?** — the sequence of *successful* tool calls that
  led to the passing verdict (that sequence is skill material).
* **What should never happen again?** — anti-patterns extracted from failures.

Scores are deterministic from hard signals (0..1). A model, when present, can
enrich the ``narrative`` and ``proposed_skills`` fields; the stub leaves
rule-generated placeholders that are still useful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Sequence

from trishula.core.logging import get_logger
from trishula.core.types import EventKind, Journal, Event

log = get_logger("autonomy.reflect")


@dataclass
class Retrospective:
    run_goal: str
    success: bool
    score: float                     # 0..1 overall quality of the run
    verdict: str = "skipped"
    steps: int = 0
    signals: dict[str, Any] = field(default_factory=dict)
    winning_tactic: list[dict[str, Any]] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    proposed_skills: list[dict[str, Any]] = field(default_factory=list)
    narrative: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Reflector:
    def reflect(
        self,
        goal: str,
        journal: Journal,
        *,
        report: dict[str, Any] | None = None,
    ) -> Retrospective:
        report = report or {}
        events = journal.events()
        # The registry emits a (TOOL_CALL, TOOL_RESULT) pair per invocation;
        # the shell tool emits its own nested pairs under tool="shell" which
        # would double-count, so keep only the outer registry-level events.
        tool_calls = [e for e in journal.events(EventKind.TOOL_CALL)
                      if "step" in e.payload or e.payload.get("tool") != "shell"]
        tool_results = [e for e in journal.events(EventKind.TOOL_RESULT)
                        if e.payload.get("tool") != "shell" or "denied" in (e.payload.get("data") or {})
                        or e.payload.get("timed_out") or e.payload.get("denied")]
        edit_fails = journal.events(EventKind.EDIT_FAILED)
        verdicts = journal.events(EventKind.VERDICT)
        errors = journal.events(EventKind.ERROR)

        verdict = report.get("verdict", verdicts[-1].payload.get("verdict") if verdicts else "skipped")
        success = report.get("ok", verdict in {"pass", "partial"})
        steps = report.get("steps", _max_step(tool_calls))

        # ── hard-signal metrics ─────────────────────────────────────────
        total_calls = len(tool_calls)
        failed_calls = sum(1 for e in tool_results if not e.payload.get("ok", True))
        denied = sum(
            1 for e in tool_results
            if isinstance(e.payload.get("data"), dict) and e.payload["data"].get("denied")
        ) + sum(1 for e in tool_results if e.payload.get("denied"))
        timeouts = 0
        for e in tool_results:
            data = e.payload.get("data")
            if isinstance(data, dict) and data.get("timed_out"):
                timeouts += 1
            if e.payload.get("timed_out"):
                timeouts += 1
        thrash = _thrashing(tool_calls)

        signals = {
            "tool_calls": total_calls,
            "failed_tool_calls": failed_calls,
            "edit_misses": len(edit_fails),
            "denied_commands": denied,
            "timeouts": timeouts,
            "thrashed_calls": thrash,
            "errors": len(errors),
            "steps": steps,
            "verdict": verdict,
        }

        # ── score: start from outcome, deduct process friction ──────────
        score = 1.0 if verdict == "pass" else 0.6 if verdict == "partial" else 0.15
        if not success:
            score = min(score, 0.35)
        friction = (
            0.02 * failed_calls
            + 0.04 * len(edit_fails)
            + 0.03 * denied
            + 0.03 * timeouts
            + 0.05 * thrash
        )
        score = round(max(0.0, min(1.0, score - friction)), 4)

        # ── winning tactic: successful calls up to the good verdict ────
        winning = _winning_tactic(tool_calls, tool_results)

        # ── anti-patterns & lessons ─────────────────────────────────────
        anti: list[str] = []
        lessons: list[str] = []
        if edit_fails:
            anti.append(
                f"str_replace missed {len(edit_fails)} time(s) — re-read the target region and copy exact indentation before editing"
            )
            lessons.append("Read the exact lines (read_file window) immediately before str_replace; never edit from memory.")
        if thrash:
            anti.append(f"{thrash} repeated identical tool call(s) — the loop was stuck; change strategy instead of retrying")
            lessons.append("When the same call fails twice, stop and gather new context instead of retrying.")
        if denied:
            anti.append("attempted sandbox-denied commands — respect the no-network / no-destructive policy")
            lessons.append("Sandbox denies network and destructive commands by design; use local verification only.")
        if timeouts:
            lessons.append("Long-running commands timed out — run targeted tests first, then the full suite.")
        if failed_calls and verdict == "pass":
            lessons.append("Recovered from tool failures — the exact recovery path is part of the tactic.")
        if not success:
            lessons.append("Run did not verify green; the tactic below is incomplete and must be extended.")

        proposed = self._propose_skills(goal, winning, success, signals)
        narrative = self._narrative(goal, signals, success, score)

        retro = Retrospective(
            run_goal=goal,
            success=bool(success),
            score=score,
            verdict=str(verdict),
            steps=steps,
            signals=signals,
            winning_tactic=winning,
            anti_patterns=anti,
            lessons=lessons,
            proposed_skills=proposed,
            narrative=narrative,
        )
        journal.emit(
            EventKind.REFLECT,
            score=score,
            success=bool(success),
            signals=signals,
            skills_proposed=len(proposed),
        )
        log.info("reflection: success=%s score=%.2f signals=%s", success, score, signals)
        return retro

    # ── skill proposal ──────────────────────────────────────────────────

    def _propose_skills(
        self,
        goal: str,
        winning: list[dict[str, Any]],
        success: bool,
        signals: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not success or len(winning) < 2:
            return []
        tool_seq = [w["tool"] for w in winning]
        # Only propose when the run was non-trivial AND clean enough to be
        # worth codifying.
        if signals.get("thrashed_calls", 0) > 2:
            return []
        name = _skill_name(goal)
        steps = _tactic_to_steps(winning)
        when = f"Tasks similar to: {goal[:160]}"
        return [
            {
                "name": name,
                "description": f"Auto-distilled tactic for: {goal[:120]}",
                "when_to_use": when,
                "steps": steps,
                "tools": sorted(set(tool_seq)),
                "tags": _goal_tags(goal),
                "origin": "autonomy",
            }
        ]

    @staticmethod
    def _narrative(goal: str, signals: dict[str, Any], success: bool, score: float) -> str:
        tone = "succeeded" if success else "did not reach a verified state"
        bits = [
            f"Run for goal {goal[:100]!r} {tone} (score {score:.2f}).",
            f"{signals['tool_calls']} tool calls, {signals['failed_tool_calls']} failed, "
            f"{signals['edit_misses']} edit misses, {signals['denied_commands']} denials, "
            f"{signals['thrashed_calls']} thrashed.",
        ]
        return " ".join(bits)


# ── helpers ──────────────────────────────────────────────────────────────────


def _max_step(calls: Sequence[Event]) -> int:
    return max((e.payload.get("step", 0) for e in calls), default=0)


def _thrashing(calls: Sequence[Event]) -> int:
    """Count calls identical to the previous one (same tool+args)."""
    prev: tuple | None = None
    n = 0
    for e in calls:
        sig = (e.payload.get("tool"), _arg_sig(e.payload.get("args", {})))
        if sig == prev:
            n += 1
        prev = sig
    return n


def _arg_sig(args: dict[str, Any]) -> str:
    # Ignore volatile args when detecting repetition.
    stable = {k: v for k, v in (args or {}).items() if k not in {"timeout"}}
    return str(sorted(stable.items()))[:200]


def _winning_tactic(
    calls: Sequence[Event], results: Sequence[Event]
) -> list[dict[str, Any]]:
    """Pair calls with their results, keep the successful, dedupe loops.

    Calls and results are separate event streams (shell emits its own pairs
    inside a tool call), so we pair them positionally *within streams* rather
    than zipping the whole logs.
    """
    out: list[dict[str, Any]] = []
    by_tool: dict[str, bool] = {}
    pairs = list(zip(calls, results))
    if len(calls) != len(results):
        # Fall back to per-call lookup by sequence order up to the shorter.
        pairs = [(c, results[i] if i < len(results) else None) for i, c in enumerate(calls)]
    for c, r in pairs:
        tool = c.payload.get("tool", "")
        ok = True
        if r is not None:
            ok = r.payload.get("ok", True)
        args = c.payload.get("args", {})
        # Collapse repeated read-only exploration to first+last.
        if tool in by_tool and tool in {"read_file", "search_code", "list_dir", "glob", "repo_map"}:
            continue
        by_tool[tool] = ok
        if ok or tool in {"run_shell", "str_replace", "run_tests"}:
            out.append({"tool": tool, "args": _brief(args), "ok": ok})
    return out[:14]


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            brief[k] = v[:120]
        else:
            brief[k] = v
    return brief


def _skill_name(goal: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", goal.lower())
    stop = {"the", "and", "for", "with", "that", "this", "add", "fix", "make", "create", "implement", "please", "should", "file", "code", "test", "tests", "using", "use", "when"}
    kept = [w for w in words if w not in stop][:5]
    if not kept:
        kept = ["task"]
    return "autonomous-" + "-".join(kept)


def _goal_tags(goal: str) -> list[str]:
    low = goal.lower()
    tags = []
    for kw, tag in [
        ("test", "testing"), ("bug", "debugging"), ("fix", "debugging"),
        ("refactor", "refactor"), ("doc", "docs"), ("ci", "devops"),
        ("deploy", "devops"), ("api", "api"), ("ui", "frontend"),
        ("sql", "database"), ("migrat", "database"), ("perf", "performance"),
    ]:
        if kw in low and tag not in tags:
            tags.append(tag)
    return tags or ["general"]


def _tactic_to_steps(winning: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    for w in winning:
        tool = w["tool"]
        args = w.get("args", {})
        if tool == "search_code":
            steps.append(f"Search the codebase for {args.get('query', 'the relevant identifiers')!r} to locate the implementation and its call sites.")
        elif tool == "read_file":
            steps.append(f"Read {args.get('path', 'the target file')} around the region to edit; copy exact indentation.")
        elif tool == "str_replace":
            steps.append(f"Apply a surgical str_replace edit to {args.get('path', 'the target file')}, including surrounding context for uniqueness.")
        elif tool == "write_file":
            steps.append(f"Create/overwrite {args.get('path', 'the required file')} with the complete content.")
        elif tool in {"run_shell", "run_tests"}:
            cmd = args.get("command", "the project's test suite")
            steps.append(f"Run verification: {cmd}. If it fails, read the failure and fix the root cause before continuing.")
        elif tool == "list_dir":
            steps.append("Inspect the relevant directory layout to understand project structure.")
        elif tool == "finish":
            steps.append("Only finish after verification is green; summarize the evidence.")
        else:
            steps.append(f"Use {tool} with {args} as part of the implementation.")
    # Dedup consecutive duplicates while preserving order.
    deduped: list[str] = []
    for s in steps:
        if not deduped or s != deduped[-1]:
            deduped.append(s)
    return deduped
