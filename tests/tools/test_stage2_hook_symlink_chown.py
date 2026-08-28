"""Regression tests for symlink-safe Docker stage2 ownership repair."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE2_HOOK = REPO_ROOT / "docker" / "stage2-hook.sh"


@pytest.fixture(scope="module")
def stage2_text() -> str:
    if not STAGE2_HOOK.exists():
        pytest.skip("docker/stage2-hook.sh not present in this checkout")
    return STAGE2_HOOK.read_text()


def _chown_shiva_tree_function(text: str) -> str:
    start = text.index("path_has_symlink_component() {")
    end = text.index("\n\nneeds_chown=false", start)
    return text[start:end]


def _run_helper(
    text: str,
    target: Path,
    log_path: Path,
    *,
    shiva_home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("sh not available")
    shiva_home = target if shiva_home is None else shiva_home
    script = (
        "set -eu\n"
        f'SHIVA_HOME="{shiva_home}"\n'
        f"{_chown_shiva_tree_function(text)}\n"
        f'chown() {{ printf "%s\\n" "$*" >> "{log_path}"; }}\n'
        f'chown_shiva_tree "{target}"\n'
    )
    return subprocess.run([shell, "-c", script], capture_output=True, text=True)


def test_chown_helper_repairs_real_directories(stage2_text: str, tmp_path: Path) -> None:
    target = tmp_path / "home"
    target.mkdir()
    log_path = tmp_path / "chown.log"

    proc = _run_helper(stage2_text, target, log_path)

    assert proc.returncode == 0, proc.stderr
    assert log_path.read_text().splitlines() == [
        f"-R shiva:shiva {target}",
    ]


def test_chown_helper_refuses_symlinked_directories(stage2_text: str, tmp_path: Path) -> None:
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    symlinked_home = tmp_path / "shiva-home"
    try:
        symlinked_home.symlink_to(real_home, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are not available on this platform")
    log_path = tmp_path / "chown.log"

    proc = _run_helper(stage2_text, symlinked_home, log_path)

    assert proc.returncode == 0, proc.stderr
    assert not log_path.exists()
    assert "refusing recursive chown through symlinked path" in proc.stdout


def test_chown_helper_refuses_target_under_symlinked_home(
    stage2_text: str,
    tmp_path: Path,
) -> None:
    real_home = tmp_path / "real-home"
    (real_home / "cron").mkdir(parents=True)
    linked_home = tmp_path / "linked-home"
    try:
        linked_home.symlink_to(real_home, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are not available on this platform")
    log_path = tmp_path / "chown.log"

    proc = _run_helper(
        stage2_text,
        linked_home / "cron",
        log_path,
        shiva_home=linked_home,
    )

    assert proc.returncode == 0, proc.stderr
    assert not log_path.exists(), "must not chown through a symlinked SHIVA_HOME"
    assert "refusing recursive chown through symlinked path" in proc.stdout


def test_stage2_uses_symlink_safe_helper_for_shiva_home_trees(stage2_text: str) -> None:
    assert 'chown_shiva_tree "$SHIVA_HOME/$sub"' in stage2_text
    assert 'chown_shiva_tree "$SHIVA_HOME/profiles"' in stage2_text
    assert 'chown_shiva_tree "$SHIVA_HOME/cron"' in stage2_text
    assert 'chown -R shiva:shiva "$SHIVA_HOME/$sub"' not in stage2_text
    assert 'chown -R shiva:shiva "$SHIVA_HOME/profiles"' not in stage2_text
    assert 'chown -R shiva:shiva "$SHIVA_HOME/cron"' not in stage2_text


def test_stage2_skips_top_level_chown_for_symlinked_shiva_home(
    stage2_text: str,
) -> None:
    assert 'refuse_symlinked_path "chown" "$SHIVA_HOME"' in stage2_text


def test_stage2_skips_recursive_repairs_when_tree_is_already_owned(
    stage2_text: str,
) -> None:
    assert "tree_has_non_shiva_owner() {" in stage2_text
    assert 'if [ -e "$SHIVA_HOME/$sub" ] && tree_has_non_shiva_owner "$SHIVA_HOME/$sub"; then' in stage2_text
    assert 'if [ -d "$SHIVA_HOME/profiles" ] && tree_has_non_shiva_owner "$SHIVA_HOME/profiles"; then' in stage2_text
    # Sibling every-boot chown blocks carry the same warm-boot gate.
    assert 'if [ -d "$SHIVA_HOME/cron" ] && tree_has_non_shiva_owner "$SHIVA_HOME/cron"; then' in stage2_text
    assert 'if [ -d "$SHIVA_HOME/platforms/pairing" ] && tree_has_non_shiva_owner "$SHIVA_HOME/platforms/pairing"; then' in stage2_text
    assert 'if [ -d "$SHIVA_HOME/pairing" ] && tree_has_non_shiva_owner "$SHIVA_HOME/pairing"; then' in stage2_text
