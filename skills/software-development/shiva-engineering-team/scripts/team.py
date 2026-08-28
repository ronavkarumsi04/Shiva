#!/usr/bin/env python3
"""Durable, dependency-aware coordination for Shiva engineering teams.

The command intentionally uses only the Python standard library.  It is safe for
multiple agent processes: every mutating operation uses SQLite transactions and
claims are leases, so abandoned work can be recovered.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY, goal TEXT NOT NULL, created REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL, brief TEXT NOT NULL, role TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
 owner TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 3, result TEXT, evidence TEXT,
 created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dependencies(
 task_id TEXT NOT NULL REFERENCES tasks(id),
 depends_on TEXT NOT NULL REFERENCES tasks(id),
 PRIMARY KEY(task_id, depends_on), CHECK(task_id <> depends_on)
);
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
 task_id TEXT, kind TEXT NOT NULL, actor TEXT, payload TEXT NOT NULL,
 created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_ready
 ON tasks(project_id,status,priority,created);
"""
TERMINAL = {"done", "failed", "cancelled"}


def emit(data: Any, *, code: int = 0) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
    raise SystemExit(code)


def connect(path: str) -> sqlite3.Connection:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def now() -> float:
    return time.time()


def event(db: sqlite3.Connection, project: str, kind: str, *, task: str | None = None,
          actor: str | None = None, payload: Any = None) -> None:
    db.execute("INSERT INTO events(project_id,task_id,kind,actor,payload,created) VALUES(?,?,?,?,?,?)",
               (project, task, kind, actor, json.dumps(payload or {}), now()))


def parse_spec(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    spec = json.loads(raw)
    if not isinstance(spec, dict) or not str(spec.get("goal", "")).strip():
        raise ValueError("spec requires a non-empty goal")
    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("spec requires a non-empty tasks array")
    ids: set[str] = set()
    for i, task in enumerate(tasks):
        if not isinstance(task, dict): raise ValueError(f"task {i} must be an object")
        tid = str(task.get("id", "")).strip()
        if not tid or tid in ids: raise ValueError(f"task {i} has missing/duplicate id")
        ids.add(tid)
        if not str(task.get("title", "")).strip(): raise ValueError(f"task {tid} needs title")
    for task in tasks:
        unknown = set(task.get("depends_on", [])) - ids
        if unknown: raise ValueError(f"task {task['id']} has unknown dependencies: {sorted(unknown)}")
    # Kahn validation prevents permanently blocked cyclic plans.
    deps = {str(t["id"]): set(map(str, t.get("depends_on", []))) for t in tasks}
    ready = [k for k, v in deps.items() if not v]
    seen = 0
    while ready:
        node = ready.pop(); seen += 1
        for key in deps:
            if node in deps[key]:
                deps[key].remove(node)
                if not deps[key]: ready.append(key)
    if seen != len(tasks): raise ValueError("task dependency graph contains a cycle")
    return spec


def cmd_init(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    spec = parse_spec(args.spec)
    pid = args.project or f"project-{uuid.uuid4().hex[:10]}"
    stamp = now()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO projects(id,goal,created,metadata) VALUES(?,?,?,?)",
                   (pid, spec["goal"].strip(), stamp, json.dumps(spec.get("metadata", {}))))
        for t in spec["tasks"]:
            db.execute("""INSERT INTO tasks(id,project_id,title,brief,role,priority,max_attempts,created,updated)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                       (str(t["id"]), pid, t["title"].strip(), str(t.get("brief", "")),
                        str(t.get("role", "engineer")), int(t.get("priority", 0)),
                        int(t.get("max_attempts", 3)), stamp, stamp))
            for dep in t.get("depends_on", []):
                db.execute("INSERT INTO dependencies(task_id,depends_on) VALUES(?,?)", (str(t["id"]), str(dep)))
        event(db, pid, "project.created", payload={"task_count": len(spec["tasks"])})
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK"); raise
    emit({"ok": True, "project": pid, "tasks": len(spec["tasks"])})


def release_expired(db: sqlite3.Connection, project: str) -> int:
    stamp = now()
    rows = db.execute("SELECT id,owner FROM tasks WHERE project_id=? AND status='running' AND lease_until<?",
                      (project, stamp)).fetchall()
    for row in rows:
        db.execute("UPDATE tasks SET status='queued',owner=NULL,lease_until=NULL,updated=? WHERE id=?",
                   (stamp, row["id"]))
        event(db, project, "task.lease_expired", task=row["id"], actor=row["owner"])
    return len(rows)


def ready_query() -> str:
    return """SELECT t.* FROM tasks t
      WHERE t.project_id=? AND t.status='queued' AND t.attempts<t.max_attempts
      AND NOT EXISTS (
        SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.depends_on
        WHERE d.task_id=t.id AND p.status!='done'
      )
      ORDER BY t.priority DESC,t.created,t.id LIMIT 1"""


def cmd_claim(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        release_expired(db, args.project)
        params: list[Any] = [args.project]
        query = ready_query()
        row = db.execute(query, params).fetchone()
        if row is None:
            db.execute("COMMIT"); emit({"ok": True, "task": None})
        lease = now() + max(30, args.lease)
        db.execute("""UPDATE tasks SET status='running',owner=?,lease_until=?,attempts=attempts+1,updated=?
                      WHERE id=? AND status='queued'""", (args.worker, lease, now(), row["id"]))
        event(db, args.project, "task.claimed", task=row["id"], actor=args.worker,
              payload={"lease_until": lease})
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction: db.execute("ROLLBACK")
        raise
    task = dict(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())
    task["depends_on"] = [r[0] for r in db.execute("SELECT depends_on FROM dependencies WHERE task_id=?",(row["id"],))]
    emit({"ok": True, "task": task})


def owned_task(db: sqlite3.Connection, args: argparse.Namespace) -> sqlite3.Row:
    row = db.execute("SELECT * FROM tasks WHERE id=? AND project_id=?", (args.task,args.project)).fetchone()
    if row is None: emit({"ok": False, "error": "task not found"}, code=2)
    if row["status"] != "running" or row["owner"] != args.worker:
        emit({"ok": False, "error": "task is not leased by this worker"}, code=3)
    return row


def cmd_heartbeat(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    db.execute("BEGIN IMMEDIATE"); owned_task(db,args)
    lease = now() + max(30,args.lease)
    db.execute("UPDATE tasks SET lease_until=?,updated=? WHERE id=?",(lease,now(),args.task))
    event(db,args.project,"task.heartbeat",task=args.task,actor=args.worker)
    db.execute("COMMIT"); emit({"ok":True,"lease_until":lease})


def cmd_complete(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    db.execute("BEGIN IMMEDIATE"); owned_task(db,args)
    evidence = json.loads(args.evidence) if args.evidence else {}
    if not args.result.strip(): emit({"ok":False,"error":"result is required"},code=2)
    db.execute("""UPDATE tasks SET status='done',result=?,evidence=?,lease_until=NULL,updated=? WHERE id=?""",
               (args.result,json.dumps(evidence),now(),args.task))
    event(db,args.project,"task.completed",task=args.task,actor=args.worker,payload=evidence)
    remaining=db.execute("SELECT count(*) FROM tasks WHERE project_id=? AND status NOT IN ('done','cancelled')",(args.project,)).fetchone()[0]
    if remaining==0:
        db.execute("UPDATE projects SET status='done' WHERE id=?",(args.project,)); event(db,args.project,"project.completed")
    db.execute("COMMIT"); emit({"ok":True,"project_complete":remaining==0})


def cmd_fail(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    db.execute("BEGIN IMMEDIATE"); row=owned_task(db,args)
    terminal = row["attempts"] >= row["max_attempts"] or args.terminal
    status = "failed" if terminal else "queued"
    db.execute("UPDATE tasks SET status=?,result=?,owner=NULL,lease_until=NULL,updated=? WHERE id=?",
               (status,args.reason,now(),args.task))
    event(db,args.project,"task.failed" if terminal else "task.requeued",task=args.task,actor=args.worker,
          payload={"reason":args.reason,"terminal":terminal})
    if terminal: db.execute("UPDATE projects SET status='attention' WHERE id=?",(args.project,))
    db.execute("COMMIT"); emit({"ok":True,"status":status})


def cmd_status(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    release_expired(db,args.project)
    project=db.execute("SELECT * FROM projects WHERE id=?",(args.project,)).fetchone()
    if project is None: emit({"ok":False,"error":"project not found"},code=2)
    tasks=[]
    for row in db.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY priority DESC,created,id",(args.project,)):
        item=dict(row); item["depends_on"]=[r[0] for r in db.execute("SELECT depends_on FROM dependencies WHERE task_id=?",(row["id"],))]
        tasks.append(item)
    counts={r[0]:r[1] for r in db.execute("SELECT status,count(*) FROM tasks WHERE project_id=? GROUP BY status",(args.project,))}
    emit({"ok":True,"project":dict(project),"counts":counts,"tasks":tasks})


def cmd_events(db: sqlite3.Connection,args: argparse.Namespace)->None:
    rows=db.execute("SELECT * FROM events WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?",(args.project,args.after,args.limit))
    emit({"ok":True,"events":[dict(r) for r in rows]})


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--db",default=os.getenv("SHIVA_TEAM_DB",".shiva/team.db"))
    sub=p.add_subparsers(dest="command",required=True)
    s=sub.add_parser("init"); s.add_argument("spec"); s.add_argument("--project"); s.set_defaults(fn=cmd_init)
    for name,fn in (("claim",cmd_claim),("status",cmd_status),("events",cmd_events),("heartbeat",cmd_heartbeat),("complete",cmd_complete),("fail",cmd_fail)):
        s=sub.add_parser(name); s.add_argument("project"); s.set_defaults(fn=fn)
        if name=="claim": s.add_argument("--worker",required=True); s.add_argument("--lease",type=int,default=900)
        if name in {"heartbeat","complete","fail"}:
            s.add_argument("task"); s.add_argument("--worker",required=True)
        if name=="heartbeat": s.add_argument("--lease",type=int,default=900)
        if name=="complete": s.add_argument("--result",required=True); s.add_argument("--evidence")
        if name=="fail": s.add_argument("--reason",required=True); s.add_argument("--terminal",action="store_true")
        if name=="events": s.add_argument("--after",type=int,default=0); s.add_argument("--limit",type=int,default=200)
    return p


def main()->None:
    args=parser().parse_args(); db=connect(args.db)
    try: args.fn(db,args)
    except (ValueError,json.JSONDecodeError,sqlite3.IntegrityError) as exc: emit({"ok":False,"error":str(exc)},code=2)
    finally: db.close()

if __name__=="__main__": main()
