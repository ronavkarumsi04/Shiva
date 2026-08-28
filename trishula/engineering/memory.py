"""Persistent engineering memory — datasheets, decisions, learned constants.

Engineering knowledge that survives across runs and agents: captured component
datasheets, design decisions and their rationale, domain constants measured or
verified in past tasks, and lessons learned. Everything is append-only JSONL on
disk (so a crash corrupts nothing) with an in-memory index rebuilt on load.

What it stores
--------------
* **Datasheet entry** — a component part with parameters
  ``{name: {value, unit, condition, source}}``, e.g. an op-amp's gain-bandwidth,
  supply range, quiescent current.
* **Fact / constant** — a named value within a domain (electrical, mechanical,
  …) with unit, source, and confidence; writing the same key updates it and
  bumps a revision count instead of duplicating.
* **Decision** — a chosen design approach with rationale, context, and tags.
* **Lesson** — something a past run proved (fed by the self-improvement loop).

Honesty rules: every record carries a ``source`` and a ``confidence`` (0..1).
Values without a source are stored but marked low-confidence and never spoken
of as datasheet truth. Nothing is fabricated — memory only returns what was
captured.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from trishula.core.logging import get_logger

log = get_logger("engineering.memory")


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


@dataclass
class MemoryRecord:
    kind: str                      # datasheet | fact | decision | lesson
    key: str                       # stable identity (part id / constant name / slug)
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    domain: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""
    confidence: float = 0.6
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    revisions: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryRecord":
        return cls(
            kind=d["kind"], key=d["key"], text=d.get("text", ""),
            data=d.get("data", {}), domain=d.get("domain", ""),
            tags=d.get("tags", []), source=d.get("source", ""),
            confidence=d.get("confidence", 0.6),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            revisions=d.get("revisions", 1),
        )

    def haystack(self) -> str:
        parts = [self.kind, self.key, self.text, self.domain, " ".join(self.tags)]
        try:
            parts.append(json.dumps(self.data, sort_keys=True))
        except (TypeError, ValueError):
            pass
        return " ".join(parts).lower()


class EngineeringMemory:
    """Append-only engineering knowledge store."""

    def __init__(self, path: str | Path | None = None, *, home: str = ""):
        if path is None:
            base = Path(home) if home else Path.home() / ".trishula"
            path = base / "engineering_memory.jsonl"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, MemoryRecord] = {}
        self._order: list[str] = []
        self._load()

    # ── persistence ─────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = MemoryRecord.from_dict(json.loads(line))
                self._records[rec.key] = rec
                if rec.key not in self._order:
                    self._order.append(rec.key)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.warning("memory load issue (%s); starting with partial index", exc)

    def _append(self, rec: MemoryRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict(), sort_keys=True) + "\n")

    def _put(self, rec: MemoryRecord) -> MemoryRecord:
        existing = self._records.get(rec.key)
        if existing is not None:
            rec.revisions = existing.revisions + 1
            rec.created_at = existing.created_at
            # keep the stronger confidence/source when merging
            if not rec.source and existing.source:
                rec.source = existing.source
            rec.confidence = max(rec.confidence, existing.confidence)
        self._records[rec.key] = rec
        if rec.key not in self._order:
            self._order.append(rec.key)
        self._append(rec)
        return rec

    # ── capture ─────────────────────────────────────────────────────────

    def capture_datasheet(
        self,
        part: str,
        parameters: dict[str, dict[str, Any]],
        *,
        manufacturer: str = "",
        source: str = "",
        domain: str = "",
        tags: list[str] | None = None,
        confidence: float = 0.9,
    ) -> MemoryRecord:
        """Record a component's datasheet parameters.

        ``parameters`` maps parameter name -> {"value", "unit", "condition"}.
        Values without an explicit source are flagged (never faked).
        """
        key = f"ds:{part.strip().lower()}"
        text = f"{manufacturer} {part} datasheet: " + ", ".join(
            f"{n}={p.get('value')}{p.get('unit', '')}" for n, p in list(parameters.items())[:20]
        )
        rec = MemoryRecord(
            kind="datasheet", key=key, text=text.strip(),
            data={"part": part, "manufacturer": manufacturer, "parameters": parameters},
            domain=domain, tags=tags or ["component"],
            source=source, confidence=confidence if source else 0.5,
        )
        return self._put(rec)

    def remember_fact(
        self, name: str, value: Any, *, unit: str = "", domain: str = "",
        note: str = "", source: str = "", confidence: float = 0.7,
    ) -> MemoryRecord:
        key = f"fact:{domain}:{name.strip().lower()}"
        rec = MemoryRecord(
            kind="fact", key=key,
            text=f"{name} = {value}{unit} {note}".strip(),
            data={"name": name, "value": value, "unit": unit, "note": note},
            domain=domain, tags=["constant"], source=source,
            confidence=confidence if source else min(confidence, 0.5),
        )
        return self._put(rec)

    def remember_decision(
        self, topic: str, choice: str, *, rationale: str = "", domain: str = "",
        tags: list[str] | None = None, source: str = "", confidence: float = 0.8,
    ) -> MemoryRecord:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
        key = f"dec:{domain}:{slug}"
        rec = MemoryRecord(
            kind="decision", key=key,
            text=f"{topic}: chose {choice}. Rationale: {rationale}",
            data={"topic": topic, "choice": choice, "rationale": rationale},
            domain=domain, tags=tags or ["decision"], source=source,
            confidence=confidence,
        )
        return self._put(rec)

    def remember_lesson(self, lesson: str, *, domain: str = "", source: str = "",
                        tags: list[str] | None = None) -> MemoryRecord:
        slug = re.sub(r"[^a-z0-9]+", "-", lesson.lower()).strip("-")[:40]
        key = f"lesson:{domain}:{slug}"
        rec = MemoryRecord(
            kind="lesson", key=key, text=lesson,
            data={"lesson": lesson}, domain=domain,
            tags=tags or ["lesson"], source=source, confidence=0.75,
        )
        return self._put(rec)

    def ingest_simulation(self, result, *, part: str = "") -> MemoryRecord | None:
        """Persist a SimulationResult's verified metrics as facts.

        Only converged/ok results are remembered — a failed simulation writes
        nothing as fact (it may be kept as a lesson by the caller).
        """
        if not getattr(result, "ok", False):
            return None
        domain = result.flavor
        for name, m in result.metrics.items():
            val = m.get("value")
            if isinstance(val, str):
                continue
            self.remember_fact(
                f"{part + ':' if part else ''}{name}", val,
                unit=m.get("unit", ""), domain=domain,
                note=m.get("note", ""), source=result.source, confidence=0.85,
            )
        rec = self.remember_lesson(
            f"{result.flavor.upper()} run for {part or result.source} converged "
            f"({result.summary})",
            domain=domain, source=result.source, tags=["simulation", "verified"],
        )
        return rec

    # ── recall ──────────────────────────────────────────────────────────

    def all(self) -> list[MemoryRecord]:
        return [self._records[k] for k in self._order]

    def components(self) -> list[MemoryRecord]:
        return [r for r in self.all() if r.kind == "datasheet"]

    def get(self, key: str) -> MemoryRecord | None:
        if key in self._records:
            return self._records[key]
        # allow lookup by part name
        return self._records.get(f"ds:{key.strip().lower()}")

    def search(self, query: str, k: int = 5) -> list[MemoryRecord]:
        """Token-overlap ranking with recency and confidence boosts."""
        q = _tokenize(query)
        if not q:
            return []
        now_t = time.time()
        scored: list[tuple[float, MemoryRecord]] = []
        for rec in self.all():
            toks = _tokenize(rec.haystack())
            if not toks:
                continue
            overlap = len(q & toks)
            if overlap == 0:
                continue
            score = overlap / len(q)
            score += 0.15 * rec.confidence
            score += 0.02 * min(rec.revisions, 5)          # validated repeatedly
            score += 0.05 * max(0.0, 1 - (now_t - rec.updated_at) / 86400 / 90)
            scored.append((score, rec))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [r for _, r in scored[:k]]

    def context_for(self, query: str, k: int = 5) -> str:
        """Formatted knowledge block to inject into a prompt, or '' if none."""
        hits = self.search(query, k=k)
        if not hits:
            return ""
        lines = ["Relevant engineering memory (verify against current datasheets):"]
        for r in hits:
            src = f" [source: {r.source}]" if r.source else " [source: unverified]"
            if r.kind == "datasheet":
                p = r.data.get("parameters", {})
                params = ", ".join(f"{n}={v.get('value')}{v.get('unit', '')}"
                                   for n, v in list(p.items())[:8])
                lines.append(f"- {r.data.get('manufacturer','')} {r.data.get('part','')}: "
                             f"{params}{src}")
            else:
                lines.append(f"- ({r.kind}{'/'+r.domain if r.domain else ''}) {r.text}{src}")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for r in self.all():
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        return {"records": len(self._records), "by_kind": by_kind, "path": str(self.path)}
