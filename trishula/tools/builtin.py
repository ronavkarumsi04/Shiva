"""Factory wiring the canonical coding toolset onto a registry.

This is the toolbelt every agentic loop shares — the same primitives Claude
Code and Codex converge on, with Trishula's sandbox semantics:

    read_file        — line-windowed reader (cheap, context-aware)
    list_dir         — one-level directory listing
    write_file       — whole-file create/overwrite (atomic replace)
    str_replace      — exact-match edit, unique by default, journaled
    insert_at_line   — line-number insertion
    undo_edit        — revert last edit to a file
    search_code      — ripgrep-style recursive grep with regex support
    glob             — filename pattern search
    run_shell        — sandboxed shell (see tools.shell)
    todo / note      — lightweight task scratchpad for long runs

The coding loop then adds ``make_plan`` / ``run_task_step`` / ``finish`` on
top; the team swarm adds delegation tools. Building them in layers keeps the
unit-tested toolbelt reusable from a plain Python REPL.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from trishula.core.config import TrishulaConfig
from trishula.core.types import Journal, ToolResult
from trishula.tools.registry import ToolRegistry
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace

_READ_ONLY = ("read", "search", "list", "glob")


def build_registry(
    workspace: Workspace,
    shell: Shell | None = None,
    *,
    config: TrishulaConfig | None = None,
    journal: Journal | None = None,
) -> ToolRegistry:
    cfg = config or TrishulaConfig()
    shell = shell or Shell(
        workspace.root,
        timeout=cfg.shell_timeout_default,
        timeout_max=cfg.shell_timeout_max,
        output_cap=cfg.shell_output_cap,
        allow_network=cfg.shell_allow_network,
        deny_commands=cfg.shell_deny_commands,
        journal=journal,
    )
    reg = ToolRegistry(journal=journal)

    # ── filesystem reads ────────────────────────────────────────────────

    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> ToolResult:
        try:
            end = end_line or (start_line + 199)
            text = workspace.read_lines(path, max(1, start_line), end)
            return ToolResult(True, output=text or "[empty file]")
        except FileNotFoundError:
            return ToolResult(False, error=f"file not found: {path}")
        except IsADirectoryError:
            return ToolResult(False, error=f"{path} is a directory; use list_dir")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")

    reg.register(
        "read_file",
        "Read a UTF-8 text file from the workspace with line numbers. "
        "Reads up to 200 lines per call; page with start_line/end_line.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path"},
                "start_line": {"type": "integer", "description": "First line (1-indexed)", "default": 1},
                "end_line": {"type": "integer", "description": "Last line inclusive; default start+199", "default": 0},
            },
            "required": ["path"],
        },
        read_file,
        tags=("read",),
        read_only=True,
    )

    def list_dir(path: str = ".") -> ToolResult:
        try:
            entries = workspace.list_dir(path)
            return ToolResult(True, output="\n".join(entries) or "[empty]")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")

    reg.register(
        "list_dir",
        "List the contents of a directory in the workspace (one level).",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path", "default": "."}},
        },
        list_dir,
        tags=("read",),
        read_only=True,
    )

    # ── filesystem writes ───────────────────────────────────────────────

    def write_file(path: str, content: str) -> ToolResult:
        try:
            workspace.write(path, content)
            lines = content.count("\n") + 1
            return ToolResult(True, output=f"wrote {workspace.rel(workspace.resolve(path))} ({lines} lines)")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")

    reg.register(
        "write_file",
        "Create or overwrite a file with exact content. Prefer str_replace "
        "for small edits to existing files.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path"},
                "content": {"type": "string", "description": "Full new file content"},
            },
            "required": ["path", "content"],
        },
        write_file,
        tags=("write",),
    )

    # Edit tools are attached by the coding layer (EditEngine owns history);
    # imported lazily to keep tools -> coding dependency one-directional.
    from trishula.coding.edits import attach_edit_tools

    attach_edit_tools(reg, workspace, config=cfg, journal=journal)

    # ── search ──────────────────────────────────────────────────────────

    def search_code(
        query: str,
        glob_pattern: str = "*",
        max_results: int = 100,
    ) -> ToolResult:
        try:
            pattern = re.compile(query)
        except re.error as exc:
            return ToolResult(False, error=f"invalid regex {query!r}: {exc}")
        hits: list[str] = []
        for f in workspace.walk_files(max_files=cfg.repomap_max_files * 5):
            if not fnmatch.fnmatch(f.name, glob_pattern) and glob_pattern != "*":
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{workspace.rel(f)}:{i}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        return ToolResult(True, output="\n".join(hits), data={"truncated": True})
        return ToolResult(True, output="\n".join(hits) or "no matches", data={"count": len(hits)})

    reg.register(
        "search_code",
        "Recursively search file contents with a regular expression. "
        "Returns file:line: text matches. Respects .git-style skip dirs.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Regular expression to search for"},
                "glob_pattern": {"type": "string", "description": "Optional filename glob, e.g. '*.py'", "default": "*"},
                "max_results": {"type": "integer", "description": "Max matches to return", "default": 100},
            },
            "required": ["query"],
        },
        search_code,
        tags=("read", "search"),
        read_only=True,
    )

    def glob(pattern: str) -> ToolResult:
        matches = [
            workspace.rel(f)
            for f in workspace.walk_files(max_files=cfg.repomap_max_files * 5)
            if fnmatch.fnmatch(workspace.rel(f), pattern) or fnmatch.fnmatch(f.name, pattern)
        ]
        return ToolResult(True, output="\n".join(matches[:500]) or "no matches", data={"count": len(matches)})

    reg.register(
        "glob",
        "Find files by glob pattern (e.g. '**/*.py', 'test_*.py').",
        {
            "type": "object",
            "properties": {"pattern": {"type": "string", "description": "Glob pattern"}},
            "required": ["pattern"],
        },
        glob,
        tags=("read", "search"),
        read_only=True,
    )

    # ── shell ───────────────────────────────────────────────────────────

    def run_shell(command: str, timeout: int = 0) -> ToolResult:
        result = shell.run(command, timeout=timeout or None)
        return ToolResult(
            ok=result.ok,
            output=result.text(),
            error="" if result.ok else result.stderr or "command failed",
            data={"exit_code": result.exit_code, "timed_out": result.timed_out, "denied": result.denied},
        )

    reg.register(
        "run_shell",
        "Run a shell command inside the sandboxed workspace. Network denied "
        "by default; output capped; destructive commands refused. Use for "
        "builds, tests, git, package management.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command line"},
                "timeout": {"type": "integer", "description": "Seconds (default 30, max 600)", "default": 0},
            },
            "required": ["command"],
        },
        run_shell,
        tags=("shell", "dangerous"),
        dangerous=True,
    )

    # ── scratchpad ──────────────────────────────────────────────────────

    scratch: dict[str, list[str]] = {"items": []}

    def todo(action: str, text: str = "") -> ToolResult:
        items = scratch["items"]
        if action == "add":
            items.append(f"[ ] {text}")
        elif action == "done":
            for i, it in enumerate(items):
                if text and text in it:
                    items[i] = it.replace("[ ]", "[x]")
        elif action == "list":
            return ToolResult(True, output="\n".join(items) or "[no todos]")
        elif action == "clear":
            items.clear()
        else:
            return ToolResult(False, error=f"unknown action {action!r}; use add|done|list|clear")
        return ToolResult(True, output="\n".join(items))

    reg.register(
        "todo",
        "Maintain a short task checklist for multi-step work: add, mark done, list, clear.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "add|done|list|clear"},
                "text": {"type": "string", "description": "Task text (for add/done)", "default": ""},
            },
            "required": ["action"],
        },
        todo,
        tags=("plan",),
    )

    return reg
