"""Exception hierarchy for Trishula.

All errors derive from :class:`TrishulaError` so callers can catch the whole
subsystem with one ``except`` while still distinguishing causes.
"""

from __future__ import annotations


class TrishulaError(Exception):
    """Base class for every error raised by the trishula package."""


class ConfigError(TrishulaError):
    """Raised when configuration is missing, malformed or contradictory."""


class ToolError(TrishulaError):
    """Raised when a tool fails in a way the caller cannot act on.

    Recoverable tool failures (non-zero exits, missing files, permission
    problems) are returned as ``ToolResult(ok=False, ...)`` instead so an
    agentic loop can react. This exception is reserved for programming
    errors: unknown tool name, malformed arguments, a tool that violates its
    own contract.
    """


class SandboxError(TrishulaError):
    """Raised when a sandbox policy denies an action *before* it runs.

    Path escapes, denylisted commands, disallowed network access and quota
    breaches raise this.  It is distinct from :class:`ToolError` because a
    well-behaved agent should learn from the denial and re-plan rather than
    retry the same call.
    """


class EditError(TrishulaError):
    """Raised when an edit cannot be applied cleanly.

    Attach :attr:`old_string` and :attr:`path` so the caller (or the agent)
    can render a useful "the code drifted, re-read the file" message.
    """

    def __init__(self, message: str, path: str = "", old_string: str = ""):
        super().__init__(message)
        self.path = path
        self.old_string = old_string


class PlanningError(TrishulaError):
    """Raised when a goal cannot be compiled into an executable plan."""


class LLMError(TrishulaError):
    """Raised when a model call fails permanently (after retries).

    A *missing* model is not an error — callers fall back to deterministic
    mode.  This is only raised when a model was explicitly requested and
    could not deliver a well-formed response.
    """


class TeamError(TrishulaError):
    """Raised when team orchestration fails (bad DAG, dead worker, etc.)."""
