"""Deterministic offline model.

The stub is not a fake — it is the *fallback brain* that lets Trishula run
usefully with zero API access:

* in the coding loop it drives a rule-based **plan-and-verify** cycle (read
  context → attempt the edit implied by the task → run tests);
* in the planner it decomposes goals into tasks via keyword heuristics;
* in the reflector it scores runs from hard signals (tests passed/failed,
  edits reverted, tool errors) rather than prose.

Any engine that works against the stub works identically with a real model
plugged in, and every test in ``trishula/tests`` runs through it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from trishula.core.types import Message
from trishula.llm.base import LLMClient, LLMResponse


class StubClient(LLMClient):
    name = "stub"

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # The engines drive the stub through *tool calls*: they ask the model
        # "what next?" and the stub picks the first tool that the last message
        # (a tool result) implies. When no tools are offered, it returns a
        # short deterministic summary — used by the reflector/planner callers
        # that expect prose.
        if not tools:
            return LLMResponse(content=self._prose(messages), model=self.name)

        choice = self._choose_tool(messages, tools)
        if choice is None:
            return LLMResponse(
                content="The task is complete; no further tool calls are needed.",
                model=self.name,
            )
        fname, args = choice
        return LLMResponse(
            content="",
            tool_calls=[
                {"id": f"stub_{len(messages)}", "name": fname, "arguments": args}
            ],
            model=self.name,
        )

    # ── tool selection heuristics ───────────────────────────────────────

    def _choose_tool(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]] | None:
        by_name = {
            t.get("function", {}).get("name", ""): t for t in tools
        }
        last = messages[-1] if messages else None
        last_text = last.content if last else ""

        def have(name: str) -> bool:
            return name in by_name

        # Fresh user request → plan first.
        if last and last.role.value == "user" and have("make_plan"):
            return "make_plan", {"goal": last_text[:500]}

        # After a plan → act on the first pending step.
        if have("run_task_step") and "plan" in last_text.lower():
            return "run_task_step", {}
        if have("run_task_step") and '"status": "ready"' in last_text.replace("'", '"'):
            return "run_task_step", {}

        # After any tool result: if verification/test ran and passed, stop.
        low = last_text.lower()
        if any(k in low for k in ("passed", '"ok": true', "verdict: pass")):
            if have("finish"):
                return "finish", {"summary": "Verification passed."}

        # Default: walk the canonical coding chain if tools exist.
        chain = [
            ("repo_map", {}),
            ("search_code", {"query": _keywords(last_text)}),
            ("read_file", {"path": _guess_path(last_text)}),
            ("str_replace", {"path": _guess_path(last_text)}),
            ("run_shell", {"command": "git status --short"}),
            ("run_tests", {}),
            ("finish", {"summary": "Deterministic stub cycle complete."}),
        ]
        used = {
            tc.get("function", {}).get("name")
            for m in messages
            for tc in getattr(m, "tool_calls", None) or []
        }
        for name, args in chain:
            if name in by_name and name not in used:
                return name, args
        return None

    def _prose(self, messages: list[Message]) -> str:
        last = messages[-1] if messages else None
        text = (last.content if last else "") or ""
        # Planning prose: emit a JSON skeleton the planner can also parse.
        if "plan" in text.lower() and "goal" in text.lower():
            return json.dumps(
                {
                    "summary": "Deterministic plan generated offline.",
                    "tasks": [
                        {"title": "Inspect repository", "role": "scout"},
                        {"title": "Implement change", "role": "implementer"},
                        {"title": "Verify with tests", "role": "qa"},
                    ],
                }
            )
        return "Deterministic offline response: no model configured."


def _keywords(text: str) -> str:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text or "")
    seen: list[str] = []
    for w in words:
        if w.lower() not in {"the", "and", "for", "with", "that", "this", "from", "true", "false", "none"}:
            if w not in seen:
                seen.append(w)
    return " ".join(seen[:6])


def _guess_path(text: str) -> str:
    m = re.search(r"[\w./-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|toml|sh|txt|html|css|rb|go|rs|java|c|h|cpp)", text or "")
    return m.group(0) if m else ""
