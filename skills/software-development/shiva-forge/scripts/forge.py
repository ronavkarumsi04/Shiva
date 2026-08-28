#!/usr/bin/env python3
"""Shiva Forge: repository intelligence and evidence-driven verification.

A dependency-free coding harness for agents. It inventories a repository,
selects checks from the actual change set, runs them with hard timeouts, and
writes a durable JSON receipt. It never mutates source files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

IGNORED = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build", "coverage", "target", "__pycache__"}
MANIFESTS = {
    "pyproject.toml": "python", "setup.py": "python", "requirements.txt": "python",
    "package.json": "javascript", "pnpm-workspace.yaml": "javascript", "deno.json": "javascript",
    "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java", "build.gradle": "java",
    "Gemfile": "ruby", "composer.json": "php", "Package.swift": "swift",
}
EXTENSIONS = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".rs": "rust", ".go": "go",
    ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".cs": "dotnet", ".cpp": "cpp", ".cc": "cpp", ".c": "c",
}
INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "README.md")


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]
    reason: str
    required: bool = True


@dataclass
class Result:
    name: str
    command: list[str]
    reason: str
    required: bool
    status: str
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str


def run(argv: list[str], cwd: Path, *, timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=check)


def git(root: Path, *args: str) -> str:
    return run(["git", *args], root).stdout.strip()


def is_repo(root: Path) -> bool:
    return run(["git", "rev-parse", "--is-inside-work-tree"], root).returncode == 0


def tracked_files(root: Path) -> list[str]:
    if is_repo(root):
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
        return sorted(x.decode("utf-8", "surrogateescape") for x in raw.split(b"\0") if x)
    output: list[str] = []
    for p in root.rglob("*"):
        if p.is_file() and not any(part in IGNORED for part in p.relative_to(root).parts):
            output.append(p.relative_to(root).as_posix())
    return sorted(output)


def changed_files(root: Path, base: str | None = None, include_untracked: bool = True) -> list[str]:
    if not is_repo(root): return []
    args = ["diff", "--name-only", "-z"]
    if base: args.append(f"{base}...HEAD")
    raw = subprocess.check_output(["git", *args], cwd=root)
    names = {x.decode("utf-8", "surrogateescape") for x in raw.split(b"\0") if x}
    raw = subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=root)
    names.update(x.decode("utf-8", "surrogateescape") for x in raw.split(b"\0") if x)
    if include_untracked:
        raw = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root)
        names.update(x.decode("utf-8", "surrogateescape") for x in raw.split(b"\0") if x)
    return sorted(names)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def instruction_chain(root: Path, relative_file: str | None = None) -> list[str]:
    target = (root / relative_file).parent if relative_file else root
    try: target = target.resolve(); target.relative_to(root.resolve())
    except ValueError: return []
    directories: list[Path] = []
    current = target
    while True:
        directories.append(current)
        if current == root.resolve(): break
        current = current.parent
    found: list[str] = []
    for directory in reversed(directories):
        for name in INSTRUCTION_NAMES:
            p = directory / name
            if p.is_file(): found.append(p.relative_to(root).as_posix())
    return found


def inventory(root: Path) -> dict[str, Any]:
    files = tracked_files(root)
    languages: dict[str, int] = {}
    manifests: list[dict[str, str]] = []
    tests = 0
    total_bytes = 0
    for rel in files:
        p = root / rel
        try: total_bytes += p.stat().st_size
        except OSError: pass
        language = EXTENSIONS.get(p.suffix.lower())
        if language: languages[language] = languages.get(language, 0) + 1
        if p.name in MANIFESTS: manifests.append({"path": rel, "ecosystem": MANIFESTS[p.name]})
        parts = set(p.parts)
        if "tests" in parts or "test" in parts or p.name.startswith(("test_", "spec.")) or ".test." in p.name or ".spec." in p.name:
            tests += 1
    head = git(root, "rev-parse", "HEAD") if is_repo(root) else None
    branch = git(root, "branch", "--show-current") if is_repo(root) else None
    dirty = bool(changed_files(root)) if is_repo(root) else None
    return {"root": str(root), "git": {"head": head, "branch": branch, "dirty": dirty},
            "files": len(files), "bytes": total_bytes, "test_files": tests,
            "languages": dict(sorted(languages.items(), key=lambda x: (-x[1], x[0]))),
            "manifests": manifests, "instructions": instruction_chain(root)}


def package_scripts(root: Path, directory: Path) -> dict[str, str]:
    p = directory / "package.json"
    if not p.is_file(): return {}
    try: data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return {}
    scripts = data.get("scripts", {})
    return scripts if isinstance(scripts, dict) else {}


def add_unique(checks: list[Check], item: Check) -> None:
    if item.command not in {x.command for x in checks}: checks.append(item)


def select_checks(root: Path, changes: list[str], *, full: bool = False) -> list[Check]:
    """Select narrow checks using only commands declared by the repository."""
    checks: list[Check] = []
    changed = [Path(x) for x in changes]
    suffixes = {p.suffix.lower() for p in changed}
    names = {p.name for p in changed}
    if is_repo(root):
        add_unique(checks, Check("diff-check", ("git", "diff", "--check"), "detect whitespace and conflict-marker errors"))
    if suffixes & {".py", ".pyi"} or names & {"pyproject.toml", "requirements.txt", "uv.lock"}:
        pyfiles = [str(p) for p in changed if p.suffix == ".py" and (root / p).is_file()]
        if pyfiles:
            add_unique(checks, Check("python-compile", (sys.executable, "-m", "py_compile", *pyfiles), "compile changed Python modules"))
        if (root / "scripts/run_tests.sh").is_file():
            tests = infer_python_tests(root, changed)
            if tests: add_unique(checks, Check("python-tests", ("scripts/run_tests.sh", *tests, "-q"), "run tests adjacent to changed Python code"))
        elif (root / "pytest.ini").is_file() or (root / "pyproject.toml").is_file():
            add_unique(checks, Check("python-tests", (sys.executable, "-m", "pytest", "-q"), "run Python tests", required=full))
    js_changed = bool(suffixes & {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"} or "package.json" in names)
    if js_changed:
        dirs = package_roots_for(root, changed)
        for directory in dirs:
            scripts = package_scripts(root, directory)
            prefix = ("npm", "--prefix", str(directory.relative_to(root) or Path(".")), "run")
            for script in ("typecheck", "lint", "test", "build"):
                if script in scripts and (full or script in {"typecheck", "test"}):
                    add_unique(checks, Check(f"npm-{script}:{directory.relative_to(root) or '.'}", (*prefix, script), f"declared {script} script for affected package"))
    if suffixes & {".rs"} or "Cargo.toml" in names:
        if (root / "Cargo.toml").is_file():
            add_unique(checks, Check("cargo-check", ("cargo", "check", "--workspace"), "type-check affected Rust workspace"))
            if full: add_unique(checks, Check("cargo-test", ("cargo", "test", "--workspace"), "test affected Rust workspace"))
    if suffixes & {".go"} or "go.mod" in names:
        add_unique(checks, Check("go-test", ("go", "test", "./..."), "test affected Go modules"))
    return checks


def infer_python_tests(root: Path, changed: list[Path]) -> list[str]:
    selected: set[str] = set()
    for p in changed:
        if p.suffix != ".py": continue
        if "tests" in p.parts and (root / p).is_file(): selected.add(p.as_posix()); continue
        stem = p.stem
        candidates = [Path("tests") / p.parent / f"test_{stem}.py", Path("tests") / f"test_{stem}.py"]
        for candidate in candidates:
            if (root / candidate).is_file(): selected.add(candidate.as_posix())
    return sorted(selected)


def package_roots_for(root: Path, changed: list[Path]) -> list[Path]:
    roots: set[Path] = set()
    for rel in changed:
        current = (root / rel).parent
        while current == root or root in current.parents:
            if (current / "package.json").is_file(): roots.add(current); break
            if current == root: break
            current = current.parent
    return sorted(roots)


def execute_checks(root: Path, checks: list[Check], timeout: int) -> list[Result]:
    results: list[Result] = []
    for check in checks:
        started = time.monotonic()
        try:
            cp = run(list(check.command), root, timeout=timeout)
            status = "passed" if cp.returncode == 0 else "failed"
            result = Result(check.name, list(check.command), check.reason, check.required, status,
                            cp.returncode, round(time.monotonic() - started, 3), cp.stdout[-20000:], cp.stderr[-20000:])
        except FileNotFoundError as exc:
            result = Result(check.name, list(check.command), check.reason, check.required, "unavailable", None,
                            round(time.monotonic() - started, 3), "", str(exc))
        except subprocess.TimeoutExpired as exc:
            result = Result(check.name, list(check.command), check.reason, check.required, "timeout", None,
                            round(time.monotonic() - started, 3), (exc.stdout or "")[-20000:], (exc.stderr or "")[-20000:])
        results.append(result)
    return results


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def cmd_scan(args: argparse.Namespace) -> int:
    payload = inventory(args.root)
    payload["changes"] = changed_files(args.root, args.base)
    print(json.dumps(payload, indent=2, sort_keys=True)); return 0


def cmd_plan(args: argparse.Namespace) -> int:
    changes = changed_files(args.root, args.base)
    checks = select_checks(args.root, changes, full=args.full)
    print(json.dumps({"changes": changes, "checks": [asdict(x) for x in checks]}, indent=2)); return 0


def cmd_verify(args: argparse.Namespace) -> int:
    started = time.time(); changes = changed_files(args.root, args.base)
    checks = select_checks(args.root, changes, full=args.full)
    results = execute_checks(args.root, checks, args.timeout)
    passed = all(r.status == "passed" for r in results if r.required)
    receipt = {"schema": 1, "started_at": started, "finished_at": time.time(), "passed": passed,
               "repository": inventory(args.root), "changes": changes, "results": [asdict(x) for x in results]}
    if args.output: atomic_json(args.output, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True)); return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=lambda x: Path(x).expanduser().resolve(), default=Path.cwd())
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in (("scan", cmd_scan), ("plan", cmd_plan), ("verify", cmd_verify)):
        s = sub.add_parser(name); s.set_defaults(fn=fn); s.add_argument("--base")
        if name in {"plan", "verify"}: s.add_argument("--full", action="store_true")
        if name == "verify":
            s.add_argument("--timeout", type=int, default=600)
            s.add_argument("--output", type=Path, default=Path(".shiva/forge-receipt.json"))
    return p


def main() -> None:
    args = build_parser().parse_args()
    try: raise SystemExit(args.fn(args))
    except subprocess.TimeoutExpired as exc:
        print(json.dumps({"error": f"command timed out: {exc.cmd}"}), file=sys.stderr); raise SystemExit(2)


if __name__ == "__main__": main()
