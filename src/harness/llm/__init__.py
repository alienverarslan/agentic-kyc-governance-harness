"""Provider-agnostic LLM access.

Agent code depends ONLY on the ``LLMClient`` protocol in ``base``. Concrete backends
(Anthropic today; OpenAI/Vertex later) are selected by ``factory.get_llm_client`` and
are never imported directly by the agent.
"""

from harness.llm.base import LLMClient
from harness.llm.stub import (
    AdversarialStub,
    FixedDecisionStub,
    OvercautiousStub,
    PolicyMirrorStub,
    ScriptedTriageStub,
)

__all__ = [
    "LLMClient",
    "PolicyMirrorStub",
    "FixedDecisionStub",
    "AdversarialStub",
    "OvercautiousStub",
    "ScriptedTriageStub",
]
