"""Client selection: config/env -> concrete LLMClient."""

from __future__ import annotations

import os
from typing import Any

from trishula.core.errors import LLMError
from trishula.core.logging import get_logger
from trishula.llm.base import LLMClient
from trishula.llm.stub import StubClient

log = get_logger("llm.factory")


def build_client(config: Any = None) -> LLMClient:
    provider = ""
    model = ""
    if config is not None:
        provider = getattr(config, "model_provider", "") or ""
        model = getattr(config, "model", "") or ""
    provider = os.environ.get("TRISHULA_PROVIDER", provider)
    model = os.environ.get("TRISHULA_MODEL", model)

    if not provider or provider in {"stub", "none", "offline", "deterministic"}:
        return StubClient()

    if provider in {"openai", "openrouter", "nous", "openai-compatible"}:
        from trishula.llm.openai_client import OpenAIClient

        base_url = os.environ.get("OPENAI_BASE_URL", "")
        if provider == "openrouter":
            base_url = base_url or "https://openrouter.ai/api/v1"
        elif provider == "nous":
            base_url = base_url or "https://router.nousresearch.com/v1"
        if not model:
            model = os.environ.get("TRISHULA_MODEL", "gpt-4o-mini")
        return OpenAIClient(model=model, base_url=base_url or None)

    if provider == "anthropic":
        from trishula.llm.anthropic_client import AnthropicClient

        if not model:
            model = os.environ.get("TRISHULA_MODEL", "claude-sonnet-4-20250514")
        return AnthropicClient(model=model)

    raise LLMError(f"Unknown model provider: {provider!r}")
