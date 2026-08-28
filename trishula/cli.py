"""Command-line entry point for the Trishula engine.

    trishula code "goal" [--path DIR] [--model M] [--provider P]
    trishula team "goal" [--path DIR] [--model M] [--provider P]
    trishula skills [list|search QUERY]
    trishula runs
    trishula selftest

The CLI is intentionally thin — every command maps to one engine call so the
same behaviors exist in-process (where the real Shiva CLI will wire them into
slash commands and the gateway).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from trishula import __version__
from trishula.core.config import TrishulaConfig
from trishula.core.logging import enable_console, get_logger

log = get_logger("cli")


def _config(args: argparse.Namespace) -> TrishulaConfig:
    cfg = TrishulaConfig(
        model=getattr(args, "model", "") or "",
        model_provider=getattr(args, "provider", "") or "",
    )
    return cfg


def cmd_code(args: argparse.Namespace) -> int:
    from trishula.autonomy.loop import AutonomyLoop

    cfg = _config(args)
    loop = AutonomyLoop(args.path, config=cfg)
    run = loop.coding_task(args.goal, max_steps=args.max_steps)
    report = run.report
    retro = run.retrospective
    print("\n=== CODING RUN ===")
    print(f"goal     : {run.goal}")
    print(f"ok       : {report.get('ok')}")
    print(f"verdict  : {report.get('verdict')}")
    print(f"steps    : {report.get('steps')}")
    print(f"changed  : {', '.join(report.get('changed_files', [])) or '(none)'}")
    print(f"summary  : {report.get('summary', '')[:500]}")
    print("\n=== RETROSPECTIVE ===")
    print(f"score    : {retro.get('score')}  success={retro.get('success')}")
    print(f"signals  : {json.dumps(retro.get('signals', {}))}")
    for lesson in retro.get("lessons", []):
        print(f"  lesson  : {lesson}")
    if run.skills_created:
        print(f"skills distilled: {', '.join(run.skills_created)}")
    return 0 if report.get("ok") else 1


def cmd_team(args: argparse.Namespace) -> int:
    from trishula.llm import get_client
    from trishula.team.planner import TeamPlanner
    from trishula.team.swarm import Swarm, DeterministicWorker

    cfg = _config(args)
    client = get_client(cfg)
    planner = TeamPlanner(args.path, client=client, config=cfg)
    plan = planner.plan(args.goal)
    print(f"\n=== TEAM PLAN ({len(plan.tasks)} tasks) ===")
    print(plan.rationale)
    for i, t in enumerate(plan.tasks, 1):
        deps = [plan.get(d).title for d in t.deps]
        print(f"  {i:>2}. [{t.assignee:<11}] {t.title}  <- {deps or 'start'}")

    if args.plan_only:
        return 0

    # Deterministic workers run offline; LocalAgentWorker runs role-scoped
    # mini coding loops (real agents) when a model is configured.
    if cfg.deterministic:
        worker = DeterministicWorker()
    else:
        from trishula.team.swarm import LocalAgentWorker

        worker = LocalAgentWorker(args.path, client=client, config=cfg)

    swarm = Swarm(args.path, plan, worker=worker, config=cfg)
    report = swarm.execute()
    print("\n=== SWARM RESULT ===")
    print(f"ok: {report.ok}   artifacts: {len(report.artifacts)}")
    for r in report.results:
        mark = {"done": "✓", "failed": "✗", "skipped": "–"}.get(r.status.value, "?")
        print(f"  {mark} [{r.assignee:<11}] {r.title}  ({r.attempts} attempt(s))")
        if r.error:
            print(f"      error: {r.error}")
    return 0 if report.ok else 1


def cmd_skills(args: argparse.Namespace) -> int:
    from trishula.autonomy.skills import SkillLibrary

    lib = SkillLibrary(_config(args))
    if args.skills_action == "list":
        stats = lib.usage_stats()
        if not stats:
            print("(no skills yet — they distill automatically after successful runs)")
            return 0
        for s in stats:
            print(f"  {s['quality']:.2f}  uses={s['uses']:<3} win={s['wins']:<3} loss={s['losses']:<3}  {s['name']}")
    elif args.skills_action == "search":
        hits = lib.search(args.query)
        if not hits:
            print(f"(no skills matched {args.query!r})")
            return 0
        for skill, score in hits:
            print(f"  {score:.3f}  q={skill.quality:.2f}  {skill.name}")
            print(f"          {skill.when_to_use[:120]}")
    else:
        print(f"unknown skills action {args.skills_action!r}")
        return 2
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    from trishula.autonomy.loop import AutonomyLoop

    loop = AutonomyLoop(args.path, config=_config(args))
    history = loop.history(limit=args.limit)
    if not history:
        print("(no recorded runs)")
        return 0
    for r in history:
        mark = "✓" if r["status"] == "success" else "✗"
        print(f"  {mark} {r['score']:.2f}  [{r['kind']:<6}] {r['goal'][:80]}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Run the bundled trishula unittest suite without pytest installed."""
    import unittest

    here = Path(__file__).resolve().parent
    loader = unittest.TestLoader()
    suite = loader.discover(str(here / "tests"), pattern="test_*.py", top_level_dir=str(here.parent))
    runner = unittest.TextTestRunner(verbosity=args.verbose + 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trishula",
        description="Trishula — the Shiva Agent upgrade core (coding · tools · autonomy · teams).",
    )
    p.add_argument("--version", action="version", version=f"trishula {__version__}")
    p.add_argument("-v", "--verbose", action="count", default=0)
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--path", default=os.getcwd(), help="workspace root (default: cwd)")
        sp.add_argument("--model", default="", help="model id (or set TRISHULA_MODEL)")
        sp.add_argument("--provider", default="", help="stub|openai|openrouter|nous|anthropic")
        sp.add_argument("--max-steps", type=int, default=60, dest="max_steps")

    sp_code = sub.add_parser("code", help="run an autonomous coding task with the full learning loop")
    sp_code.add_argument("goal")
    add_common(sp_code)
    sp_code.set_defaults(func=cmd_code)

    sp_team = sub.add_parser("team", help="plan and execute a goal as a Devin-style dev team")
    sp_team.add_argument("goal")
    sp_team.add_argument("--plan-only", action="store_true", dest="plan_only")
    add_common(sp_team)
    sp_team.set_defaults(func=cmd_team)

    sp_skills = sub.add_parser("skills", help="inspect the self-improvement skill library")
    sp_skills.add_argument("skills_action", nargs="?", default="list", choices=["list", "search"])
    sp_skills.add_argument("query", nargs="?", default="")
    add_common(sp_skills)
    sp_skills.set_defaults(func=cmd_skills)

    sp_runs = sub.add_parser("runs", help="show recorded autonomous runs and their scores")
    sp_runs.add_argument("--limit", type=int, default=20)
    add_common(sp_runs)
    sp_runs.set_defaults(func=cmd_runs)

    sp_test = sub.add_parser("selftest", help="run the trishula engine test suite")
    sp_test.set_defaults(func=cmd_selftest)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", 0):
        import logging

        enable_console(logging.DEBUG if args.verbose > 1 else logging.INFO)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
