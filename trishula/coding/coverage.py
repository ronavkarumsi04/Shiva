"""Statement coverage for changed files — stdlib only (no pytest-cov).

Runs the project's test files in a subprocess under ``trace.Trace`` and
reports, per target source file, how many executable statements were hit.
Executable lines come from ``ast`` (real statement nodes — blanks, comments
and docstrings don't count), so the number means the same as a line-based
coverage tool. Used by the verifier's Phase-2 feedback loop: uncovered
regions are fed back to the coding agent instead of being guessed at.
"""

from __future__ import annotations

import ast
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from trishula.core.logging import get_logger
from trishula.tools.shell import Shell

log = get_logger("coding.coverage")

_RUNNER = r"""
import json, os, runpy, sys, trace

targets = json.loads(sys.argv[1])
tests = json.loads(sys.argv[2])

sys.path.insert(0, os.getcwd())
# Ignore stdlib/site-packages via sys.prefix. The runner file itself is
# excluded from results because it is not in the target list — do NOT add its
# directory to ignoredirs, since it sits in the workspace alongside the
# sources under measurement.
tracer = trace.Trace(
    count=1, trace=0,
    ignoredirs=[sys.prefix, sys.exec_prefix],
)

def _run():
    for t in tests:
        try:
            runpy.run_path(t, run_name="__main__")
        except SystemExit:
            pass

tracer.runfunc(_run)
counts = tracer.results().counts

# map absolute path counts -> per-file executed lines
by_file: dict = {}
for (filename, lineno), n in counts.items():
    by_file.setdefault(os.path.abspath(filename), set()).add(lineno)

def statement_lines(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return set()
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines.add(node.lineno)
        if isinstance(node, ast.stmt) and hasattr(node, "lineno"):
            lines.add(node.lineno)
    return sorted(lines)

report = {}
for t in targets:
    ap = os.path.abspath(t)
    stmts = statement_lines(ap)
    executed = by_file.get(ap, set())
    report[t] = {
        "statements": len(stmts),
        "covered": len([l for l in stmts if l in executed]),
        "missing": [l for l in stmts if l not in executed][:60],
    }

print("__TRISHULA_COV__" + json.dumps(report))
"""


@dataclass
class CoverageReport:
    files: Dict[str, dict] = field(default_factory=dict)
    overall_pct: float = 0.0

    @property
    def uncovered(self) -> Dict[str, List[int]]:
        return {f: d["missing"] for f, d in self.files.items() if d["missing"]}

    def to_dict(self) -> dict:
        return {"overall_pct": round(self.overall_pct, 4), "files": self.files}


def measure_coverage(
    shell: Shell,
    target_files: List[str],
    test_files: List[str],
    *,
    timeout: int = 300,
) -> CoverageReport | None:
    """Run ``test_files`` and measure line coverage of ``target_files``.

    Returns ``None`` if there is nothing to measure (no targets / no tests).
    """
    target_files = [t for t in target_files if t.endswith(".py")]
    test_files = [t for t in test_files if t.endswith(".py")]
    if not target_files or not test_files:
        return None

    # The runner MUST live inside the workspace root: trace's ignoredirs uses
    # prefix matching against sys.prefix, and a runner placed under /tmp would
    # also cause files adjacent to /tmp to be dropped from counts.
    runner_name = f".trishula_cov_runner_{os.getpid()}.py"
    runner_rel = runner_name
    import shlex

    # Make trishula importable inside generated tests; pass the repo root via
    # the environment the scaffold looks for (TRISHULA_PYTHONPATH).
    tri_root = str(Path(__file__).resolve().parents[2])
    env_prefix = f"import os; os.environ.setdefault('TRISHULA_PYTHONPATH', {tri_root!r})\n"

    # Write the runner via the workspace root on disk (outside the sandbox
    # dir filter — it's a temp harness file, removed afterwards).
    runner_abs = Path(shell.root) / runner_rel
    runner_abs.write_text("import ast\n" + env_prefix + _RUNNER, encoding="utf-8")
    try:
        cmd = (
            f"python3 {shlex.quote(runner_rel)} {shlex.quote(json.dumps(target_files))} "
            f"{shlex.quote(json.dumps(test_files))}"
        )
        result = shell.run(cmd, timeout=timeout)
        for line in result.stdout.splitlines():
            if line.startswith("__TRISHULA_COV__"):
                data = json.loads(line[len("__TRISHULA_COV__"):])
                total = sum(d["statements"] for d in data.values())
                covered = sum(d["covered"] for d in data.values())
                pct = (covered / total) if total else 0.0
                return CoverageReport(files=data, overall_pct=pct)
        log.warning("coverage runner produced no report: %s", result.text()[-500:])
        return None
    finally:
        try:
            runner_abs.unlink(missing_ok=True)
        except OSError:
            pass
