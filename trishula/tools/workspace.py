"""Workspace: the agent's view of a filesystem, confined to a root.

Every path an agent touches is resolved and checked against the workspace
root (``resolve(strict=False)`` + ``is_relative_to``). Symlinks that point
outside the root are rejected on *write* and reported on *read*. This is the
same threat model as Claude Code's working-directory confinement and Codex's
sandbox: an agent (or a prompt-injected tool result) cannot exfiltrate or
clobber ``~/.ssh``, ``/etc``, or a sibling project.

The class also records every write so the coding loop can diff, revert, and
attribute changes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from trishula.core.errors import SandboxError
from trishula.core.logging import get_logger

log = get_logger("tools.workspace")

# Directories we never descend into when walking/reading.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox", "dist", "build",
    ".next", ".nuxt", ".turbo", ".cache", ".idea", ".vscode",
}

_MAX_READ_BYTES = 1_000_000  # 1 MiB cap on any single read


class Workspace:
    def __init__(self, root: str | Path, *, readonly: bool = False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.readonly = readonly
        self._writes: list[Path] = []
        self._deletes: list[Path] = []

    # ── path containment ────────────────────────────────────────────────

    def resolve(self, path: str | Path) -> Path:
        """Resolve ``path`` (relative => under root) and enforce containment."""
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.expanduser().resolve(strict=False)
        try:
            inside = p.is_relative_to(self.root)  # py3.9+
        except AttributeError:  # pragma: no cover
            inside = str(p).startswith(str(self.root) + os.sep) or p == self.root
        if not inside:
            raise SandboxError(
                f"path {p} is outside the workspace root {self.root}"
            )
        return p

    def rel(self, path: str | Path) -> str:
        """Workspace-relative POSIX path for display and storage."""
        p = self.resolve(path)
        return p.relative_to(self.root).as_posix()

    def _guard_write(self, p: Path) -> None:
        if self.readonly:
            raise SandboxError(f"workspace is read-only: refusing write to {p}")

    # ── reads ───────────────────────────────────────────────────────────

    def read(self, path: str | Path, *, offset: int = 0, limit: int | None = None) -> str:
        p = self.resolve(path)
        if not p.exists():
            raise FileNotFoundError(self.rel(p))
        if p.is_dir():
            raise IsADirectoryError(self.rel(p))
        size = p.stat().st_size
        if size > _MAX_READ_BYTES and limit is None:
            raise SandboxError(
                f"{self.rel(p)} is {size} bytes (> {_MAX_READ_BYTES}); read with a limit"
            )
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            if offset:
                fh.seek(offset)
            data = fh.read(limit if limit is not None else _MAX_READ_BYTES)
        return data

    def read_lines(self, path: str | Path, start: int = 1, end: int | None = None) -> str:
        """Read a 1-indexed inclusive line window — the canonical 'view'."""
        text = self.read(path)
        lines = text.splitlines()
        end = end or len(lines)
        chosen = lines[start - 1:end]
        width = max(3, len(str(end)))
        return "\n".join(f"{i + start:>{width}}\t{line}" for i, line in enumerate(chosen))

    def exists(self, path: str | Path) -> bool:
        try:
            return self.resolve(path).exists()
        except SandboxError:
            return False

    def list_dir(self, path: str | Path = ".") -> list[str]:
        p = self.resolve(path)
        if not p.is_dir():
            raise NotADirectoryError(self.rel(p))
        out = []
        for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name)):
            if child.name in _SKIP_DIRS and child.is_dir():
                continue
            suffix = "/" if child.is_dir() else ""
            out.append(child.name + suffix)
        return out

    def walk_files(
        self,
        *,
        suffixes: Iterable[str] | None = None,
        max_files: int = 10_000,
    ) -> list[Path]:
        """All non-ignored files under root, bounded and deterministic."""
        suffixes = {s.lower() for s in suffixes} if suffixes else None
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if suffixes and p.suffix.lower() not in suffixes:
                    continue
                found.append(p)
                if len(found) >= max_files:
                    return found
        return found

    # ── writes ──────────────────────────────────────────────────────────

    def write(self, path: str | Path, content: str) -> Path:
        p = self.resolve(path)
        self._guard_write(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish replace: write temp file in same dir, then os.replace.
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".trishula-tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._writes.append(p)
        return p

    def append(self, path: str | Path, content: str) -> Path:
        p = self.resolve(path)
        self._guard_write(p)
        existed = p.exists()
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)
        if not existed:
            self._writes.append(p)
        return p

    def delete(self, path: str | Path) -> None:
        p = self.resolve(path)
        self._guard_write(p)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
        self._deletes.append(p)

    def make_dirs(self, path: str | Path) -> Path:
        p = self.resolve(path)
        self._guard_write(p)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ── change tracking ─────────────────────────────────────────────────

    @property
    def changed_files(self) -> list[str]:
        """Files written this session, deduped, workspace-relative."""
        seen: list[str] = []
        for p in self._writes:
            r = self.rel(p)
            if r not in seen:
                seen.append(r)
        return seen

    def snapshot_hashes(self) -> dict[str, str]:
        """Map of relpath -> sha256 for every file (used by undo/verify)."""
        import hashlib

        out: dict[str, str] = {}
        for p in self.walk_files():
            try:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                out[self.rel(p)] = h
            except OSError:
                continue
        return out
