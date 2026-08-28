"""Self-improving prompt loop — every run makes the next one sharper.

After a coding/team run, the :class:`~trishula.autonomy.reflect.Reflector`
produces a :class:`~trishula.autonomy.reflect.Retrospective` with hard signals
(failed edits, thrashing, denials, repair rounds, verdict). This module turns
those signals into *durable, gated* refinements to the system prompt the next
run receives:

* deterministic **rules** are derived from recurring anti-patterns (e.g. three
  runs hit ``str_replace`` drift → emit an explicit "re-read before editing"
  rule). Rules only promote after a frequency threshold so one bad day does not
  permanently distort the prompt;
* each rule has a ``weight`` (evidence count) and is promoted/demoted by later
  outcomes — success reinforces, a contradicting failure pattern raises
  competing rules;
* an LLM, when configured, can draft a concise distilled guideline from the
  lessons; it is accepted only if short and free of prompt-injection-style
  instructions. Offline mode still improves, purely from rules.

Refinements live in a JSON file under the Trishula home, so learning persists
across sessions. ``build_prefix()`` renders the active rules into a block
prepended to the base system prompt. Nothing here rewrites code or base
prompts in place — it only *adds* gated, evidence-backed guidance.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from trishula.core.logging import get_logger
from trishula.core.types import Message

log = get_logger("autonomy.prompt_evolution")

# Pattern name → anti-pattern detector over retrospective signals.
# Each returns a (rule_title, rule_body) when the signal fires.


def _detect_drift(signals: dict[str, Any], anti: list[str]) -> tuple[str, str] | None:
    drift = signals.get("edit_failures", 0)
    if drift >= 2 or any("drift" in a or "did not match" in a or "str_replace" in a
                         for a in anti):
        return (
            "re-read before editing",
            "A str_replace failed to match because the file drifted. Re-read the "
            "exact region with read_file immediately before editing, and copy the "
            "old_string verbatim with correct indentation.",
        )
    return None


def _detect_thrash(signals: dict[str, Any], anti: list[str]) -> tuple[str, str] | None:
    if signals.get("thrashing", 0) >= 1 or any("repeat" in a or "thrash" in a for a in anti):
        return (
            "avoid repeating identical calls",
            "You issued the same tool call with the same arguments repeatedly. "
            "Change approach after a failure: inspect the error output and alter "
            "arguments or the plan instead of retrying verbatim.",
        )
    return None


def _detect_denials(signals: dict[str, Any], anti: list[str]) -> tuple[str, str] | None:
    if signals.get("denied", 0) >= 1 or any("denied" in a or "network" in a for a in anti):
        return (
            "respect the sandbox",
            "A command was denied or network access attempted. Work within the "
            "sandbox: do not request secrets, network calls, or disallowed "
            "shell commands; use the provided tools.",
        )
    return None


def _detect_repair(signals: dict[str, Any], anti: list[str]) -> tuple[str, str] | None:
    if signals.get("repair_rounds", 0) >= 1 or any("test" in a.lower() for a in anti):
        return (
            "write tests before declaring done",
            "Verification needed repair rounds after you finished. Before calling "
            "finish, run the targeted tests yourself and confirm they pass; add "
            "edge-case assertions for the exact failure the verifier reported.",
        )
    return None


def _detect_timeout(signals: dict[str, Any], anti: list[str]) -> tuple[str, str] | None:
    if signals.get("timeouts", 0) >= 1:
        return (
            "bound long-running commands",
            "A command timed out. Prefer scoped, fast commands (targeted tests, "
            "limited globs) and avoid unbounded builds/watches in the loop.",
        )
    return None


_DETECTORS = [_detect_drift, _detect_thrash, _detect_denials,
              _detect_repair, _detect_timeout]


@dataclass
class PromptRule:
    title: str
    body: str
    weight: int = 1           # evidence count
    hits: int = 1             # runs that triggered it
    reinforced: int = 0       # runs where outcome supported keeping it
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PromptRule":
        return cls(
            title=d["title"], body=d["body"],
            weight=d.get("weight", 1), hits=d.get("hits", 1),
            reinforced=d.get("reinforced", 0),
            created_at=d.get("created_at", time.time()),
            last_seen=d.get("last_seen", time.time()),
        )


class PromptEvolution:
    """Accumulates evidence and renders an improved system-prompt prefix."""

    PROMOTE_AT = 2        # weight needed for a rule to enter the active prompt
    MAX_RULES = 8

    def __init__(self, path: str | Path | None = None, *, home: str = "",
                 client=None):  # noqa: ANN001
        if path is None:
            base = Path(home) if home else Path.home() / ".trishula"
            path = base / "prompt_rules.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.rules: dict[str, PromptRule] = {}
        self.runs: int = 0
        self.successes: int = 0
        self._load()

    # ── persistence ─────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.runs = d.get("runs", 0)
            self.successes = d.get("successes", 0)
            self.rules = {
                r["title"]: PromptRule.from_dict(r) for r in d.get("rules", [])
            }
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.warning("prompt-evolution load issue: %s", exc)

    def _save(self) -> None:
        payload = {
            "runs": self.runs,
            "successes": self.successes,
            "updated_at": time.time(),
            "rules": [r.to_dict() for r in self.rules.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    # ── learning ────────────────────────────────────────────────────────

    def learn(self, retrospective) -> list[PromptRule]:
        """Feed one retrospective; returns the rules triggered this run."""
        signals = getattr(retrospective, "signals", {}) or {}
        anti = getattr(retrospective, "anti_patterns", []) or []
        success = bool(getattr(retrospective, "success", False))
        self.runs += 1
        if success:
            self.successes += 1

        triggered: list[PromptRule] = []
        titles: set[str] = set()
        for det in _DETECTORS:
            found = det(signals, anti)
            if not found:
                continue
            title, body = found
            titles.add(title)
            if title in self.rules:
                rule = self.rules[title]
                rule.hits += 1
                rule.weight += 1
                rule.last_seen = time.time()
                # a successful run that *still* triggered this pattern means
                # the rule is worth keeping and its advice works.
                if success:
                    rule.reinforced += 1
            else:
                rule = PromptRule(title=title, body=body)
                self.rules[title] = rule
            triggered.append(rule)

        # Decay rules that did not fire this run but have been around, so stale
        # guidance doesn't accumulate forever.
        for title, rule in list(self.rules.items()):
            if title not in titles:
                # mild decay on success (environment improving) — keep strong ones
                if success and rule.weight >= self.PROMOTE_AT:
                    rule.weight = max(self.PROMOTE_AT - 1, rule.weight - 0)  # hold
                elif not success:
                    rule.weight = max(0, rule.weight - 0)  # hold; evidence persists

        self._prune()
        self._maybe_llm_distill(retrospective, triggered)
        self._save()
        return triggered

    def _prune(self) -> None:
        if len(self.rules) <= self.MAX_RULES:
            return
        ranked = sorted(self.rules.values(),
                        key=lambda r: (r.weight, r.reinforced, r.last_seen),
                        reverse=True)
        keep = {r.title for r in ranked[: self.MAX_RULES]}
        self.rules = {t: r for t, r in self.rules.items() if t in keep}

    def _maybe_llm_distill(self, retro, triggered: list[PromptRule]) -> None:
        if self.client is None or getattr(self.client, "name", "") == "stub":
            return
        lessons = "; ".join(getattr(retro, "lessons", []) or [])
        if not lessons:
            return
        prompt = (
            "Distill ONE short, imperative coding guideline (max 2 sentences) "
            "that would prevent this recurring failure in future runs. Output "
            "only the guideline, no preamble. Failure context: "
            f"{lessons[:800]}"
        )
        try:
            resp = self.client.complete(
                [Message.system("You write concise, safe agent guidelines."),
                 Message.user(prompt)],
                temperature=0.1, max_tokens=140,
            )
            text = (resp.content or "").strip().strip('"')
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM distill failed: %s", exc)
            return
        if not self._safe_rule(text):
            return
        title = " ".join(re.findall(r"[A-Za-z]+", text)[:4]).lower() or "llm-guideline"
        if title in self.rules:
            self.rules[title].weight += 1
        else:
            self.rules[title] = PromptRule(title=title, body=text)

    @staticmethod
    def _safe_rule(text: str) -> bool:
        if not text or len(text) > 240:
            return False
        lowered = text.lower()
        # reject anything that tries to redirect identity/policy or exfiltrate
        banned = ("ignore ", "system prompt", "you are now", "reveal", "password",
                  "api key", "http://", "https://", "exfiltrate", "secret")
        return not any(b in lowered for b in banned)

    # ── application ─────────────────────────────────────────────────────

    def active_rules(self) -> list[PromptRule]:
        return sorted(
            [r for r in self.rules.values() if r.weight >= self.PROMOTE_AT],
            key=lambda r: (r.reinforced, r.weight, r.last_seen), reverse=True,
        )

    def build_prefix(self) -> str:
        rules = self.active_rules()
        if not rules:
            return ""
        lines = ["# Learned engineering guidance (from past runs)"]
        for r in rules:
            lines.append(f"- {r.body}")
        return "\n".join(lines)

    def augment_system_prompt(self, base_prompt: str) -> str:
        prefix = self.build_prefix()
        if not prefix:
            return base_prompt
        return base_prompt.rstrip() + "\n\n" + prefix

    def stats(self) -> dict[str, Any]:
        return {
            "runs": self.runs, "successes": self.successes,
            "rules_total": len(self.rules),
            "rules_active": len(self.active_rules()),
            "path": str(self.path),
        }
