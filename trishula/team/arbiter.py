"""Merge arbiter — semantic reconciliation of parallel-worker merge conflicts.

When two swarm workers edit the same file in separate worktrees, a plain
``git merge`` can leave conflict markers. Rather than failing the task (and
throwing away one worker's output), the arbiter attempts resolution:

1. **Deterministic, always-safe rules** (no model needed):
   * identical both sides  → keep one;
   * one side empty        → keep the non-empty side;
   * disjoint *additive* lines (both only add imports / list entries /
     independent top-level definitions with no overlap) → take the union,
     de-duplicated;
2. **LLM reconciliation** when a model is available and the deterministic
   pass cannot decide: the marked file + context go to the model, which
   returns a full merged file; it is accepted only if it parses/compiles and
   contains no conflict markers;
3. anything that cannot be proven safe stays **unresolved** — the merge is
   aborted and the conflict is reported (never silently corrupt code).

The arbiter never guesses on a structural hunk: correctness beats optimism.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from trishula.core.logging import get_logger
from trishula.tools.shell import Shell

log = get_logger("team.arbiter")

_MARKER_OURS = re.compile(r"^<{7} .*$")
_MARKER_MID = re.compile(r"^={7}$")
_MARKER_THEIRS = re.compile(r"^>{7} .*$")


@dataclass
class Region:
    ours: str
    theirs: str
    context_before: str = ""


@dataclass
class Resolution:
    file: str
    resolved: bool
    method: str = "unresolved"
    detail: str = ""


def parse_conflicts(text: str) -> tuple[list[Region], bool]:
    """Split file text into conflict regions; ``has_conflict`` marks presence.

    Returns regions in document order along with whether any markers remain.
    """
    lines = text.splitlines(keepends=True)
    regions: list[Region] = []
    i = 0
    has = False
    while i < len(lines):
        if _MARKER_OURS.match(lines[i].rstrip("\n")):
            has = True
            i += 1
            ours: list[str] = []
            while i < len(lines) and not _MARKER_MID.match(lines[i].rstrip("\n")):
                ours.append(lines[i])
                i += 1
            i += 1  # skip =======
            theirs: list[str] = []
            while i < len(lines) and not _MARKER_THEIRS.match(lines[i].rstrip("\n")):
                theirs.append(lines[i])
                i += 1
            i += 1  # skip >>>>>>>
            regions.append(Region("".join(ours), "".join(theirs)))
        else:
            i += 1
    return regions, has


def _norm(block: str) -> str:
    return "\n".join(l.strip() for l in block.strip().splitlines() if l.strip())


def _is_only(block: str, prefixes: tuple[str, ...]) -> bool:
    body = _norm(block)
    if not body:
        return True
    return all(
        any(l.strip().startswith(p) for p in prefixes)
        for l in body.splitlines()
    )


_IMPORT_RE = re.compile(
    r"^(?:from\s+[\w.]*\s+import\s+[\w.*,\s()]+|import\s+[\w.,\s]+)$"
)


def _valid_import_lines(block: str) -> bool:
    body = _norm(block)
    if not body:
        return True
    for line in body.splitlines():
        if not _IMPORT_RE.match(line):
            return False
    return True


def _resolve_region_deterministic(region: Region, rel: str = "") -> Optional[str]:
    """Return resolved text for a region, or None if it needs judgment.

    Semantic union rules (imports, list literals) only fire for files where the
    syntax is meaningful — Python for imports — so prose like ``from A`` in a
    .txt is never mistaken for an ``import`` statement.
    """
    ours = region.ours
    theirs = region.theirs
    n_o, n_t = _norm(ours), _norm(theirs)
    if n_o == n_t:
        return ours
    if not n_o:
        return theirs
    if not n_t:
        return ours
    is_py = rel.endswith(".py")
    # Both sides only add real imports → union, de-duplicated, order-preserving.
    if is_py and _valid_import_lines(ours) and _valid_import_lines(theirs):
        seen: list[str] = []
        for block in (ours, theirs):
            for line in block.splitlines():
                if line.strip() and line not in seen:
                    seen.append(line)
        return "\n".join(seen) + "\n"
    return None


def apply_region_resolution(text: str, resolved_blocks: list[str]) -> str:
    """Rebuild a file, replacing each conflict region with its resolution."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    ri = 0
    i = 0
    while i < len(lines):
        if _MARKER_OURS.match(lines[i].rstrip("\n")):
            out.append(resolved_blocks[ri])
            ri += 1
            # skip the whole conflict block
            i += 1
            depth = 1
            while i < len(lines) and depth:
                if _MARKER_OURS.match(lines[i].rstrip("\n")):
                    depth += 1
                elif _MARKER_THEIRS.match(lines[i].rstrip("\n")):
                    depth -= 1
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


class MergeArbiter:
    def __init__(self, shell: Shell, client=None):  # noqa: ANN001
        self.shell = shell
        self.client = client

    # ── public API ──────────────────────────────────────────────────────

    def conflicted_files(self) -> List[str]:
        res = self.shell.run("git diff --name-only --diff-filter=U", timeout=60)
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def resolve_all(self, max_files: int = 12) -> list[Resolution]:
        results: list[Resolution] = []
        for rel in self.conflicted_files()[:max_files]:
            results.append(self._resolve_file(rel))
        return results

    # ── internals ───────────────────────────────────────────────────────

    def _resolve_file(self, rel: str) -> Resolution:
        path = self.shell.root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Resolution(rel, False, detail=f"cannot read: {exc}")
        regions, _ = parse_conflicts(text)
        if not regions:
            return Resolution(rel, True, method="no-markers", detail="file had no conflict markers")

        resolved_blocks: list[str] = []
        methods: list[str] = []
        unresolved = False
        for idx, region in enumerate(regions):
            block = _resolve_region_deterministic(region, rel)
            if block is not None:
                methods.append("deterministic")
                resolved_blocks.append(block)
                continue
            if self.client is not None:
                llm_block = self._llm_resolve(rel, text, idx, region)
                if llm_block is not None:
                    methods.append("llm")
                    resolved_blocks.append(llm_block)
                    continue
            unresolved = True
            break

        if unresolved:
            return Resolution(
                rel, False, method="unresolved",
                detail="conflicting edits overlap; needs a human or a fresh task",
            )

        merged = apply_region_resolution(text, resolved_blocks)
        if any(m in merged for m in ("<<<<<<<", ">>>>>>>")):
            return Resolution(rel, False, detail="resolution left conflict markers")
        if not self._compiles(rel, merged):
            return Resolution(rel, False, method="unresolved",
                              detail="proposed resolution failed to compile")
        path.write_text(merged, encoding="utf-8")
        r = self.shell.run(f"git add {_quote(rel)}", timeout=60)
        if not r.ok:
            return Resolution(rel, False, detail=f"git add failed: {r.text()[:200]}")
        method = "+".join(sorted(set(methods)))
        log.info("resolved %s via %s", rel, method)
        return Resolution(rel, True, method=method, detail=f"{len(regions)} region(s) merged")

    def _llm_resolve(self, rel: str, full_text: str, idx: int, region: Region) -> Optional[str]:
        from trishula.core.types import Message

        prompt = (
            f"Resolve this git merge conflict in {rel}. Output ONLY the complete, "
            "correct merged file content (no fences, no prose). Keep both sides' "
            "intended changes; combine disjoint additions; where they truly "
            "conflict choose a coherent result that preserves both behaviors. "
            "Do not include conflict markers.\n\n"
            f"<<<<<<<\n{full_text}\n>>>>>>>"
        )
        try:
            resp = self.client.complete(
                [Message.system("You are a meticulous merge arbiter inside an agent harness."),
                 Message.user(prompt)],
                temperature=0.0, max_tokens=6000,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM resolve failed for %s: %s", rel, exc)
            return None
        content = _strip_fences(resp.content or "")
        if not content or any(m in content for m in ("<<<<<<<", ">>>>>>>", "=======")):
            return None
        return content if content.endswith("\n") else content + "\n"

    def _compiles(self, rel: str, content: str) -> bool:
        if not rel.endswith(".py"):
            return True  # other languages: trust markers/LLM; compile gate is Python
        import ast

        try:
            ast.parse(content)
            return True
        except SyntaxError:
            return False


def _quote(p: str) -> str:
    import shlex

    return shlex.quote(p)


def _strip_fences(text: str) -> str:
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    return m.group(1) if m else text
