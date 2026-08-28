"""Git-worktree worker isolation for the Devas swarm.

Genuine parallel engineering means two implementers must never write the
same file at once. Git worktrees give each worker its own checked-out
working directory *and branch* pointing at the same object store:

* workers edit independently with no shared cwd;
* a finished worker merges its branch back to the base branch in the main
  checkout — if the merge is clean, keep it (the next worker builds on the
  merged state); if it conflicts (the same regions were touched), the merge
  is aborted and the task fails with a *conflict* status so the swarm can
  reopen/re-dispatch it;
* pools are bounded and cleaned up on teardown.

Non-git workspaces (or git missing) transparently degrade to in-place
execution: every worker shares the base workspace and the manager reports
``isolated=False``, which keeps the harness useful anywhere.
"""

from __future__ import annotations

import os
import shlex
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from trishula.core.logging import get_logger
from trishula.core.types import Journal, EventKind
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace

log = get_logger("team.worktree")


@dataclass
class WorktreeResult:
    task_id: str
    ok: bool
    isolated: bool
    path: str
    branch: str = ""
    merged: bool = False
    conflict_files: List[str] = field(default_factory=list)
    error: str = ""


class WorktreePool:
    """Manage a bounded set of git worktrees used by swarm workers."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        max_worktrees: int = 4,
        branch_prefix: str = "trishula/",
        journal: Journal | None = None,
        shell: Shell | None = None,
        client=None,  # LLM client for the merge arbiter (None = deterministic only)
    ):
        self.base = Path(workspace_root).resolve()
        self.max = max(1, max_worktrees)
        self.prefix = branch_prefix
        self.journal = journal
        self.shell = shell or Shell(self.base, timeout=120)
        self.is_git = (self.base / ".git").exists()
        self.client = client
        self._active: Dict[str, Path] = {}

    # ── lifecycle ───────────────────────────────────────────────────────

    def acquire(self, task_id: str) -> tuple[Workspace, bool]:
        """Return a workspace for ``task_id`` — an isolated worktree if git
        is available and under capacity, else the base workspace."""
        if not self.is_git or len(self._active) >= self.max:
            return Workspace(self.base), False
        name = f"{self.prefix}{task_id.replace('_', '-')[:20]}-{uuid.uuid4().hex[:6]}"
        wt_dir = self.base / ".trishula" / "worktrees" / name.split("/")[-1]
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        rel_dir = os.path.relpath(wt_dir, self.base)
        res = self.shell.run(f"git worktree add -b {name} {shlex.quote(rel_dir)} HEAD", timeout=120)
        if not res.ok:
            log.warning("worktree add failed (%s); falling back to base workspace",
                        res.text()[:200])
            return Workspace(self.base), False
        self._active[task_id] = wt_dir
        if self.journal:
            self.journal.emit(EventKind.TEAM_SPAWN, worktree=rel_dir, branch=name, task=task_id)
        log.info("acquired worktree %s for %s", rel_dir, task_id)
        return Workspace(wt_dir), True

    def commit_worker_changes(self, task_id: str, message: str = "") -> bool:
        """Commit any changes made inside a task's worktree.

        Workers edit files in their own checkout; those edits must become a
        commit on the worktree branch before :meth:`complete` can merge them.
        Returns True when a commit was created.
        """
        wt_dir = self._active.get(task_id)
        if wt_dir is None:
            return False
        msg = message or f"Trishula worker {task_id[:12]}"
        r = self.shell.run(
            f"git add -A && git commit -qm {shlex.quote(msg)}",
            timeout=120, cwd=wt_dir,
        )
        return r.ok

    def complete(self, task_id: str, *, merge: bool = True) -> WorktreeResult:
        wt_dir = self._active.pop(task_id, None)
        if wt_dir is None:
            return WorktreeResult(task_id, True, isolated=False, path=str(self.base))
        branch = self._branch_for(wt_dir)
        if merge:
            merged, conflicts, error = self._merge(branch)
        else:
            merged, conflicts, error = False, [], "merge skipped"
        self._remove(wt_dir, branch)
        if conflicts:
            return WorktreeResult(task_id, False, True, str(wt_dir), branch,
                                  merged=False, conflict_files=conflicts,
                                  error="merge conflict (parallel edits to same regions)")
        return WorktreeResult(task_id, error == "" or merged, True, str(wt_dir),
                              branch=branch, merged=merged, error=error)

    def cleanup(self) -> None:
        for task_id in list(self._active):
            try:
                self.complete(task_id, merge=False)
            except Exception:  # noqa: BLE001
                pass
        self.shell.run("git worktree prune", timeout=60)
        shutil.rmtree(self.base / ".trishula" / "worktrees", ignore_errors=True)

    # ── internals ───────────────────────────────────────────────────────

    def _branch_for(self, wt_dir: Path) -> str:
        res = self.shell.run(
            f"git -C {shlex.quote(str(wt_dir))} rev-parse --abbrev-ref HEAD", timeout=30
        )
        return res.stdout.strip()

    def _merge(self, branch: str) -> tuple[bool, List[str], str]:
        """Merge ``branch`` into the base checkout's current branch.

        On conflict the merge arbiter tries deterministic (and, when a client
        is provided, LLM-assisted) reconciliation. Only conflicts that cannot
        be proven safe are left unresolved (merge aborted, files reported).
        """
        r = self.shell.run(f"git merge --no-ff {shlex.quote(branch)} "
                           f"-m {shlex.quote(f'Trishula swarm: merge {branch}')}", timeout=120)
        if r.ok:
            return True, [], ""
        files = self._conflict_files()
        if files:
            from trishula.team.arbiter import MergeArbiter

            arbiter = MergeArbiter(self.shell, client=self.client)
            resolutions = arbiter.resolve_all()
            unresolved = [
                res for res in resolutions
                if not res.resolved and res.file in files
            ]
            if resolutions and not unresolved:
                # All conflicts resolved: complete the merge commit.
                commit = self.shell.run(
                    f"git commit --no-edit -m {shlex.quote(f'Trishula swarm: merge {branch} (arbiter)')}",
                    timeout=120,
                )
                if commit.ok:
                    methods = ",".join(sorted({res.method for res in resolutions}))
                    return True, [], f"conflicts auto-resolved via {methods}"
                files = self._conflict_files()
            elif unresolved:
                names = [res.file for res in unresolved]
                self.shell.run("git merge --abort", timeout=60)
                return False, names, (
                    "merge conflict that auto-resolution could not prove safe: "
                    + ", ".join(names)
                )
        # Nothing resolved — abort so the base tree stays clean.
        self.shell.run("git merge --abort", timeout=60)
        return False, files, r.text()[-400:]

    def _conflict_files(self) -> List[str]:
        res = self.shell.run("git diff --name-only --diff-filter=U", timeout=30)
        if res.ok:
            return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        return []

    def _remove(self, wt_dir: Path, branch: str) -> None:
        rel = os.path.relpath(wt_dir, self.base)
        self.shell.run(f"git worktree remove --force {shlex.quote(rel)}", timeout=60)
        if branch:
            self.shell.run(f"git branch -D {shlex.quote(branch)}", timeout=60)
