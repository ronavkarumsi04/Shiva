"""AutonomyLoop — the full Hermes-style self-improvement cycle.

    goal ─▶ retrieve relevant skills ─▶ run (coding|team) ─▶ verify
         ─▶ reflect ─▶ score run ─▶ distill/patch skills ─▶ remember

One object owns the whole loop so a cron job, a Telegram command, or a test
can kick off autonomous work with a single call. It persists every run into
``runs.db`` (goal, verdict, score, report, retrospectives), which is the
data the reflector and future planning use to avoid repeating mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trishula.autonomy.reflect import Reflector, Retrospective
from trishula.autonomy.skills import SkillLibrary
from trishula.coding.loop import CodingLoop, RunReport
from trishula.core.config import TrishulaConfig
from trishula.core.logging import get_logger
from trishula.core.storage import Database
from trishula.core.types import EventKind, Journal, Skill, new_id, now
from trishula.llm.base import LLMClient

log = get_logger("autonomy.loop")


@dataclass
class AutonomyRun:
    id: str
    goal: str
    report: dict[str, Any]
    retrospective: dict[str, Any]
    skills_used: list[str] = field(default_factory=list)
    skills_created: list[str] = field(default_factory=list)


class AutonomyLoop:
    def __init__(
        self,
        workspace: str | Path,
        client: LLMClient | None = None,
        *,
        config: TrishulaConfig | None = None,
        library: SkillLibrary | None = None,
    ):
        self.cfg = config or TrishulaConfig()
        self.client = client
        self.workspace = Path(workspace)
        self.library = library or SkillLibrary(self.cfg)
        self.reflector = Reflector()
        self.runs_db = Database(self.cfg.runs_db)
        # Self-improving prompt loop + persistent engineering memory.
        from trishula.autonomy.prompt_evolution import PromptEvolution
        from trishula.engineering.memory import EngineeringMemory

        self.prompt_ev = PromptEvolution(home=self.cfg.home, client=self._llm())
        self.eng_memory = EngineeringMemory(home=self.cfg.home)

    def _llm(self):
        if self.client is not None:
            return self.client
        try:
            from trishula.llm import get_client

            c = get_client(self.cfg)
            return c if c.name != "stub" else None
        except Exception:  # noqa: BLE001
            return None

    def coding_task(self, goal: str, *, max_steps: int | None = None) -> AutonomyRun:
        """Run one coding goal through the full learn loop."""
        journal = Journal()
        run_id = new_id("run")

        # 1. retrieve skills that look relevant and inject them as guidance
        skills = self.library.search(goal, k=self.cfg.skill_search_k)
        skills_used = [s.name for s, _ in skills]
        log.info("injecting %d skills for goal %r", len(skills_used), goal[:60])

        from trishula.coding.loop import _SYSTEM_PROMPT

        loop = CodingLoop(
            self.workspace,
            client=self.client,
            config=self.cfg,
            journal=journal,
            system_prompt=self.prompt_ev.augment_system_prompt(_SYSTEM_PROMPT),
        )
        report = self._run_with_skills(loop, goal, skills, max_steps=max_steps)

        # 2. reflect on hard signals
        retro = self.reflector.reflect(goal, journal, report=report.to_dict())

        # 2b. self-improving prompt loop: distill durable guidance from retro
        try:
            new_rules = self.prompt_ev.learn(retro)
            log.info("prompt evolution: %d rule(s) fired (%d active)",
                     len(new_rules), len(self.prompt_ev.active_rules()))
        except Exception as exc:  # noqa: BLE001
            log.warning("prompt evolution failed: %s", exc)

        # 3. update memory: record outcomes, distill new tactics, patch bad ones
        created: list[str] = []
        for skill, _score in skills:
            success = retro.success and _skill_helped(skill, report)
            self.library.record_use(skill.id, success, context={"goal": goal})
            if not success:
                self.library.refine(
                    skill.id,
                    failure_detail="; ".join(retro.anti_patterns) or retro.narrative,
                    new_steps=[l for l in retro.lessons],
                )
        for proposal in retro.proposed_skills:
            if retro.score >= self.cfg.skill_min_success_quality:
                # Seed quality from the run score: a tactic proven in a
                # 0.9-score run starts strong; a marginal one starts modest.
                s = self.library.promote(
                    proposal, quality=max(0.5, min(0.95, retro.score))
                )
                created.append(s.name)

        # 4. persist
        self._persist_run(run_id, "coding", goal, report, retro, skills_used, created)

        return AutonomyRun(
            id=run_id,
            goal=goal,
            report=report.to_dict(),
            retrospective=retro.to_dict(),
            skills_used=skills_used,
            skills_created=created,
        )

    # ── internals ───────────────────────────────────────────────────────

    def _run_with_skills(
        self,
        loop: CodingLoop,
        goal: str,
        skills: list[tuple[Skill, float]],
        *,
        max_steps: int | None,
    ) -> RunReport:
        if not skills:
            return loop.run(goal, max_steps=max_steps)
        # Inject retrieved skills as an extra system message before the run:
        # we do this by wrapping the client so the first system prompt carries
        # the skill block (keeps CodingLoop's prompt canonical).
        skill_block = self.library.skills_for_prompt(goal)
        original_run = loop.run

        def patched_run(g: str, **kw: Any) -> RunReport:
            # Add the block by temporarily extending the system prompt:
            # simplest robust hook — prepend to goal context.
            enriched = f"{g}\n\n[PRIOR KNOWLEDGE]\n{skill_block}"
            return original_run(enriched, **kw)

        return patched_run(goal, max_steps=max_steps)

    def _persist_run(
        self,
        run_id: str,
        kind: str,
        goal: str,
        report: RunReport,
        retro: Retrospective,
        skills_used: list[str],
        created: list[str],
    ) -> None:
        body = self.runs_db.dumps(
            {
                "report": report.to_dict(),
                "retrospective": retro.to_dict(),
                "skills_used": skills_used,
                "skills_created": created,
            }
        )
        ts = now()
        self.runs_db.execute(
            """INSERT INTO runs (id, kind, goal, status, score, body, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, score=excluded.score,
                 body=excluded.body, updated_at=excluded.updated_at""",
            (
                run_id, kind, goal,
                "success" if retro.success else "failed",
                retro.score, body, ts, ts,
            ),
        )

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.runs_db.query(
            "SELECT id, kind, goal, status, score, created_at FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]


def _skill_helped(skill: Skill, report: RunReport) -> bool:
    """Heuristic: a skill helped if the tools it recommends were exercised."""
    if not skill.tools:
        return True
    used = {c["tool"] for c in report.tool_calls}
    return bool(set(skill.tools) & used)
