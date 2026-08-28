"""Edit engine: exact-match string replacement with undo history.

Semantics deliberately match what makes Claude Code edits reliable:

* ``str_replace`` requires ``old_string`` to occur **exactly once** in the
  file (whitespace shown verbatim — the agent must copy real indentation).
  Zero matches => error with a "closest region" hint; >1 matches => error
  listing the match lines so the agent can add disambiguating context.
* Every successful edit pushes the prior content onto a per-file stack so
  ``undo_edit`` reverts cleanly.
* ``insert_at_line`` inserts before a 1-indexed line number.
* Edits are journaled (``EDIT_APPLIED`` / ``EDIT_FAILED``) — the reflector
  later correlates edit churn with outcomes.

Edits are applied through the :class:`Workspace`, so confinement is
inherited; nothing here writes outside the sandbox.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trishula.core.config import TrishulaConfig
from trishula.core.errors import EditError
from trishula.core.logging import get_logger
from trishula.core.types import Journal, EventKind, ToolResult
from trishula.tools.registry import ToolRegistry
from trishula.tools.workspace import Workspace

log = get_logger("coding.edits")


@dataclass
class Edit:
    path: str
    old_string: str
    new_string: str
    at_line: int = 0          # for insert edits
    kind: str = "replace"     # "replace" | "insert" | "create"
    applied: bool = False
    diff: str = ""


class EditEngine:
    def __init__(
        self,
        workspace: Workspace,
        *,
        config: TrishulaConfig | None = None,
        journal: Journal | None = None,
    ):
        self.ws = workspace
        self.cfg = config or TrishulaConfig()
        self.journal = journal
        self._history: dict[str, list[str]] = {}
        self.edits: list[Edit] = []

    # ── primitives ──────────────────────────────────────────────────────

    def str_replace(self, path: str, old_string: str, new_string: str) -> Edit:
        p = self.ws.resolve(path)
        rel = self.ws.rel(p)
        if not p.exists():
            edit = Edit(rel, old_string, new_string, kind="replace")
            self._fail(edit, f"file not found: {rel}")
            raise EditError(f"file not found: {rel}", rel, old_string)
        original = p.read_text(encoding="utf-8", errors="replace")
        count = original.count(old_string)
        if count == 0:
            edit = Edit(rel, old_string, new_string, kind="replace")
            hint = self._fuzzy_hint(original, old_string)
            self._fail(edit, f"old_string not found in {rel}.{hint}")
            raise EditError(f"old_string not found in {rel}.{hint}", rel, old_string)
        if count > 1 and self.cfg.edit_require_unique:
            lines = [
                i + 1
                for i, line in enumerate(original.splitlines())
                if old_string.splitlines()[0][:60] in line
            ]
            edit = Edit(rel, old_string, new_string, kind="replace")
            msg = f"old_string occurs {count} times in {rel} (near lines {lines[:8]}); add surrounding context"
            self._fail(edit, msg)
            raise EditError(msg, rel, old_string)

        updated = original.replace(old_string, new_string, 1)
        self._push_history(rel, original)
        p.write_text(updated, encoding="utf-8")
        diff = self._unified_diff(rel, original, updated)
        edit = Edit(rel, old_string, new_string, kind="replace", applied=True, diff=diff)
        self.edits.append(edit)
        if self.journal:
            self.journal.emit(EventKind.EDIT_APPLIED, path=rel, kind="replace", diff=diff[:2000])
        log.info("str_replace applied to %s (%d -> %d chars)", rel, len(original), len(updated))
        return edit

    def insert_at_line(self, path: str, line: int, text: str) -> Edit:
        p = self.ws.resolve(path)
        rel = self.ws.rel(p)
        if not p.exists():
            raise EditError(f"file not found: {rel}", rel)
        original = p.read_text(encoding="utf-8", errors="replace")
        lines = original.splitlines(keepends=True)
        if line < 1 or line > len(lines) + 1:
            raise EditError(f"line {line} out of range (1..{len(lines) + 1}) for {rel}", rel)
        insertion = text if text.endswith("\n") else text + "\n"
        lines.insert(line - 1, insertion)
        updated = "".join(lines)
        self._push_history(rel, original)
        p.write_text(updated, encoding="utf-8")
        diff = self._unified_diff(rel, original, updated)
        edit = Edit(rel, "", text, at_line=line, kind="insert", applied=True, diff=diff)
        self.edits.append(edit)
        if self.journal:
            self.journal.emit(EventKind.EDIT_APPLIED, path=rel, kind="insert", line=line)
        return edit

    def undo(self, path: str | None = None) -> str:
        """Revert the most recent edit (optionally for one file)."""
        target = path
        if target is not None:
            rel = self.ws.rel(self.ws.resolve(target))
            stack = self._history.get(rel, [])
            if not stack:
                raise EditError(f"no edit history for {rel}")
            content = stack.pop()
            self.ws.resolve(rel).write_text(content, encoding="utf-8")
            return rel
        # most recent across files
        for rel in sorted(self._history, key=lambda r: len(self._history[r]), reverse=True):
            stack = self._history[rel]
            if stack:
                content = stack.pop()
                self.ws.resolve(rel).write_text(content, encoding="utf-8")
                return rel
        raise EditError("edit history is empty")

    @property
    def changed_files(self) -> list[str]:
        seen: list[str] = []
        for e in self.edits:
            if e.applied and e.path not in seen:
                seen.append(e.path)
        return seen

    # ── internals ───────────────────────────────────────────────────────

    def _push_history(self, rel: str, content: str) -> None:
        self._history.setdefault(rel, []).append(content)

    def _fail(self, edit: Edit, msg: str) -> None:
        self.edits.append(edit)
        if self.journal:
            self.journal.emit(EventKind.EDIT_FAILED, path=edit.path, error=msg)
        log.info("edit failed: %s", msg)

    @staticmethod
    def _unified_diff(rel: str, before: str, after: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=2,
            )
        )

    @staticmethod
    def _fuzzy_hint(content: str, old: str) -> str:
        """Point at the closest region when an exact match fails."""
        lines = content.splitlines()
        target = old.strip().splitlines()[0] if old.strip() else ""
        if not target:
            return ""
        best = difflib.get_close_matches(target, lines, n=1, cutoff=0.5)
        if best:
            idx = lines.index(best[0]) + 1
            return f" Closest existing line is {idx}: {best[0].strip()[:120]}"
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Tool registration (called by tools.builtin.build_registry)
# ─────────────────────────────────────────────────────────────────────────────


def attach_edit_tools(
    reg: ToolRegistry,
    workspace: Workspace,
    *,
    config: TrishulaConfig | None = None,
    journal: Journal | None = None,
) -> EditEngine:
    engine = EditEngine(workspace, config=config, journal=journal)

    def str_replace(path: str, old_string: str, new_string: str) -> ToolResult:
        try:
            edit = engine.str_replace(path, old_string, new_string)
            return ToolResult(True, output=f"edited {edit.path}\n{edit.diff[:4000]}")
        except EditError as exc:
            return ToolResult(False, error=str(exc))

    reg.register(
        "str_replace",
        "Replace an exact, UNIQUE string in a file. old_string must match "
        "byte-for-byte including indentation and occur exactly once; include "
        "2-3 surrounding lines for uniqueness. On failure the closest line "
        "is reported.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to edit"},
                "old_string": {"type": "string", "description": "Exact text to replace (must be unique)"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        str_replace,
        tags=("write", "edit"),
    )

    def insert_at_line(path: str, line: int, text: str) -> ToolResult:
        try:
            edit = engine.insert_at_line(path, line, text)
            return ToolResult(True, output=f"inserted at {edit.path}:{edit.at_line}")
        except EditError as exc:
            return ToolResult(False, error=str(exc))

    reg.register(
        "insert_at_line",
        "Insert text before a 1-indexed line number.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to edit"},
                "line": {"type": "integer", "description": "Line number to insert before"},
                "text": {"type": "string", "description": "Text to insert"},
            },
            "required": ["path", "line", "text"],
        },
        insert_at_line,
        tags=("write", "edit"),
    )

    def undo_edit(path: str = "") -> ToolResult:
        try:
            rel = engine.undo(path or None)
            return ToolResult(True, output=f"reverted last edit to {rel}")
        except EditError as exc:
            return ToolResult(False, error=str(exc))

    reg.register(
        "undo_edit",
        "Revert the most recent edit (optionally scoped to one file).",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Optional file to revert", "default": ""}},
        },
        undo_edit,
        tags=("write", "edit"),
    )

    # Stash the engine where the coding loop can find it.
    reg._edit_engine = engine  # type: ignore[attr-defined]
    return engine
