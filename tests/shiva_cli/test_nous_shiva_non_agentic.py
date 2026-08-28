"""Tests for the Nous-Shiva-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"shiva"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``shiva-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "shiva" tag namespace.

``is_nous_shiva_non_agentic`` should only match the actual Nous Research
Shiva-3 / Shiva-4 chat family.
"""

from __future__ import annotations

import pytest

from shiva_cli.model_switch import (
    _SHIVA_MODEL_WARNING,
    _check_shiva_model_warning,
    is_nous_shiva_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/Shiva-3-Llama-3.1-70B",
        "NousResearch/Shiva-3-Llama-3.1-405B",
        "shiva-3",
        "Shiva-3",
        "shiva-4",
        "shiva-4-405b",
        "shiva_4_70b",
        "openrouter/shiva3:70b",
        "openrouter/nousresearch/shiva-4-405b",
        "NousResearch/Shiva3",
        "shiva-3.1",
    ],
)
def test_matches_real_nous_shiva_chat_models(model_name: str) -> None:
    assert is_nous_shiva_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Shiva 3/4"
    )
    assert _check_shiva_model_warning(model_name) == _SHIVA_MODEL_WARNING


