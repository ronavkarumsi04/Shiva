from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "skills/software-development/shiva-forge/scripts/forge.py"


def module():
    spec = importlib.util.spec_from_file_location("shiva_forge", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(loaded)
    return loaded


def git(root: Path, *args: str):
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True)


def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="sample"\nversion="1.0.0"\n')
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text("def test_value(): assert True\n")
    (tmp_path / "AGENTS.md").write_text("instructions\n")
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_inventory_reports_contracts_and_languages(tmp_path):
    forge = module(); root = repository(tmp_path)
    data = forge.inventory(root)
    assert data["git"]["dirty"] is False
    assert data["languages"]["python"] == 2
    assert data["test_files"] == 1
    assert "AGENTS.md" in data["instructions"]
    assert {m["ecosystem"] for m in data["manifests"]} == {"python"}


def test_change_aware_python_plan_selects_adjacent_test(tmp_path):
    forge = module(); root = repository(tmp_path)
    (root / "app.py").write_text("VALUE = 2\n")
    changes = forge.changed_files(root)
    checks = forge.select_checks(root, changes)
    commands = [check.command for check in checks]
    assert any(command[:3] == (forge.sys.executable, "-m", "py_compile") for command in commands)
    assert any("tests/test_app.py" in command for command in commands)
    assert len(commands) == len(set(commands))


def test_verify_receipt_fails_on_required_failure(tmp_path):
    forge = module(); root = repository(tmp_path)
    checks = [forge.Check("bad", (forge.sys.executable, "-c", "raise SystemExit(7)"), "proof")]
    results = forge.execute_checks(root, checks, timeout=5)
    assert results[0].status == "failed"
    assert results[0].exit_code == 7
    assert all(result.status == "passed" for result in results if not result.required) is True


def test_atomic_receipt_replaces_existing_file(tmp_path):
    forge = module(); target = tmp_path / "nested/receipt.json"
    forge.atomic_json(target, {"passed": False})
    forge.atomic_json(target, {"passed": True, "results": []})
    assert json.loads(target.read_text()) == {"passed": True, "results": []}
    assert list(target.parent.glob("*.tmp")) == []


def test_nested_instruction_chain_is_ordered(tmp_path):
    forge = module(); root = repository(tmp_path)
    nested = root / "src/component"; nested.mkdir(parents=True)
    (root / "src/AGENTS.md").write_text("src\n")
    (nested / "CLAUDE.md").write_text("component\n")
    (nested / "file.py").write_text("pass\n")
    chain = forge.instruction_chain(root, "src/component/file.py")
    assert chain.index("AGENTS.md") < chain.index("src/AGENTS.md") < chain.index("src/component/CLAUDE.md")
