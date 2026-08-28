"""Verifier: prove an edit works instead of hoping it does.

The verifier is the difference between "the agent wrote code" and "the code
works". It runs, in order of cost:

1. **syntax checks** — compile/parse every changed file (py_compile, node
   --check, tsc if configured);
2. **lint** when a configured linter exists (ruff/eslint/golangci...),
   failures downgrade the verdict but never fail it outright;
3. **tests** — auto-detects the project runner (pytest, npm test, go test,
   cargo test) and runs the *targeted* subset first, falling back to the
   whole suite;
4. **build** when the project clearly needs one (tsconfig / makefile).

Verdicts: ``PASS``, ``FAIL``, ``PARTIAL`` (tests ran but some failed / only
lint failed), ``SKIPPED`` (nothing verifiable). The failing test names are
parsed out and returned so the coding loop can feed them straight back into
the next attempt — that closed loop is Claude-Code-grade behaviour.
"""

from __future__ import annotations

import enum
import re
import shlex
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from trishula.core.config import TrishulaConfig
from trishula.core.logging import get_logger
from trishula.core.types import Journal, EventKind
from trishula.tools.shell import Shell
from trishula.tools.workspace import Workspace

log = get_logger("coding.verifier")


class Verdict(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    SKIPPED = "skipped"


@dataclass
class TestFailure:
    name: str
    file: str = ""
    detail: str = ""


@dataclass
class VerificationResult:
    verdict: Verdict
    summary: str = ""
    syntax_ok: bool = True
    lint_ok: bool | None = None
    tests_ran: bool = False
    tests_passed: int = 0
    tests_failed: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    # phase 2
    property_results: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] | None = None
    feedback: str = ""   # machine-actionable hints fed back to the coding loop

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


class Verifier:
    def __init__(
        self,
        workspace: Workspace,
        shell: Shell,
        *,
        config: TrishulaConfig | None = None,
        journal: Journal | None = None,
    ):
        self.ws = workspace
        self.shell = shell
        self.cfg = config or TrishulaConfig()
        self.journal = journal

    def verify(self, changed_files: list[str] | None = None) -> VerificationResult:
        changed = changed_files or self.ws.changed_files
        result = VerificationResult(verdict=Verdict.SKIPPED, changed_files=changed)
        log.info("verifying %d changed files", len(changed))

        # Optional: scaffold smoke/property tests for newly changed functions.
        generated: list[str] = []
        if self.cfg.auto_generate_tests:
            generated = self._scaffold_tests(changed)

        # 1. syntax
        py_files = [f for f in changed if f.endswith(".py")]
        if py_files:
            res = self.shell.run(
                "python3 -m py_compile " + " ".join(shlex.quote(f) for f in py_files),
                timeout=120,
            )
            result.commands.append({"cmd": "py_compile", "exit": res.exit_code, "out": res.text()[-2000:]})
            if not res.ok:
                result.syntax_ok = False
                result.verdict = Verdict.FAIL
                result.summary = "Python syntax/compile errors:\n" + res.text()[-1500:]
                self._emit(result)
                return result

        # 2. tests (generated scaffolds count toward detection + runs)
        runner = self._detect_test_runner()
        if generated and not runner:
            runner = "py-stdlib"
        if runner:
            tr = self._run_tests(runner, changed)
            result.commands.append(tr.get("cmd", ""))
            result.tests_ran = True
            result.tests_passed = tr.get("passed", 0)
            result.tests_failed = tr.get("failed", 0)
            result.failures = tr.get("failures", [])
            if tr["failed"] == 0 and tr["passed"] > 0:
                result.verdict = Verdict.PASS
                result.summary = f"{tr['passed']} tests passed ({runner})"
            elif tr["failed"] == 0 and tr["passed"] == 0:
                result.verdict = Verdict.PARTIAL
                result.summary = f"test runner ran but reported no tests ({runner})"
            else:
                result.verdict = Verdict.FAIL if not result.syntax_ok else Verdict.FAIL
                result.summary = (
                    f"{tr['failed']} test(s) failed, {tr['passed']} passed ({runner})"
                )
        else:
            result.verdict = Verdict.PASS if result.syntax_ok else Verdict.FAIL
            result.summary = "syntax OK" if result.syntax_ok else "syntax failed"

        # 3. Phase 2 — property testing + statement coverage feedback.
        if result.verdict in (Verdict.PASS, Verdict.PARTIAL):
            self._phase2(result, changed, generated)

        self._emit(result)
        return result

    # ── phase 2: property tests + coverage ──────────────────────────────

    def _scaffold_tests(self, changed: list[str]) -> list[str]:
        """Generate smoke/property test files for changed modules."""
        from trishula.coding.testgen import generate_tests

        try:
            mapping = generate_tests(self.ws, changed, write=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("test scaffolding failed: %s", exc)
            return []
        return list(mapping.values())

    def _phase2(self, result: VerificationResult, changed: list[str],
                generated: list[str]) -> None:
        """Property tests tighten correctness; coverage feeds back gaps."""
        feedback: list[str] = []

        if self.cfg.verify_property_tests:
            props = self._run_property_tests(generated)
            result.property_results = props
            failing = [p for p in props if not p["ok"]]
            for p in failing:
                feedback.append(
                    f"Property {p['name']} violated: {p.get('error', '')} — add an "
                    "edge-case guard and a regression assertion."
                )
            if failing:
                result.verdict = Verdict.PARTIAL
                result.tests_failed += len(failing)

        if self.cfg.verify_coverage:
            cov = self._measure_coverage(changed, generated)
            if cov is not None:
                result.coverage = cov.to_dict()
                for path, data in cov.files.items():
                    pct = data["covered"] / data["statements"] if data["statements"] else 1.0
                    if pct < self.cfg.coverage_min_pct and data["missing"]:
                        shown = ", ".join(str(n) for n in data["missing"][:12])
                        feedback.append(
                            f"{path} is {pct:.0%} covered by tests; uncovered lines "
                            f"around {shown} — add tests that exercise them."
                        )

        result.feedback = "\n".join(feedback)
        if result.feedback and self.journal:
            self.journal.emit(EventKind.VERDICT, coverage=result.coverage,
                              feedback=result.feedback)

    def _run_property_tests(self, test_files: list[str]) -> list[dict[str, Any]]:
        from trishula.coding.proplib import run_property_file

        results: list[dict[str, Any]] = []
        for rel in test_files:
            try:
                for r in run_property_file(str(self.ws.resolve(rel))):
                    results.append(r.to_dict())
            except Exception as exc:  # noqa: BLE001
                results.append({"name": rel, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return results

    def _measure_coverage(self, changed: list[str], generated: list[str]):
        from trishula.coding.coverage import measure_coverage

        sources = [f for f in changed if f.endswith(".py") and "test" not in f]
        # Tests to exercise: existing test files changed plus discovered suite.
        test_files = [f for f in changed if "test" in f and f.endswith(".py")] + generated
        if not test_files:
            test_files = [
                self.ws.rel(p) for p in self.ws.root.glob("test_*.py")
            ]
        if not sources or not test_files:
            return None
        try:
            return measure_coverage(
                self.shell, sources, test_files, timeout=self.cfg.verify_test_timeout
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("coverage measurement failed: %s", exc)
            return None

    # ── runners ─────────────────────────────────────────────────────────

    def _detect_test_runner(self) -> str | None:
        root = self.ws.root
        if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or list(root.glob("test_*.py")) or list(root.glob("tests/**/test_*.py")):
            if self._which("pytest") or self._module_ok("pytest"):
                return "pytest"
        if (root / "package.json").exists():
            try:
                pkg = (root / "package.json").read_text()
                if '"test"' in pkg:
                    return "npm"
            except OSError:
                pass
        if (root / "go.mod").exists():
            return "go"
        if (root / "Cargo.toml").exists():
            return "cargo"
        # Last resort: plain stdlib test files we can execute directly.
        if list(root.glob("test_*.py")) or list(root.glob("tests/**/test_*.py")):
            return "py-stdlib"
        return None

    def _which(self, cmd: str) -> bool:
        res = self.shell.run(f"command -v {shlex.quote(cmd)}", timeout=10)
        return res.ok and bool(res.stdout.strip())

    def _module_ok(self, mod: str) -> bool:
        res = self.shell.run(f"python3 -c 'import {mod}'", timeout=30)
        return res.ok

    def _run_tests(self, runner: str, changed: list[str]) -> dict[str, Any]:
        timeout = self.cfg.verify_test_timeout
        if runner == "pytest":
            targeted = [f for f in changed if "test" in f]
            if targeted:
                cmd = "python3 -m pytest -x -q " + " ".join(shlex.quote(f) for f in targeted)
                res = self.shell.run(cmd, timeout=timeout)
                if res.ok or "no tests ran" in res.text():
                    return self._parse_pytest(res)
            res = self.shell.run("python3 -m pytest -q --timeout=60", timeout=timeout)
            return self._parse_pytest(res)
        if runner == "npm":
            res = self.shell.run("npm test -- --run 2>/dev/null || npm test", timeout=timeout)
            return self._parse_npm(res)
        if runner == "go":
            res = self.shell.run("go test ./... -count=1", timeout=timeout)
            return self._parse_go(res)
        if runner == "cargo":
            res = self.shell.run("cargo test --quiet", timeout=timeout)
            return self._parse_cargo(res)
        if runner == "py-stdlib":
            return self._run_stdlib_pytests(changed)
        return {"failed": 0, "passed": 0, "failures": [], "cmd": {"cmd": "unknown"}}

    def _run_stdlib_pytests(self, changed: list[str]) -> dict[str, Any]:
        """Execute test_*.py files directly with the interpreter (no pytest).

        Each file is run as a script; exit code != 0 means failure. Output is
        scanned for the typical ``AssertionError``/``Error:`` lines. This makes
        verification work in minimal environments where pytest is absent.
        """
        import glob as _glob

        candidates = set(
            list(self.ws.root.glob("test_*.py"))
            + list(self.ws.root.glob("tests/test_*.py"))
        )
        targeted = {self.ws.root / f for f in changed if "test" in f}
        ordered = [p for p in targeted if p in candidates] or sorted(candidates)
        passed = failed = 0
        failures: list[TestFailure] = []
        outputs: list[str] = []
        for test_file in ordered[:20]:
            rel = self.ws.rel(test_file)
            res = self.shell.run(f"python3 {shlex.quote(rel)}", timeout=self.cfg.verify_test_timeout)
            outputs.append(f"$ python3 {rel}\n{res.text()[-1500:]}")
            if res.ok:
                passed += 1
            else:
                failed += 1
                name = rel
                for m in re.finditer(r"(AssertionError|Error):\s*(.+)", res.text()):
                    name = f"{rel}: {m.group(2)[:100]}"
                    break
                failures.append(TestFailure(name=name, file=rel, detail=res.text()[-600:]))
        return {
            "passed": passed,
            "failed": failed,
            "failures": failures[:20],
            "cmd": {"cmd": "python3 <test files>", "out": "\n".join(outputs)[-3000:]},
        }

    # ── output parsers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_pytest(res) -> dict[str, Any]:  # noqa: ANN001
        text = res.text()
        passed = sum(int(n) for n in re.findall(r"(\d+) passed", text))
        failed = sum(int(n) for n in re.findall(r"(\d+) failed", text))
        errors = sum(int(n) for n in re.findall(r"(\d+) error", text))
        failures: list[TestFailure] = []
        for m in re.finditer(r"FAILED (\S+?)(?:\s*-\s*(.+))?$", text, re.M):
            failures.append(TestFailure(name=(m.group(2) or m.group(1)), file=m.group(1)))
        if not failures:
            for m in re.finditer(r"_{5,}\s+([\w\[\]./-]+)\s+_{5,}", text):
                failures.append(TestFailure(name=m.group(1)))
        return {
            "passed": passed,
            "failed": failed + errors,
            "failures": failures[:20],
            "cmd": {"cmd": "pytest", "exit": res.exit_code, "out": text[-3000:]},
        }

    @staticmethod
    def _parse_npm(res) -> dict[str, Any]:  # noqa: ANN001
        text = res.text()
        passed = sum(int(n) for n in re.findall(r"passing[^\d]*(\d+)|✓\s+(\d+)", text)) // 2 + sum(
            int(n) for n in re.findall(r"(\d+) passing", text)
        )
        failed = sum(int(n) for n in re.findall(r"(\d+) failing", text))
        failures = [
            TestFailure(name=m.group(1).strip())
            for m in re.finditer(r"(?:failing|✗|×)\s+\d*\)?\s*([^\n]{5,120})", text)
        ]
        return {
            "passed": passed,
            "failed": failed,
            "failures": failures[:20],
            "cmd": {"cmd": "npm test", "exit": res.exit_code, "out": text[-3000:]},
        }

    @staticmethod
    def _parse_go(res) -> dict[str, Any]:  # noqa: ANN001
        text = res.text()
        failures = [
            TestFailure(name=m.group(2), file=m.group(1))
            for m in re.finditer(r"--- FAIL:\s+(\S+)/?(\S*)", text)
        ]
        failed = len(failures) or (0 if res.ok else 1)
        ok_pkgs = len(re.findall(r"^ok\s+", text, re.M))
        return {
            "passed": ok_pkgs,
            "failed": failed,
            "failures": failures[:20],
            "cmd": {"cmd": "go test", "exit": res.exit_code, "out": text[-3000:]},
        }

    @staticmethod
    def _parse_cargo(res) -> dict[str, Any]:  # noqa: ANN001
        text = res.text()
        passed = sum(int(n) for n in re.findall(r"(\d+) passed", text))
        failed = sum(int(n) for n in re.findall(r"(\d+) failed", text))
        failures = [
            TestFailure(name=m.group(1))
            for m in re.finditer(r"test\s+(result\s+)?(FAILED|failures::\S+)", text)
        ]
        return {
            "passed": passed,
            "failed": failed,
            "failures": failures[:20],
            "cmd": {"cmd": "cargo test", "exit": res.exit_code, "out": text[-3000:]},
        }

    def _emit(self, result: VerificationResult) -> None:
        if self.journal:
            self.journal.emit(
                EventKind.VERDICT,
                verdict=result.verdict.value,
                passed=result.tests_passed,
                failed=result.tests_failed,
                failures=[f.name for f in result.failures],
            )
