"""Tiny SQLite storage layer.

Two databases live under ``$TRISHULA_HOME``:

* ``skills.db`` — the self-improvement library (skills + usage events)
* ``runs.db``   — coding/team runs, tasks, trajectories, retrospective scores

Everything is stored as JSON blobs in TEXT columns so the dataclasses evolve
without migrations for now; indexes cover the columns we actually query.
Writes use an immediate transaction with WAL for reader/writer friendliness.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

from trishula.core.logging import get_logger

log = get_logger("storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    body        TEXT NOT NULL,
    quality     REAL NOT NULL DEFAULT 0.5,
    uses        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skills_quality ON skills(quality);

CREATE TABLE IF NOT EXISTS skill_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL,
    success     INTEGER NOT NULL,
    context     TEXT NOT NULL DEFAULT '{}',
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_events_skill ON skill_events(skill_id);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,           -- "coding" | "team" | "autonomy"
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL,
    score       REAL NOT NULL DEFAULT 0,
    body        TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL,
    assignee    TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);
"""


class Database:
    """Thin, thread-safe sqlite wrapper with JSON (de)serialization."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        log.debug("opened database %s", self.path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── generic helpers ─────────────────────────────────────────────────

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)))

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    @staticmethod
    def dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def loads(raw: str, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default
