from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "skills/software-development/shiva-engineering-team/scripts/team.py"


def load_module():
    spec = importlib.util.spec_from_file_location("shiva_team", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def write_spec(tmp_path: Path, tasks: list[dict]) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"goal": "ship", "tasks": tasks}))
    return path


def args(**values):
    return type("Args", (), values)()


def invoke(fn, db, namespace):
    with pytest.raises(SystemExit) as stopped:
        fn(db, namespace)
    return stopped.value.code


def test_rejects_cycles_and_unknown_dependencies(tmp_path):
    team = load_module()
    cyclic = write_spec(tmp_path, [
        {"id": "a", "title": "A", "depends_on": ["b"]},
        {"id": "b", "title": "B", "depends_on": ["a"]},
    ])
    with pytest.raises(ValueError, match="cycle"):
        team.parse_spec(str(cyclic))
    unknown = write_spec(tmp_path, [{"id": "a", "title": "A", "depends_on": ["missing"]}])
    with pytest.raises(ValueError, match="unknown dependencies"):
        team.parse_spec(str(unknown))


def test_dependency_order_claim_and_completion(tmp_path, capsys):
    team = load_module()
    db = team.connect(str(tmp_path / "team.db"))
    spec = write_spec(tmp_path, [
        {"id": "design", "title": "Design", "priority": 10},
        {"id": "build", "title": "Build", "depends_on": ["design"]},
    ])
    assert invoke(team.cmd_init, db, args(spec=str(spec), project="p")) == 0
    capsys.readouterr()
    assert invoke(team.cmd_claim, db, args(project="p", worker="w1", lease=60)) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["task"]["id"] == "design"
    assert invoke(team.cmd_complete, db, args(project="p", task="design", worker="w1", result="ok", evidence='{"tests":["pass"]}')) == 0
    capsys.readouterr()
    assert invoke(team.cmd_claim, db, args(project="p", worker="w2", lease=60)) == 0
    assert json.loads(capsys.readouterr().out)["task"]["id"] == "build"


def test_claim_is_exclusive_and_failure_requeues(tmp_path, capsys):
    team = load_module()
    db = team.connect(str(tmp_path / "team.db"))
    spec = write_spec(tmp_path, [{"id": "work", "title": "Work", "max_attempts": 2}])
    invoke(team.cmd_init, db, args(spec=str(spec), project="p")); capsys.readouterr()
    invoke(team.cmd_claim, db, args(project="p", worker="one", lease=60)); capsys.readouterr()
    invoke(team.cmd_claim, db, args(project="p", worker="two", lease=60))
    assert json.loads(capsys.readouterr().out)["task"] is None
    assert invoke(team.cmd_fail, db, args(project="p", task="work", worker="one", reason="retry", terminal=False)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "queued"
    invoke(team.cmd_claim, db, args(project="p", worker="two", lease=60)); capsys.readouterr()
    invoke(team.cmd_fail, db, args(project="p", task="work", worker="two", reason="still bad", terminal=False))
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_expired_lease_is_recovered(tmp_path, capsys, monkeypatch):
    team = load_module()
    clock = iter([100.0] * 20 + [1000.0] * 20)
    monkeypatch.setattr(team, "now", lambda: next(clock))
    db = team.connect(str(tmp_path / "team.db"))
    spec = write_spec(tmp_path, [{"id": "work", "title": "Work"}])
    invoke(team.cmd_init, db, args(spec=str(spec), project="p")); capsys.readouterr()
    invoke(team.cmd_claim, db, args(project="p", worker="lost", lease=30)); capsys.readouterr()
    # Force expiry without depending on wall-clock behavior.
    db.execute("UPDATE tasks SET lease_until=0 WHERE id='work'")
    invoke(team.cmd_claim, db, args(project="p", worker="recovery", lease=30))
    assert json.loads(capsys.readouterr().out)["task"]["owner"] == "recovery"
