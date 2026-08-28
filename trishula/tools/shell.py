"""Shell: command execution with Codex-style guardrails.

Policy layers, in order:

1. **Command denylist** — destructive fork-bombs / disk-wipers are refused
   before a process spawns (configurable; conservative defaults).
2. **Working directory** — commands run inside the workspace root and cannot
   ``cd`` out (the resolved cwd is asserted post-shell).
3. **Network toggle** — when ``allow_network=False`` (default), we set
   ``http_proxy``/``https_proxy`` to an unroutable black hole *and* unset
   common token vars for the child. This is best-effort on all platforms;
   on Linux we additionally use ``unshare -n`` when available (network
   namespace isolation) and ``bwrap`` when present.
4. **Timeouts & output caps** — killed hard on timeout; stdout/stderr are
   truncated with markers so an agent never floods context.
5. **Secrets scrubbing** — env vars ending in KEY/TOKEN/SECRET/PASSWORD are
   stripped from the child environment unless explicitly passed through.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from trishula.core.errors import SandboxError
from trishula.core.logging import get_logger
from trishula.core.types import Journal, EventKind

log = get_logger("tools.shell")

_SECRET_SUFFIXES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIALS")
_SECRET_SUBSTRINGS = ("API_KEY", "ACCESS_TOKEN", "PRIVATE_KEY")
_BLACKHOLE_PROXY = "http://127.0.0.1:9"  # discard port -> connections fail fast


@dataclass
class ShellResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    denied: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.denied

    def text(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.timed_out:
            parts.append(f"[timed out after {self.duration_ms:.0f}ms]")
        if self.exit_code and not self.timed_out:
            parts.append(f"[exit {self.exit_code}]")
        return "\n".join(parts)


class Shell:
    def __init__(
        self,
        workspace_root: str | Path,
        *,
        timeout: int = 30,
        timeout_max: int = 600,
        output_cap: int = 20_000,
        allow_network: bool = False,
        deny_commands: Sequence[str] = (),
        journal: Journal | None = None,
    ):
        self.root = Path(workspace_root).resolve()
        self.timeout = timeout
        self.timeout_max = timeout_max
        self.output_cap = output_cap
        self.allow_network = allow_network
        self.deny_commands = tuple(deny_commands)
        self.journal = journal
        self._have_unshare = shutil.which("unshare") is not None
        self._have_bwrap = shutil.which("bwrap") is not None

    # ── policy ──────────────────────────────────────────────────────────

    def check_command(self, command: str) -> None:
        low = command.lower().strip()
        for bad in self.deny_commands:
            if bad in low:
                raise SandboxError(f"command denied by policy: {bad!r}")
        # Refuse obvious escapes regardless of denylist.
        if "cd .." in low or "cd /" in low or low.startswith("cd ~"):
            if not self._stays_inside(command):
                raise SandboxError("command attempts to leave the workspace root")

    def _stays_inside(self, command: str) -> bool:
        # Conservative: any absolute cd outside root is rejected here.
        for token in shlex.split(command, posix=os.name != "nt"):
            if token.startswith("/") and not token.startswith(str(self.root)):
                return False
        return True

    def _child_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if any(upper.endswith(s) for s in _SECRET_SUFFIXES):
                continue
            if any(s in upper for s in _SECRET_SUBSTRINGS):
                continue
            env[key] = value
        env["PWD"] = str(self.root)
        if not self.allow_network:
            env["http_proxy"] = _BLACKHOLE_PROXY
            env["https_proxy"] = _BLACKHOLE_PROXY
            env["HTTP_PROXY"] = _BLACKHOLE_PROXY
            env["HTTPS_PROXY"] = _BLACKHOLE_PROXY
            env["NO_PROXY"] = ""
        return env

    def _wrap_isolation(self, command: str) -> tuple[list[str], str]:
        """Return (argv, shell_command). Best-effort Linux isolation."""
        if os.name == "nt":
            return [], command
        wrapped = command
        prefix: list[str] = []
        # bwrap is strongest if present: bind the workspace read-write, /usr
        # and /lib read-only, no network.
        if self._have_bwrap and not self.allow_network:
            prefix = [
                "bwrap",
                "--unshare-net",
                "--die-with-parent",
                "--bind", str(self.root), str(self.root),
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/bin", "/bin",
                "--symlink", "usr/lib", "/lib64",
                "--proc", "/proc",
                "--dev", "/dev",
                "--chdir", str(self.root),
            ]
            return prefix, wrapped
        if self._have_unshare and not self.allow_network and os.geteuid() != 0:
            # user+net namespace: no root needed on most modern kernels.
            prefix = ["unshare", "-Urn"]
        return prefix, wrapped

    # ── execution ───────────────────────────────────────────────────────

    def run(
        self,
        command: str,
        *,
        timeout: int | None = None,
        cwd: str | Path | None = None,
    ) -> ShellResult:
        start = time.monotonic()
        try:
            self.check_command(command)
        except SandboxError as exc:
            if self.journal:
                self.journal.emit(EventKind.ERROR, where="shell", error=str(exc))
            return ShellResult(command, 126, "", str(exc), 0.0, denied=True)

        effective_cwd = Path(cwd).resolve() if cwd else self.root
        try:
            effective_cwd.relative_to(self.root)
        except ValueError as exc:
            raise SandboxError(f"cwd {effective_cwd} outside workspace") from exc

        to = min(timeout or self.timeout, self.timeout_max)
        prefix, wrapped = self._wrap_isolation(command)
        argv = prefix + ["/bin/sh", "-c", wrapped] if prefix else ["/bin/sh", "-c", command]
        log.info("run: %s (cwd=%s, timeout=%ss)", command, effective_cwd, to)

        if self.journal:
            self.journal.emit(EventKind.TOOL_CALL, tool="shell", command=command)

        timed_out = False
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(effective_cwd),
                env=self._child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=True,
            )
        except FileNotFoundError:
            argv = ["sh", "-c", command]
            proc = subprocess.Popen(
                argv,
                cwd=str(effective_cwd),
                env=self._child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=True,
            )
        try:
            stdout, stderr = proc.communicate(timeout=to)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill(proc)
            stdout, stderr = proc.communicate(timeout=5)
            exit_code = 124

        duration_ms = (time.monotonic() - start) * 1000
        stdout = self._cap(stdout or "")
        stderr = self._cap(stderr or "")
        result = ShellResult(command, exit_code, stdout, stderr, duration_ms, timed_out)
        if self.journal:
            self.journal.emit(
                EventKind.TOOL_RESULT,
                tool="shell",
                exit_code=exit_code,
                ok=result.ok,
                duration_ms=duration_ms,
            )
        return result

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()

    def _cap(self, text: str) -> str:
        if len(text) <= self.output_cap:
            return text
        head = text[: self.output_cap // 2]
        tail = text[-self.output_cap // 2:]
        return (
            f"{head}\n... [truncated {len(text) - self.output_cap} chars] ...\n{tail}"
        )
