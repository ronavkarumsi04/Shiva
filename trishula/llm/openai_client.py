"""OpenAI-compatible chat-completions client (stdlib only).

Works with any endpoint that speaks the OpenAI Chat Completions schema:
OpenAI itself, OpenRouter, Nous Portal, vLLM, llama.cpp server, LM Studio.

Uses ``urllib.request`` so the package needs zero pip installs; if ``httpx``
is present in the host environment it is used instead for connection
pooling and nicer timeout behaviour.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any

from trishula.core.errors import LLMError
from trishula.core.logging import get_logger
from trishula.core.types import Message
from trishula.llm.base import LLMClient, LLMResponse

log = get_logger("llm.openai")

try:  # optional acceleration only
    import httpx  # type: ignore

    _HAS_HTTPX = True
except Exception:  # noqa: BLE001
    _HAS_HTTPX = False


class OpenAIClient(LLMClient):
    name = "openai"

    def __init__(
        self,
        model: str,
        *,
        api_key: str = "",
        base_url: str = "",
        timeout: float = 120.0,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL", "")
            or os.environ.get("OPENROUTER_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise LLMError(
                "OpenAIClient requires an API key (OPENAI_API_KEY / OPENROUTER_API_KEY)"
            )

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        data = self._post("/chat/completions", body)
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Malformed completion response: {data!r}") from exc

        tool_calls: list[dict[str, Any]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args}
            )
        usage = data.get("usage", {})
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=tool_calls,
            raw=data,
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        raw = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            if _HAS_HTTPX:
                r = httpx.post(url, content=raw, headers=headers, timeout=self.timeout)
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code} from {url}: {r.text[:500]}")
                return r.json()
            req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - network
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network
            raise LLMError(f"Network error calling {url}: {exc.reason}") from exc
