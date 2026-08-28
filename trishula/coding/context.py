"""Context engine: build the right model context for a task.

Given a task description and a workspace, produce a bounded context bundle:

1. extract keywords from the task (identifiers, quoted strings, file paths);
2. score every file with a blend of — keyword hits in content, symbol-name
   hits (from RepoMap), filename/path hits, recency, and the repo-map's
   authority score;
3. pick the top files within the token budget (≈4 chars/token);
4. return the repo-map skeleton + whole small files + line-windows around
   hits in large files.

The same output feeds a real model *and* the deterministic stub loop, so
"what to look at" is never model-dependent.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from trishula.coding.repomap import RepoMap
from trishula.core.config import TrishulaConfig
from trishula.core.logging import get_logger
from trishula.tools.workspace import Workspace

log = get_logger("coding.context")

_TOKEN_CHARS = 4.0  # rough chars-per-token for budget math
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_QUOTED = re.compile(r"[\"'`]([^\"'`]{2,})[\"'`]")
_PATH = re.compile(r"[\w./-]+\.(?:py|js|ts|tsx|jsx|md|json|ya?ml|toml|sh|txt|html|css|go|rs|rb|java|c|h|cpp)")

_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "should", "file",
    "code", "make", "add", "fix", "use", "when", "where", "which", "will",
    "have", "has", "not", "but", "are", "was", "can", "all", "any", "into",
    "true", "false", "none", "null", "test", "tests", "implement", "create",
    "function", "class", "method", "error", "value", "return", "string",
}


@dataclass
class ContextBundle:
    repo_map: str
    files: list["IncludedFile"] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    estimated_tokens: int = 0

    def render(self) -> str:
        parts = ["# Repository map", self.repo_map or "(no symbols found)"]
        for f in self.files:
            parts.append(f"\n# {f.path}\n```\n{f.content}\n```")
        return "\n".join(parts)


@dataclass
class IncludedFile:
    path: str
    content: str
    score: float
    windowed: bool = False


class ContextEngine:
    def __init__(self, workspace: Workspace, *, config: TrishulaConfig | None = None):
        self.ws = workspace
        self.cfg = config or TrishulaConfig()
        self.repomap = RepoMap(workspace, config=self.cfg)

    def keywords_for(self, task: str) -> list[str]:
        kws: list[str] = []
        for m in _QUOTED.finditer(task):
            kws.append(m.group(1))
        for m in _PATH.finditer(task):
            kws.append(m.group(0))
        for m in _IDENT.finditer(task):
            w = m.group(0)
            if w.lower() not in _STOP and w not in kws:
                kws.append(w)
        return kws[:20]

    def build_context(self, task: str, *, budget_tokens: int | None = None) -> ContextBundle:
        budget = budget_tokens or self.cfg.context_token_budget
        keywords = self.keywords_for(task)
        repo_maps = self.repomap.build()

        scores = self._score_files(keywords, repo_maps)
        repo_map_text = self.repomap.render(
            max_chars=2000, focus=[p for p, _ in scores[:5]]
        )
        used_chars = len(repo_map_text)
        budget_chars = int(budget * _TOKEN_CHARS)

        bundle = ContextBundle(repo_map=repo_map_text, keywords=keywords)
        for rel, score in scores:
            if used_chars >= budget_chars:
                break
            try:
                text = self.ws.read(rel)
            except OSError:
                continue
            windowed = False
            if len(text) > 6000:
                text = self._window(text, keywords)
                windowed = True
            if used_chars + len(text) > budget_chars:
                room = budget_chars - used_chars
                if room < 800:
                    break
                text = text[:room] + "\n...[truncated to fit context budget]"
                windowed = True
            bundle.files.append(IncludedFile(rel, text, round(score, 4), windowed))
            used_chars += len(text)
        bundle.estimated_tokens = int(used_chars / _TOKEN_CHARS)
        log.info(
            "context for %r: %d keywords, %d files, ~%d tokens",
            task[:60], len(keywords), len(bundle.files), bundle.estimated_tokens,
        )
        return bundle

    # ── scoring ─────────────────────────────────────────────────────────

    def _score_files(self, keywords: list[str], repo_maps: dict) -> list[tuple[str, float]]:
        files = self.ws.walk_files(max_files=self.cfg.repomap_max_files * 5)
        scored: list[tuple[str, float]] = []
        idents = [k for k in keywords if re.match(r"^[A-Za-z_][\w]*$", k)]
        paths_kw = [k for k in keywords if "/" in k or "." in k]
        for f in files:
            rel = self.ws.rel(f)
            score = 0.0
            low_rel = rel.lower()
            # path/filename keyword hits are extremely strong signals
            for pk in paths_kw:
                base = Path(pk).name.lower()
                if base and base in low_rel:
                    score += 8.0
            for ident in idents:
                if ident.lower() in low_rel:
                    score += 4.0
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low_text = text.lower()
            hit_count = 0
            for ident in idents:
                c = low_text.count(ident.lower())
                if c:
                    hit_count += min(c, 20)
                    score += 1.5 + math.log1p(c)
                # symbol defs weigh more
                if f.suffix.lower() in {".py", ".js", ".ts", ".tsx"} and re.search(
                    rf"(def|class|function|interface)\s+{re.escape(ident)}\b", text
                ):
                    score += 5.0
            # tests mentioning the feature score moderately (they document intent)
            if "test" in low_rel and hit_count:
                score += 1.0
            # repo-map authority
            fm = repo_maps.get(rel)
            if fm:
                score += 2.0 * fm.score
            if score > 0:
                scored.append((rel, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    @staticmethod
    def _window(text: str, keywords: list[str], radius: int = 12) -> str:
        lines = text.splitlines()
        wanted: set[int] = set()
        for i, line in enumerate(lines):
            low = line.lower()
            if any(k.lower() in low for k in keywords):
                for j in range(max(0, i - radius), min(len(lines), i + radius + 1)):
                    wanted.add(j)
        if not wanted:
            return "\n".join(lines[:150])
        out: list[str] = []
        last = -2
        for i in sorted(wanted):
            if i != last + 1:
                out.append(f"...[elided, resumes at line {i + 1}]...")
            out.append(f"{i + 1:>5}: {lines[i]}")
            last = i
        return "\n".join(out)
