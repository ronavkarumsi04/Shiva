"""Model access layer.

Trishula never imports a provider SDK directly; it talks to
:class:`~trishula.llm.base.LLMClient`. Three implementations matter:

* :class:`~trishula.llm.stub.StubClient` — deterministic, offline, used in
  tests and whenever no provider is configured. Engines must still produce
  useful behaviour with this client (rule-based plans, real verification);
* :class:`~trishula.llm.openai_client.OpenAIClient` — OpenAI/OpenRouter/
  Nous-Portal compatible chat-completions + tool-calling over httpx if it is
  installed, else stdlib urllib;
* :class:`~trishula.llm.anthropic_client.AnthropicClient` — Messages API.

:func:`get_client` builds the configured client or returns the stub.
"""

from trishula.llm.base import LLMClient, LLMResponse
from trishula.llm.stub import StubClient
from trishula.core.errors import LLMError

__all__ = ["LLMClient", "LLMResponse", "LLMError", "StubClient", "get_client"]


def get_client(config=None) -> LLMClient:  # noqa: ANN001
    """Build a model client from configuration.

    Resolution order:
      1. ``config.model_provider`` / ``TRISHULA_PROVIDER`` (``"stub"`` or
         empty => deterministic offline mode);
      2. the Shiva/OpenAI-style env vars (``OPENAI_API_KEY`` +
         ``OPENAI_BASE_URL``, ``OPENROUTER_API_KEY`` ...);
      3. ``ANTHROPIC_API_KEY``.
    """
    from trishula.llm.factory import build_client

    return build_client(config)
