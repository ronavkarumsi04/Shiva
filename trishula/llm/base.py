"""Model client protocol and shared response type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trishula.core.types import Message


@dataclass
class LLMResponse:
    """Normalized result of a model turn.

    ``tool_calls`` is a list of ``{"id", "name", "arguments": {...}}``.
    When the model answers in prose, ``content`` holds the text.
    """

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient:
    """Interface every backend implements.

    Subclasses override :meth:`complete`. The class is deliberately a base
    class with a clear error rather than a typing.Protocol so runtimes can
    ``isinstance``-check and extend it.
    """

    name: str = "base"

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        raise NotImplementedError

    # convenience
    def say(self, prompt: str, system: str = "", **kw: Any) -> str:
        msgs: list[Message] = []
        if system:
            msgs.append(Message.system(system))
        msgs.append(Message.user(prompt))
        return self.complete(msgs, **kw).content
