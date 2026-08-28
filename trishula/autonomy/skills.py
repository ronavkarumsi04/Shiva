"""SkillLibrary — the persistent, self-improving memory of tactics.

Skills are stored as JSON documents in SQLite (``skills.db`` under
``$TRISHULA_HOME``) and retrieved with a from-scratch BM25 over the
searchable fields (``when_to_use`` + ``description`` + ``steps`` + ``tags``).
Retrieval returns the top-k skills *blended with quality*: a skill that has
won real runs ranks higher than an untried proposal.

The library closes the learning loop with three mutations:

* :meth:`promote` — persist a skill proposed by the reflector;
* :meth:`record_use` — after every run where a skill was injected, update
  its EMA quality and write a usage event;
* :meth:`refine` — when a skill was used but the run failed, patch the
  skill: append the observed anti-pattern and (if reflector suggests) new
  steps, bumping ``version``. Skills improve *during* use — the Hermes loop.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from trishula.core.config import TrishulaConfig
from trishula.core.logging import get_logger
from trishula.core.storage import Database
from trishula.core.types import EventKind, Journal, Skill, now

log = get_logger("autonomy.skills")

_TOKEN = re.compile(r"[a-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class SkillLibrary:
    def __init__(
        self,
        config: TrishulaConfig | None = None,
        *,
        db: Database | None = None,
        journal: Journal | None = None,
    ):
        self.cfg = config or TrishulaConfig()
        self.journal = journal
        self.db = db or Database(self.cfg.skills_db)
        self._cache: dict[str, Skill] | None = None

    # ── CRUD ────────────────────────────────────────────────────────────

    def promote(self, proposal: dict[str, Any] | Skill, *, quality: float | None = None) -> Skill:
        """Persist a reflector proposal as a skill (idempotent by name).

        ``quality`` seeds the EMA: a skill distilled from a clean, high-scoring
        run starts above the 0.5 neutral line so it can be retrieved
        immediately (it still has zero recorded uses — that comes from real
        deployments via :meth:`record_use`).
        """
        skill = proposal if isinstance(proposal, Skill) else Skill(
            name=proposal["name"],
            description=proposal.get("description", ""),
            when_to_use=proposal.get("when_to_use", proposal.get("description", "")),
            steps=list(proposal.get("steps", [])),
            tags=list(proposal.get("tags", [])),
            tools=list(proposal.get("tools", [])),
            origin=proposal.get("origin", "autonomy"),
        )
        if quality is not None:
            skill.quality = round(quality, 4)
        existing = self.get_by_name(skill.name)
        if existing is not None:
            # Merge: keep the higher-quality version, union the steps.
            merged = self._merge(existing, skill)
            self._save(merged)
            return merged
        self._save(skill)
        if self.journal:
            self.journal.emit(EventKind.SKILL_SAVED, skill=skill.name, id=skill.id)
        log.info("promoted skill %s (%d steps)", skill.name, len(skill.steps))
        return skill

    def all(self) -> list[Skill]:
        if self._cache is None:
            rows = self.db.query("SELECT body FROM skills ORDER BY updated_at DESC")
            self._cache = {}
            for r in rows:
                s = Skill.from_dict(self.db.loads(r["body"], {}))
                self._cache[s.id] = s
        return list(self._cache.values())

    def get(self, skill_id: str) -> Skill | None:
        row = self.db.query_one("SELECT body FROM skills WHERE id = ?", (skill_id,))
        return Skill.from_dict(self.db.loads(row["body"], {})) if row else None

    def get_by_name(self, name: str) -> Skill | None:
        row = self.db.query_one("SELECT body FROM skills WHERE name = ?", (name,))
        return Skill.from_dict(self.db.loads(row["body"], {})) if row else None

    def _save(self, skill: Skill) -> None:
        skill.updated_at = now()
        body = self.db.dumps(skill.to_dict())
        self.db.execute(
            """INSERT INTO skills (id, name, body, quality, uses, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 body=excluded.body, quality=excluded.quality,
                 uses=excluded.uses, updated_at=excluded.updated_at""",
            (skill.id, skill.name, body, skill.quality, skill.uses, skill.created_at, skill.updated_at),
        )
        if self._cache is not None:
            self._cache[skill.id] = skill

    @staticmethod
    def _merge(a: Skill, b: Skill) -> Skill:
        for step in b.steps:
            if step not in a.steps:
                a.steps.append(step)
        a.quality = max(a.quality, b.quality)
        a.tags = sorted(set(a.tags) | set(b.tags))
        a.tools = sorted(set(a.tools) | set(b.tools))
        a.version += 1
        return a

    # ── BM25 retrieval ──────────────────────────────────────────────────

    def search(self, query: str, *, k: int | None = None) -> list[tuple[Skill, float]]:
        skills = self.all()
        if not skills:
            return []
        k = k or self.cfg.skill_search_k
        docs = [self._searchable(s) for s in skills]
        tokenized = [tokenize(d) for d in docs]
        df: Counter[str] = Counter()
        for toks in tokenized:
            for term in set(toks):
                df[term] += 1
        n_docs = len(skills)
        avg_len = sum(len(t) for t in tokenized) / max(1, n_docs)
        k1, b = 1.5, 0.75
        q_terms = tokenize(query)

        scored: list[tuple[Skill, float]] = []
        for skill, toks in zip(skills, tokenized):
            tf = Counter(toks)
            score = 0.0
            dl = len(toks)
            for term in q_terms:
                if term not in tf:
                    continue
                idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * (tf[term] * (k1 + 1)) / (
                    tf[term] + k1 * (1 - b + b * dl / max(1, avg_len))
                )
            if score > 0:
                # Quality blend: proven skills win ties; terrible skills sink.
                score *= 0.7 + 0.6 * skill.quality
                scored.append((skill, round(score, 4)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def skills_for_prompt(self, query: str, *, k: int | None = None) -> str:
        hits = self.search(query, k=k)
        if not hits:
            return ""
        blocks = []
        for skill, score in hits:
            steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(skill.steps))
            blocks.append(
                f"### Skill: {skill.name} (relevance {score:.2f}, quality {skill.quality:.2f})\n"
                f"When: {skill.when_to_use}\n{steps}"
            )
        return "Reusable skills distilled from past runs:\n\n" + "\n\n".join(blocks)

    @staticmethod
    def _searchable(s: Skill) -> str:
        return " ".join(
            [s.name, s.description, s.when_to_use, " ".join(s.tags), " ".join(s.steps), " ".join(s.tools)]
        )

    # ── usage & refinement (the improving-during-use loop) ──────────────

    def record_use(self, skill_id: str, success: bool, *, context: dict | None = None) -> None:
        skill = self.get(skill_id)
        if skill is None:
            return
        skill.record_outcome(success)
        self._save(skill)
        self.db.execute(
            "INSERT INTO skill_events (skill_id, success, context, at) VALUES (?, ?, ?, ?)",
            (skill_id, 1 if success else 0, self.db.dumps(context or {}), now()),
        )
        if self.journal:
            self.journal.emit(
                EventKind.SKILL_USED, skill=skill.name, success=success, quality=skill.quality
            )

    def refine(
        self,
        skill_id: str,
        *,
        failure_detail: str = "",
        new_steps: Iterable[str] = (),
        anti_patterns: Iterable[str] = (),
    ) -> Skill | None:
        """Patch a skill after it failed — this is skills improving *during* use."""
        skill = self.get(skill_id)
        if skill is None:
            return None
        changed = False
        for step in new_steps:
            if step and step not in skill.steps:
                skill.steps.append(step)
                changed = True
        for ap in anti_patterns:
            if ap and ap not in skill.anti_patterns:
                skill.anti_patterns.append(ap)
                changed = True
        if failure_detail and failure_detail not in skill.anti_patterns:
            skill.anti_patterns.append(failure_detail[:300])
            changed = True
        if changed:
            skill.version += 1
            self._save(skill)
            if self.journal:
                self.journal.emit(EventKind.SKILL_PATCHED, skill=skill.name, version=skill.version)
            log.info("refined skill %s to v%d", skill.name, skill.version)
        return skill

    def usage_stats(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            """SELECT s.name, s.quality, s.uses,
                      (SELECT COUNT(*) FROM skill_events e WHERE e.skill_id=s.id AND e.success=1) AS wins,
                      (SELECT COUNT(*) FROM skill_events e WHERE e.skill_id=s.id AND e.success=0) AS losses
               FROM skills s ORDER BY s.quality DESC"""
        )
        return [dict(r) for r in rows]
