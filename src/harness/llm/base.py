"""The single LLM abstraction the agent is allowed to depend on."""

from __future__ import annotations

from typing import Protocol, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    """A minimal, provider-agnostic structured-output interface.

    The agent makes exactly one kind of LLM call: given a system prompt and a user
    prompt, return an instance of a Pydantic ``schema`` validated from the model's
    output. Keeping the surface this small is what lets the stub stand in everywhere
    (so the whole test suite runs without an API key) and lets new providers plug in
    behind ``factory.get_llm_client``.
    """

    def complete_structured(self, system: str, user: str, schema: Type[T]) -> T:
        """Return a validated ``schema`` instance produced from ``system``/``user``."""
        ...
