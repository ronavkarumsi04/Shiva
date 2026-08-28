"""CodingLoop — the Plan → Act → Verify agentic cycle.

This is the heart of the Kāraṇa prong. It runs a bounded tool-calling loop:

    context → model picks tool → tool executes (sandboxed) → result fed back
    → ... until the model calls ``finish`` or the step budget is exhausted,
    then the verifier proves the change works.

Key properties, learned from what makes Claude Code good:

* **Context is curated, not dumped.** The :class:`ContextEngine` selects
  files before the first turn; after that only tool results enter context.
* **Verification is mandatory.** The loop never reports success without a
  :class:`VerificationResult`; failed tests are fed back as the next user
  message with a strict instruction to fix root cause, not symptoms.
* **The loop is model-agnostic.** With a real model the model chooses tools;
  with :class:`StubClient` a deterministic state machine drives the same
  registry — so the harness itself is testable end-to-end without keys.
* **Every action is journaled** for the autonomy reflector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trishula.coding.context import ContextEngine
from trishula.coding.edits import EditEngine
from trishula.coding.verifier import Verifier, Verdict, VerificationResult
from trishula.core.config import TrishulaConfig
from trishula.core.errors import TrishulaError
from trishula.core.logging import get_logger
from trishula.core.types import Journal, EventKind, Message, Role, ToolResult
from trishula.llm.base import LLMClient
from trishula.tools.builtin import build_registry
from trishula.tools.registry import ToolRegistry
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace

log = get_logger("coding.loop")

_SYSTEM_PROMPT = """\
You are Shiva, an elite autonomous software engineer working inside a \
sandboxed workspace. You operate by calling tools; every change MUST be \
proven by verification.

Operating rules:
1. Understand before editing: use repo_map/search_code/read_file to locate \
the exact code. Never write a file you have not read.
2. Make minimal, surgical edits with str_replace. Match indentation exactly; \
include enough context for uniqueness.
3. After implementing, verify: run the relevant tests/build via run_shell. \
If tests fail, read the failure, fix the ROOT CAUSE, and re-run. Never mark \
work done with failing tests.
4. If an edit fails to match, the file drifted — re-read the region and retry.
5. Stay inside the workspace. Do not request secrets or network access.
6. When the change is verified, call finish with a concise summary.
"""


@dataclass
class RunReport:
    goal: str
    ok: bool
    summary: str
    steps: int
    verification: VerificationResult | None = None
    changed_files: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "ok": self.ok,
            "summary": self.summary,
            "steps": self.steps,
            "verdict": self.verification.verdict.value if self.verification else "skipped",
            "changed_files": self.changed_files,
            "tool_calls": self.tool_calls,
        }


class CodingLoop:
    def __init__(
        self,
        workspace: str | Path | Workspace,
        client: LLMClient | None = None,
        *,
        config: TrishulaConfig | None = None,
        journal: Journal | None = None,
        registry: ToolRegistry | None = None,
        shell: Shell | None = None,
    ):
        self.cfg = config or TrishulaConfig()
        self.journal = journal or Journal()
        self.ws = workspace if isinstance(workspace, Workspace) else Workspace(
            workspace, readonly=self.cfg.workspace_readonly
        )
        self.shell = shell or Shell(
            self.ws.root,
            timeout=self.cfg.shell_timeout_default,
            timeout_max=self.cfg.shell_timeout_max,
            output_cap=self.cfg.shell_output_cap,
            allow_network=self.cfg.shell_allow_network,
            deny_commands=self.cfg.shell_deny_commands,
            journal=self.journal,
        )
        self.registry = registry or build_registry(
            self.ws, self.shell, config=self.cfg, journal=self.journal
        )
        self.client = client
        self.context_engine = ContextEngine(self.ws, config=self.cfg)
        self.edit_engine: EditEngine = getattr(self.registry, "_edit_engine", None) or EditEngine(
            self.ws, config=self.cfg, journal=self.journal
        )
        self.verifier = Verifier(self.ws, self.shell, config=self.cfg, journal=self.journal)
        self._register_loop_tools()

    def _register_loop_tools(self) -> None:
        if "finish" not in self.registry:
            def finish(summary: str) -> ToolResult:
                return ToolResult(True, output=f"FINISH: {summary}", data={"finish": True, "summary": summary})
            self.registry.register(
                "finish",
                "Call when the task is fully implemented AND verified. Argument: summary.",
                {
                    "type": "object",
                    "properties": {"summary": {"type": "string", "description": "What was done and verification evidence"}},
                    "required": ["summary"],
                },
                finish,
                tags=("control",),
            )

    def run(self, goal: str, *, max_steps: int | None = None) -> RunReport:
        client = self.client or self._lazy_client()
        max_steps = max_steps or self.cfg.coding_max_steps
        log.info("coding run: %r (max_steps=%d, client=%s)", goal[:80], max_steps, client.name)
        self.journal.emit(EventKind.PLAN_MADE, goal=goal, client=client.name)

        bundle = self.context_engine.build_context(goal)
        context_block = bundle.render()
        messages: list[Message] = [
            Message.system(_SYSTEM_PROMPT),
            Message.user(
                f"TASK: {goal}\n\n"
                f"=== Curated repository context (~{bundle.estimated_tokens} tokens) ===\n"
                f"{context_block}\n"
                f"=== End context. Now make the change and verify it. ==="
            ),
        ]
        tools = self.registry.schemas()
        calls: list[dict[str, Any]] = []
        finished_summary = ""
        steps = 0

        for step in range(1, max_steps + 1):
            steps = step
            response = client.complete(
                messages, tools=tools,
                temperature=self.cfg.model_temperature,
                max_tokens=self.cfg.model_max_tokens,
            )
            if not response.tool_calls:
                # Model answered in prose; nudge it once toward tools, else finish.
                if step == 1:
                    messages.append(Message.assistant(response.content))
                    messages.append(Message.user(
                        "Use the provided tools to implement the change, then call finish."
                    ))
                    continue
                finished_summary = response.content or "Model ended without calling finish."
                break

            messages.append(Message.assistant(response.content, tool_calls=[
                {"id": tc["id"], "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments", {}))}}
                for tc in response.tool_calls
            ]))

            stop = False
            for tc in response.tool_calls:
                name = tc["name"]
                args = tc.get("arguments", {}) or {}
                try:
                    result = self.registry.call(name, args)
                except TrishulaError as exc:
                    # Unknown tool / bad args: feed back as a recoverable error
                    # instead of crashing the run.
                    result = ToolResult(ok=False, error=str(exc), tool=name)
                calls.append({"step": step, "tool": name, "args": _short(args), "ok": result.ok})
                self.journal.emit(EventKind.PLAN_STEP, step=step, tool=name, ok=result.ok)
                messages.append(Message.tool(
                    _result_text(result), tc["id"], name=name
                ))
                if result.data.get("finish"):
                    finished_summary = result.data.get("summary", result.output)
                    stop = True
                    break
            if stop:
                break

        # ── mandatory verification (phase 2: properties + coverage) ─────
        changed = self.edit_engine.changed_files or self.ws.changed_files
        verification = None
        ok = False
        if changed:
            verification = self.verifier.verify(changed)
            if verification.feedback:
                # Coverage gaps / property violations become actionable input.
                messages.append(Message.user(
                    "Verification feedback — strengthen before finishing:\n"
                    + verification.feedback
                ))
            if verification.verdict == Verdict.FAIL and steps < max_steps:
                # One closed-loop repair attempt: feed failures back.
                messages.append(Message.user(
                    "Verification FAILED. Fix the root cause and re-verify.\n"
                    + verification.summary
                    + "\nFailures: " + ", ".join(f.name for f in verification.failures)
                ))
                # (The deterministic stub ignores this extra turn's prose; a
                # real model gets another budget to repair.)
            ok = verification.verdict in {Verdict.PASS, Verdict.PARTIAL}
        else:
            ok = bool(finished_summary)

        report = RunReport(
            goal=goal,
            ok=ok,
            summary=finished_summary or verification.summary if verification else finished_summary,
            steps=steps,
            verification=verification,
            changed_files=changed,
            tool_calls=calls,
            transcript=[m.to_dict() for m in messages],
        )
        self.journal.emit(
            EventKind.TASK_FINISHED,
            goal=goal, ok=ok,
            verdict=verification.verdict.value if verification else "no-changes",
            steps=steps,
        )
        return report

    def _lazy_client(self) -> LLMClient:
        from trishula.llm import get_client
        return get_client(self.cfg)


def _result_text(result: ToolResult) -> str:
    if result.ok:
        return result.output or "(ok)"
    return f"ERROR: {result.error or result.output}"


def _short(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 120:
            out[k] = v[:80] + f"...<{len(v)} chars>"
        else:
            out[k] = v
    return out
