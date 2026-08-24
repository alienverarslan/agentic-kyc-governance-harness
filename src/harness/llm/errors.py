"""Provider-agnostic LLM failure type.

Why this exists: the agent layer must never name a backend (see ``llm/factory.py``), and
the ``anthropic`` SDK is imported lazily inside ``AnthropicClient`` so offline paths never
require the package. The graph therefore cannot catch ``anthropic.APITimeoutError``
directly — doing so would both leak a provider name into the agent layer and make the
offline test suite depend on the SDK. Instead each client translates the provider's KNOWN
failures into ``LLMError`` with a bounded kind, and the node boundary catches that.

Deliberately NOT a catch-all. A client wraps only known external / model-output failures.
An unexpected exception — say a ``KeyError`` in our own client code — must propagate and be
classified as ``unexpected_exception`` at the node boundary; labelling it ``provider_error``
would hide a harness bug behind an LLM-failure story.
"""

from __future__ import annotations

from harness.contracts.findings import ErrorKind

# The kinds a client may raise. The other two members of ErrorKind are produced elsewhere:
# ``unexpected_exception`` at a node boundary, ``rule_runtime_error`` by the learned-rule
# node. Keeping this explicit is what stops a client from becoming a catch-all.
CLIENT_ERROR_KINDS: frozenset[str] = frozenset({"timeout", "provider_error", "schema_invalid"})


class LLMError(Exception):
    """A KNOWN LLM call failure carrying a bounded, machine-readable kind.

    The original exception is preserved as ``__cause__`` for internal logging only. It must
    never be rendered into a user-facing finding: a provider message can carry endpoints,
    response bodies, or prompt content.
    """

    def __init__(self, kind: ErrorKind, *, cause: BaseException | None = None) -> None:
        super().__init__(f"LLM call failed: {kind}")
        self.kind: ErrorKind = kind
        if cause is not None:
            self.__cause__ = cause
