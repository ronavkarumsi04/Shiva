"""Regression coverage for Kanban CLI process exit status propagation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _run_shiva(home: Path, *args: str, marker: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SHIVA_HOME"] = str(home)
    env["SHIVA_KANBAN_HOME"] = str(home)
    for name in (
        "SHIVA_KANBAN_BOARD",
        "SHIVA_KANBAN_DB",
        "SHIVA_KANBAN_WORKSPACES_ROOT",
    ):
        env.pop(name, None)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if marker:
        env["SHIVA_DELEGATED_CHILD_CONTEXT"] = "1"
    else:
        env.pop("SHIVA_DELEGATED_CHILD_CONTEXT", None)
    return subprocess.run(
        [sys.executable, "-m", "shiva_cli.main", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_delegated_child_kanban_cli_refusal_returns_nonzero_exit_status(tmp_path):
    """A printed Kanban mutation refusal must not look like CLI success."""
    home = tmp_path / "shiva"
    home.mkdir()

    created = _run_shiva(home, "kanban", "create", "exit status probe", "--json")
    assert created.returncode == 0, created.stderr
    task_id = json.loads(created.stdout)["id"]

    refused = _run_shiva(
        home,
        "kanban",
        "comment",
        task_id,
        "must be refused",
        marker=True,
    )

    assert refused.returncode == 1
    assert "delegate_task child contexts cannot mutate Kanban tasks via the CLI" in refused.stderr
