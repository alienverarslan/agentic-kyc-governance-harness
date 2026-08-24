"""Provider selection. Agent code obtains a client here and never names a backend."""

from __future__ import annotations

import os

from harness.llm.base import LLMClient


def get_llm_client() -> LLMClient:
    """Return an ``LLMClient`` for the provider named by ``LLM_PROVIDER`` (default
    "anthropic"). Any other value raises a clear ``NotImplementedError`` marking where
    additional backends plug in."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()

    if provider == "anthropic":
        from harness.llm.anthropic_client import AnthropicClient

        return AnthropicClient()

    # --- future backends plug in here -------------------------------------------
    # if provider == "openai":
    #     from harness.llm.openai_client import OpenAIClient
    #     return OpenAIClient()
    # if provider == "vertex":
    #     from harness.llm.vertex_client import VertexClient
    #     return VertexClient()
    raise NotImplementedError(
        f"LLM_PROVIDER={provider!r} is not implemented in slice-1. "
        "Only 'anthropic' is available; see harness/llm/factory.py for the plug-in "
        "points for 'openai' / 'vertex'."
    )
