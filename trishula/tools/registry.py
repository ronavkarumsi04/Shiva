"""Tool registry: declare tools once, get validation, schemas and dispatch.

Design mirrors the best parts of Codex's tool layer and Claude Code's tool
contracts:

* tools are declared with a name, description, and a JSON-Schema-ish
  parameter spec;
* arguments are validated/coerced before the handler runs, so handlers trust
  their inputs;
* the same declarations emit (a) model-facing ``tools=[...]`` payloads for
  *both* OpenAI and Anthropic shapes and (b) human-readable help;
* handlers are plain python callables returning :class:`ToolResult`, a
  string, or a dict — the registry normalizes;
* results are journaled for the learning loop.
"""

from __future__ import annotations

import inspect
import time
from typing import Any, Callable, Iterable

from trishula.core.errors import ToolError
from trishula.core.logging import get_logger
from trishula.core.types import Journal, EventKind, Tool, ToolResult

log = get_logger("tools.registry")


class ToolRegistry:
    def __init__(self, *, journal: Journal | None = None):
        self._tools: dict[str, Tool] = {}
        self.journal: Journal | None = journal

    # ── registration ────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
        *,
        tags: tuple[str, ...] = (),
        dangerous: bool = False,
        read_only: bool = False,
    ) -> Tool:
        if name in self._tools:
            raise ToolError(f"tool {name!r} already registered")
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            tags=tags,
            dangerous=dangerous,
            read_only=read_only,
        )
        self._tools[name] = tool
        return tool

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"unknown tool {name!r}") from exc

    def names(self, *, tags: Iterable[str] | None = None) -> list[str]:
        if not tags:
            return sorted(self._tools)
        wanted = set(tags)
        return sorted(n for n, t in self._tools.items() if wanted & set(t.tags))

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # ── schemas for models ──────────────────────────────────────────────

    def schemas(self, *, tags: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return [self._tools[n].schema() for n in self.names(tags=tags)]

    def describe(self, name: str) -> str:
        t = self.get(name)
        props = t.parameters.get("properties", {})
        required = set(t.parameters.get("required", []))
        lines = [f"{name} — {t.description}", ""]
        for pname, pspec in props.items():
            star = "*" if pname in required else " "
            ptype = pspec.get("type", "any")
            desc = pspec.get("description", "")
            lines.append(f"  {star}{pname} ({ptype}): {desc}")
        return "\n".join(lines)

    def help_text(self) -> str:
        return "\n\n".join(self.describe(n) for n in self.names())

    # ── validation ──────────────────────────────────────────────────────

    def validate(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        spec = tool.parameters
        props: dict[str, dict[str, Any]] = spec.get("properties", {})
        required = spec.get("required", [])
        coerced: dict[str, Any] = {}

        for key in required:
            if key not in args:
                raise ToolError(f"tool {name}: missing required argument {key!r}")
        for key, value in args.items():
            if key not in props:
                # Pass through unknown kwargs only if the handler accepts **kwargs;
                # otherwise fail loudly — silent argument dropping is how loops
                # go infinite.
                if self._handler_takes_var_kw(tool.handler):
                    coerced[key] = value
                else:
                    raise ToolError(f"tool {name}: unexpected argument {key!r}")
                continue
            coerced[key] = self._coerce(name, key, value, props[key])

        # Apply defaults for absent optional args.
        for key, pspec in props.items():
            if key not in coerced and "default" in pspec:
                coerced[key] = pspec["default"]
        return coerced

    @staticmethod
    def _handler_takes_var_kw(handler: Callable[..., Any]) -> bool:
        try:
            return any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in inspect.signature(handler).parameters.values()
            )
        except (TypeError, ValueError):
            return True

    def _coerce(self, tool: str, key: str, value: Any, spec: dict[str, Any]) -> Any:
        ptype = spec.get("type")
        if ptype is None or isinstance(value, bool):
            return value
        try:
            if ptype == "string":
                return value if isinstance(value, str) else str(value)
            if ptype == "integer":
                return int(value)
            if ptype == "number":
                return float(value)
            if ptype == "boolean":
                if isinstance(value, bool):
                    return value
                return str(value).lower() in {"1", "true", "yes", "on"}
            if ptype == "array":
                if isinstance(value, list):
                    return value
                if isinstance(value, str):
                    return [v.strip() for v in value.split(",") if v.strip()]
                return [value]
            if ptype == "object":
                return value if isinstance(value, dict) else dict(value)
        except (ValueError, TypeError) as exc:
            raise ToolError(
                f"tool {tool}: argument {key!r} expected {ptype}, got {value!r}"
            ) from exc
        return value

    # ── dispatch ────────────────────────────────────────────────────────

    def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        start = time.monotonic()
        if self.journal:
            self.journal.emit(EventKind.TOOL_CALL, tool=name, args=_safe_args(args or {}))
        # Lookup/validation failures are recoverable agent mistakes — return
        # them as failed results so the agentic loop can react, not crash.
        try:
            tool = self.get(name)
            clean = self.validate(name, args or {})
        except ToolError as exc:
            result = ToolResult(ok=False, error=str(exc), tool=name)
            result.duration_ms = (time.monotonic() - start) * 1000
            if self.journal:
                self.journal.emit(
                    EventKind.TOOL_RESULT, tool=name, ok=False,
                    duration_ms=result.duration_ms, error=str(exc),
                )
            return result
        try:
            raw = tool.handler(**clean)
            result = self._normalize(raw, name)
        except Exception as exc:  # noqa: BLE001 - tool errors are results, not crashes
            log.warning("tool %s raised: %s", name, exc)
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}", tool=name)
        result.tool = name
        result.duration_ms = (time.monotonic() - start) * 1000
        if self.journal:
            self.journal.emit(
                EventKind.TOOL_RESULT,
                tool=name,
                ok=result.ok,
                duration_ms=result.duration_ms,
                error=result.error,
            )
        return result

    @staticmethod
    def _normalize(raw: Any, name: str) -> ToolResult:
        if isinstance(raw, ToolResult):
            return raw
        if isinstance(raw, dict):
            ok = raw.get("ok", True)
            return ToolResult(
                ok=bool(ok),
                output=str(raw.get("output", "")),
                error=str(raw.get("error", "")),
                data=raw.get("data", {k: v for k, v in raw.items() if k not in {"ok", "output", "error", "data"}}),
                tool=name,
            )
        if isinstance(raw, (list, tuple)):
            return ToolResult(ok=True, output="\n".join(str(x) for x in raw), tool=name)
        text = "" if raw is None else str(raw)
        return ToolResult(ok=True, output=text, tool=name)


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate huge argument values before they hit the journal."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 500:
            out[k] = v[:200] + f"...<{len(v)} chars>"
        else:
            out[k] = v
    return out
