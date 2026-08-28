"""Repository map: a cheap, ctags-free navigational skeleton.

Claude Code's superpower is knowing *where in a repo* to look before reading
anything. A full ctags/LSP pass is too heavy for a $5 VPS and unavailable for
half the languages anyway; instead we extract definitions with tuned regexes
for the languages that cover ~95% of real work (py, js/ts, jsx/tsx, go, rs,
rb, java, c/cpp/h, md headings, toml/yaml sections), rank files by a
recency × definition-density × reference-count score, and render only the
budget that fits.

The map is intentionally lossy: its job is navigation, not semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from trishula.core.config import TrishulaConfig
from trishula.core.logging import get_logger
from trishula.tools.workspace import Workspace

log = get_logger("coding.repomap")

# language -> (line regex, symbol group, kind label)
_DEF_RULES: dict[str, list[tuple[re.Pattern[str], str]]] = {
    ".py": [
        (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"), "def"),
        (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
    ],
    ".js": [
        (re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)"), "fn"),
        (re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("), "fn"),
        (re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"\bexports?\.([A-Za-z_$][\w$]*)\s*="), "export"),
    ],
    ".ts": [
        (re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)"), "fn"),
        (re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("), "fn"),
        (re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"\b(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)"), "type"),
    ],
    ".tsx": [], ".jsx": [],  # share .js rules by suffix fallback below
    ".go": [
        (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)"), "func"),
        (re.compile(r"^type\s+([A-Za-z_][A-Za-z0-9_]*)"), "type"),
    ],
    ".rs": [
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"), "fn"),
        (re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"), "type"),
    ],
    ".rb": [
        (re.compile(r"^\s*(?:def)\s+([A-Za-z_][A-Za-z0-9_?!]*)"), "def"),
        (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
        (re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_]*)"), "module"),
    ],
    ".java": [
        (re.compile(r"\b(?:public|private|protected|static|\s)+[\w<>\[\]]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("), "method"),
        (re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
        (re.compile(r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)"), "interface"),
    ],
    ".c": [], ".h": [], ".cpp": [], ".cc": [], ".hpp": [
        (re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{"), "fn"),
        (re.compile(r"\b(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)"), "type"),
    ],
}
for _ext in (".tsx", ".jsx"):
    _DEF_RULES[_ext] = _DEF_RULES[".js"] + _DEF_RULES[".ts"]
for _ext in (".c", ".h", ".cpp", ".cc"):
    _DEF_RULES[_ext] = _DEF_RULES[".hpp"]

_MD_RULE = re.compile(r"^(#{1,3})\s+(.+)$")
_SECTION_RULE = re.compile(r"^\[([^\]]+)\]\s*$")  # toml sections / ini


@dataclass
class Symbol:
    name: str
    kind: str
    line: int


@dataclass
class FileMap:
    path: str
    symbols: list[Symbol] = field(default_factory=list)
    score: float = 0.0
    mtime: float = 0.0
    references: int = 0


class RepoMap:
    def __init__(self, workspace: Workspace, *, config: TrishulaConfig | None = None):
        self.ws = workspace
        self.cfg = config or TrishulaConfig()

    def extract_symbols(self, path: Path) -> list[Symbol]:
        suffix = path.suffix.lower()
        symbols: list[Symbol] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return symbols
        for i, line in enumerate(text.splitlines(), 1):
            if suffix in _DEF_RULES:
                for rule, kind in _DEF_RULES[suffix]:
                    m = rule.search(line if rule.pattern.startswith("^") else line)
                    if m:
                        symbols.append(Symbol(m.group(1), kind, i))
                        break
            elif suffix == ".md":
                m = _MD_RULE.match(line)
                if m:
                    symbols.append(Symbol(m.group(2).strip()[:60], f"h{len(m.group(1))}", i))
            elif suffix in {".toml", ".ini", ".cfg"}:
                m = _SECTION_RULE.match(line)
                if m:
                    symbols.append(Symbol(m.group(1), "section", i))
        return symbols

    def build(self) -> dict[str, FileMap]:
        files = self.ws.walk_files(max_files=self.cfg.repomap_max_files)
        maps: dict[str, FileMap] = {}
        # First pass: symbols + mtimes.
        for f in files:
            syms = self.extract_symbols(f)
            if not syms:
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                mtime = 0.0
            rel = self.ws.rel(f)
            maps[rel] = FileMap(path=rel, symbols=syms, mtime=mtime)
        # Second pass: reference counts (a symbol mentioned elsewhere is hot).
        name_to_files: dict[str, set[str]] = {}
        for rel, fm in maps.items():
            for s in fm.symbols:
                name_to_files.setdefault(s.name, set()).add(rel)
        all_text: dict[str, str] = {}
        for rel in maps:
            try:
                all_text[rel] = self.ws.resolve(rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                all_text[rel] = ""
        for rel, fm in maps.items():
            refs = 0
            text = all_text.get(rel, "")
            for name, owners in name_to_files.items():
                if rel in owners and len(owners) > 1:
                    continue
                if len(name) < 4:
                    continue
                count = text.count(name)
                if count:
                    refs += count
            fm.references = refs
            fm.score = self._score(fm)
        return maps

    @staticmethod
    def _score(fm: FileMap) -> float:
        import time as _t

        age_days = max(0.0, (_t.time() - fm.mtime) / 86400.0)
        recency = 1.0 / (1.0 + age_days / 14.0)  # half-ish weight every ~2 weeks
        density = min(len(fm.symbols), 40) / 40.0
        refs = min(fm.references, 100) / 100.0
        return round(0.45 * recency + 0.25 * density + 0.30 * refs, 4)

    def render(self, *, max_chars: int = 6000, focus: list[str] | None = None) -> str:
        maps = self.build()
        focus_set = set(focus or [])
        ordered = sorted(
            maps.values(),
            key=lambda fm: (fm.path in focus_set, fm.score),
            reverse=True,
        )
        lines: list[str] = []
        used = 0
        for fm in ordered:
            header = f"{fm.path}  (score {fm.score})"
            block = [header]
            for s in fm.symbols[:25]:
                block.append(f"  {s.line:>5}  {s.kind:<7} {s.name}")
            chunk = "\n".join(block) + "\n"
            if used + len(chunk) > max_chars:
                break
            lines.append(chunk)
            used += len(chunk)
        return "\n".join(lines)

    def top_files(self, n: int = 10) -> list[str]:
        maps = self.build()
        return [fm.path for fm in sorted(maps.values(), key=lambda f: f.score, reverse=True)[:n]]
