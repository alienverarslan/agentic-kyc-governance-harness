"""Anthropic backend for ``LLMClient``.

Pinned to a single model at temperature 0 for reproducibility. The SDK is imported
lazily inside ``__init__`` so that importing this module never requires the ``anthropic``
package or an API key to be present — the offline test suite never constructs this
class.

This module is also the ONLY place that knows Anthropic's exception classes. It normalizes
the provider's KNOWN failures into the provider-agnostic ``LLMError`` so the agent layer
can stay backend-free (see ``llm/errors.py``). It deliberately does not wrap everything:
an unexpected error here is a harness bug, and the node boundary classifies it as
``unexpected_exception`` rather than mislabelling it a provider failure.
"""

from __future__ import annotations

import json
import os
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from harness.llm.errors import LLMError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicClient:
    """Structured-output client backed by the Anthropic Messages API + tool use."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        """``timeout`` and ``max_retries`` are OPTIONAL transport settings forwarded to the
        SDK client only when explicitly supplied. Omitting both (the default) constructs
        ``anthropic.Anthropic(api_key=...)`` exactly as before, so every existing caller
        keeps the SDK's own defaults and its current behavior unchanged. A caller that
        needs a bounded, self-owned retry policy — P4(c)'s live-triage measurement is the
        first — passes ``max_retries=0`` so the SDK adds no invisible attempts beneath it,
        plus a per-request ``timeout``. Both are passed with an explicit ``is not None``
        test, so ``max_retries=0`` and ``timeout=0.0`` are forwarded rather than swallowed
        as falsy."""
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Use a stub client for offline runs, or "
                "export the key for a live run."
            )
        # Lazy import: keeps offline paths free of the anthropic dependency.
        import anthropic

        client_kwargs: dict[str, object] = {"api_key": api_key}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self._client = anthropic.Anthropic(**client_kwargs)
        self._model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._temperature = temperature
        self._max_tokens = max_tokens

    def complete_structured(self, system: str, user: str, schema: Type[T]) -> T:
        """Force the model to emit ``schema`` via a single-tool tool_choice.

        Known provider and structured-output failures are normalized to ``LLMError``:

        * ``APITimeoutError``  -> timeout        (it subclasses APIConnectionError, so it
                                                  MUST be caught first)
        * any other ``APIError`` -> provider_error (covers connection, 4xx/5xx, rate limit,
                                                  auth, overloaded — all of APIStatusError)
        * ``ValidationError`` / ``JSONDecodeError`` -> schema_invalid

        Anything else propagates untouched, by design.
        """
        import anthropic

        tool_name = "emit_" + schema.__name__.lower()
        tool = {
            "name": tool_name,
            "description": f"Return a valid {schema.__name__}.",
            "input_schema": schema.model_json_schema(),
        }
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APITimeoutError as exc:
            raise LLMError("timeout", cause=exc) from exc
        except anthropic.APIError as exc:
            raise LLMError("provider_error", cause=exc) from exc

        try:
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                    return schema.model_validate(block.input)
            # Fallback: some responses may return JSON text.
            text = "".join(getattr(b, "text", "") for b in response.content)
            return schema.model_validate(json.loads(text))
        except (ValidationError, json.JSONDecodeError) as exc:
            raise LLMError("schema_invalid", cause=exc) from exc
