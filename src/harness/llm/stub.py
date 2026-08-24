"""Deterministic stand-in LLM clients (no network, no API key).

These implement the ``LLMClient`` protocol so the ENTIRE pipeline — graph, API, and
eval — runs offline and reproducibly. There are now TWO structured LLM calls in the
graph: ``ai_triage`` (open-world concern discovery) and ``synthesize`` (the decision
proposal). Every stub answers both, dispatching on the requested schema.

* ``PolicyMirrorStub`` — a competent analyst: raises NO novel concerns and proposes the
  decision the policy would reach. With it the guardrail never needs to override, which
  is the expected behaviour on the clean seed/generated sets.
* ``FixedDecisionStub`` / ``AdversarialStub`` / ``OvercautiousStub`` — always propose a
  fixed decision (used to exercise the guardrail's override paths).
* ``ScriptedTriageStub`` — raises a preset list of novel concerns (used to test the
  fail-closed wiring of the AI triage layer deterministically).
* ``ScriptedProposerStub`` — answers ``RuleProposal`` (Faz 3's rule-proposer schema) with a
  preset template_id/params, so ``harness.rules.proposer.propose_rule`` is testable offline.
  Deliberately NOT a ``_StubBase`` subclass: the proposer is a standalone tool invoked
  outside the screening graph, never asked for ``TriageResult``/``SynthesisProposal``.
"""

from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel

from harness.agent.guardrail import decision_for_severities
from harness.agent.triage import _coerce_concerns
from harness.contracts.findings import (
    Decision,
    NovelConcern,
    SynthesisProposal,
    TriageResult,
)

T = TypeVar("T", bound=BaseModel)


def _extract_severities(user: str) -> tuple[list[str], list[str]]:
    """Return (severities, codes) parsed from the synthesize JSON payload."""
    payload = json.loads(user)
    severities: list[str] = []
    codes: list[str] = []
    for check in payload.get("check_results", []):
        for finding in check.get("findings", []):
            severities.append(finding["severity"])
            codes.append(finding["code"])
    return severities, codes


class _StubBase:
    """Dispatches structured calls on schema. Subclasses override ``_synth``; the default
    ``_triage`` raises no concerns."""

    def _triage(self, user: str) -> TriageResult:
        return TriageResult(novel_concerns=[])

    def _synth(self, user: str) -> SynthesisProposal:  # pragma: no cover - overridden
        raise NotImplementedError

    def complete_structured(self, system: str, user: str, schema: Type[T]) -> T:
        if schema is TriageResult:
            return self._triage(user)  # type: ignore[return-value]
        if schema is SynthesisProposal:
            return self._synth(user)  # type: ignore[return-value]
        raise AssertionError(f"stub received unexpected schema: {schema!r}")


class PolicyMirrorStub(_StubBase):
    """Proposes the decision a well-calibrated agent would, derived from findings; raises
    no novel concerns."""

    def _synth(self, user: str) -> SynthesisProposal:
        severities, codes = _extract_severities(user)
        decision = decision_for_severities(severities)
        key = [c for c in codes if c != "NONE"] or ["NONE"]
        return SynthesisProposal(
            proposed_decision=decision,
            reasoning=(
                "Stub synthesis: proposed decision derived from finding severities "
                f"{sorted(set(severities)) or ['none']}."
            ),
            key_findings=key,
        )


class FixedDecisionStub(_StubBase):
    """Always proposes ``decision`` — used to force guardrail override paths in tests."""

    def __init__(self, decision: Decision) -> None:
        self._decision = decision

    def _synth(self, user: str) -> SynthesisProposal:
        _severities, codes = _extract_severities(user)
        return SynthesisProposal(
            proposed_decision=self._decision,
            reasoning=f"Fixed stub: always proposes {self._decision}.",
            key_findings=[c for c in codes if c != "NONE"] or ["NONE"],
        )


class AdversarialStub(FixedDecisionStub):
    """A maximally LAX proposer: always proposes ``approve``, even over unexplainable
    findings. Used to prove the guardrail catches an under-cautious agent at scale."""

    def __init__(self) -> None:
        super().__init__("approve")


class OvercautiousStub(FixedDecisionStub):
    """A maximally CAUTIOUS proposer: always proposes ``escalate``. Used to prove the
    guardrail overrides over-caution DOWN to the correct decision."""

    def __init__(self) -> None:
        super().__init__("escalate")


class ScriptedTriageStub(PolicyMirrorStub):
    """Raises a fixed set of novel concerns from the triage call (and otherwise mirrors
    policy for synthesis). Used to test the AI triage layer's fail-closed wiring without
    a real model. ``concerns`` items may be NovelConcern, dicts, or (detail, severity)
    tuples."""

    def __init__(self, concerns: list) -> None:
        self._concerns: list[NovelConcern] = _coerce_concerns(concerns)

    def _triage(self, user: str) -> TriageResult:
        return TriageResult(novel_concerns=list(self._concerns))


class ScriptedProposerStub:
    """Always answers Faz 3's ``RuleProposal`` schema with a fixed template_id/params.
    Does not implement the graph's two schemas — the proposer is invoked standalone."""

    def __init__(self, template_id: str = "", params: dict | None = None, rationale: str = "stub") -> None:
        self._template_id = template_id
        self._params = dict(params or {})
        self._rationale = rationale

    def complete_structured(self, system: str, user: str, schema: Type[T]) -> T:
        from harness.rules.proposer import RuleProposal

        if schema is not RuleProposal:
            raise AssertionError(f"ScriptedProposerStub received unexpected schema: {schema!r}")
        return RuleProposal(  # type: ignore[return-value]
            template_id=self._template_id, params=self._params, rationale=self._rationale
        )
