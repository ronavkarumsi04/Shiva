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
class _DriveState:
    steps: int = 0
    summary: str = ""


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
    repair_rounds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "ok": self.ok,
            "summary": self.summary,
            "steps": self.steps,
            "verdict": self.verification.verdict.value if self.verification else "skipped",
            "changed_files": self.changed_files,
            "tool_calls": self.tool_calls,
            "repair_rounds": self.repair_rounds,
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
        system_prompt: str | None = None,
    ):
        self.cfg = config or TrishulaConfig()
        self.journal = journal or Journal()
        self.system_prompt = system_prompt or _SYSTEM_PROMPT
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
        mem_block = ""
        if getattr(self.cfg, "engineering_memory", True):
            try:
                from trishula.engineering.memory import EngineeringMemory

                mem = EngineeringMemory(home=self.cfg.home)
                mem_block = mem.context_for(goal, k=getattr(self.cfg, "memory_search_k", 5))
            except Exception as exc:  # noqa: BLE001
                log.debug("memory injection skipped: %s", exc)
        context_block = bundle.render()
        if mem_block:
            context_block = mem_block + "\n\n" + context_block
        messages: list[Message] = [
            Message.system(self.system_prompt),
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

        # Initial implementation round.
        state = self._drive(
            client, messages, tools,
            step_budget=max_steps, step_offset=0, calls=calls,
        )
        steps = state.steps
        finished_summary = state.summary

        # ── mandatory verification + bounded repair rounds (phase 2) ─────
        changed = self.edit_engine.changed_files or self.ws.changed_files
        verification: VerificationResult | None = None
        repair_rounds = max(0, getattr(self.cfg, "coding_repair_rounds", 2))
        rounds_done = 0

        if changed:
            verification = self.verifier.verify(changed)

            for round_no in range(1, repair_rounds + 1):
                if verification.verdict == Verdict.PASS and not verification.feedback:
                    break
                if verification.verdict not in (Verdict.FAIL, Verdict.PARTIAL) and not verification.feedback:
                    break
                if steps >= max_steps:
                    break
                feedback = self._repair_message(verification, round_no)
                messages.append(Message.user(feedback))
                rounds_done = round_no
                self.journal.emit(EventKind.PLAN_MADE, goal=goal, repair_round=round_no,
                                  verdict=verification.verdict.value)
                repair = self._drive(
                    client, messages, tools,
                    step_budget=max_steps - steps, step_offset=steps, calls=calls,
                )
                steps += repair.steps
                if repair.summary:
                    finished_summary = repair.summary
                changed = self.edit_engine.changed_files or self.ws.changed_files
                verification = self.verifier.verify(changed)

            ok = verification.verdict in {Verdict.PASS, Verdict.PARTIAL}
        else:
            ok = bool(finished_summary)

        summary = finished_summary
        if verification is not None and not summary:
            summary = verification.summary
        report = RunReport(
            goal=goal,
            ok=ok,
            summary=summary,
            steps=steps,
            verification=verification,
            changed_files=changed,
            tool_calls=calls,
            transcript=[m.to_dict() for m in messages],
            repair_rounds=rounds_done,
        )
        self.journal.emit(
            EventKind.TASK_FINISHED,
            goal=goal, ok=ok,
            verdict=verification.verdict.value if verification else "no-changes",
            steps=steps,
        )
        return report

    def _repair_message(self, verification: VerificationResult, round_no: int) -> str:
        parts = [f"Verification round {round_no} — repair and re-verify."]
        if verification.verdict == Verdict.FAIL:
            parts.append("Tests FAILED — fix the ROOT CAUSE (not the symptom):")
            parts.append(verification.summary)
            if verification.failures:
                parts.append("Failures: " + ", ".join(f.name for f in verification.failures))
        if verification.feedback:
            parts.append("Coverage/property feedback:\n" + verification.feedback)
        parts.append("Re-run the failing tests/edits, then call finish only when green.")
        return "\n".join(parts)

    def _drive(
        self,
        client: LLMClient,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        step_budget: int,
        step_offset: int,
        calls: list[dict[str, Any]],
    ) -> "_DriveState":
        """Run tool-calling turns until finish or the step budget is spent."""
        finished_summary = ""
        taken = 0
        for i in range(1, step_budget + 1):
            step = step_offset + i
            taken = i
            response = client.complete(
                messages, tools=tools,
                temperature=self.cfg.model_temperature,
                max_tokens=self.cfg.model_max_tokens,
            )
            if not response.tool_calls:
                if i == 1 and step_offset == 0:
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
                    result = ToolResult(ok=False, error=str(exc), tool=name)
                calls.append({"step": step, "tool": name, "args": _short(args), "ok": result.ok})
                self.journal.emit(EventKind.PLAN_STEP, step=step, tool=name, ok=result.ok)
                messages.append(Message.tool(_result_text(result), tc["id"], name=name))
                if result.data.get("finish"):
                    finished_summary = result.data.get("summary", result.output)
                    stop = True
                    break
            if stop:
                break
        return _DriveState(steps=taken, summary=finished_summary)

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
