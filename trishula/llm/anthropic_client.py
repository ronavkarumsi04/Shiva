"""Anthropic Messages API client (stdlib only)."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any

from trishula.core.errors import LLMError
from trishula.core.logging import get_logger
from trishula.core.types import Message, Role
from trishula.llm.base import LLMClient, LLMResponse

log = get_logger("llm.anthropic")


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        *,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com",
        timeout: float = 120.0,
        anthropic_version: str = "2023-06-01",
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.anthropic_version = anthropic_version
        if not self.api_key:
            raise LLMError("AnthropicClient requires ANTHROPIC_API_KEY")

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        system = "\n\n".join(
            m.content for m in messages if m.role == Role.SYSTEM and m.content
        )
        chat: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                continue
            if m.role == Role.TOOL:
                chat.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
            elif m.role == Role.ASSISTANT and m.tool_calls:
                chat.append(
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": m.content or ""}
                        ]
                        + [
                            {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": tc["name"],
                                "input": tc.get("arguments", {}),
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            else:
                chat.append({"role": m.role.value, "content": m.content})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": chat,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [self._convert_tool(t) for t in tools]

        data = self._post("/v1/messages", body)
        return self._parse(data)

    @staticmethod
    def _convert_tool(t: dict[str, Any]) -> dict[str, Any]:
        fn = t.get("function", t)
        return {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        }

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        content = data.get("content", [])
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": block.get("input", {}),
                    }
                )
        usage = data.get("usage", {})
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            raw=data,
            model=data.get("model", self.model),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        raw = json.dumps(body).encode("utf-8")
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }
        try:
            req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - network
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network
            raise LLMError(f"Network error calling {url}: {exc.reason}") from exc
