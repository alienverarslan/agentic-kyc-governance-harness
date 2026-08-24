"""Faz 3, part 2: the LLM-assisted rule proposer.

The structural contract this whole module exists to enforce: the LLM may ONLY choose an
EXISTING ``template_id`` from ``TEMPLATE_REGISTRY`` and fill in that template's numeric
``params`` — it cannot author a new template, cannot set a taxonomy code or severity (those
belong to the template), and cannot supply anything resembling code. ``RuleProposal`` (the
LLM's output schema) has exactly two fields for that reason: ``template_id`` and ``params``.

A proposal is advisory only and carries NO authority of its own:
* an unknown ``template_id`` is REJECTED here (the proposer is not permitted to invent one);
* out-of-bounds/missing ``params`` are REJECTED here (the same ``validate_params`` fence
  the rest of ``harness.rules`` uses);
* even a STRUCTURALLY accepted proposal is just a ``CandidateRule`` — it still must pass
  ``harness.rules.gate.run_validation_gate`` (zero regressions on the 428 known cases)
  before a human can promote it via ``harness.rules.store.promote_rule``. Nothing in this
  module can make a rule effective.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from harness.llm.base import LLMClient
from harness.rules.schema import TEMPLATE_REGISTRY, CandidateRule, validate_params

PROPOSER_SYSTEM = (
    "You are a RULE-PROPOSING assistant for a deterministic compliance engine. You do NOT "
    "write code and you do NOT invent new rule types. You may ONLY select one 'template_id' "
    "from the 'available_templates' list you are given, and fill in ITS numeric parameters "
    "within the stated [min, max] bounds. Nothing else is settable: no taxonomy code, no "
    "severity, no free-form logic — those are fixed by the template.\n\n"
    "You are shown 'missed_anomaly_evidence': short descriptions of dossiers that a "
    "downstream open-world AI reviewer failed to flag, representing a detection gap a "
    "deterministic rule might close. Propose the template + parameters that would have "
    "caught this evidence WITHOUT being so aggressive it would plausibly misfire on a normal, "
    "clean company. If NONE of the available templates fit this evidence, return an empty "
    "template_id and an empty params object — do not force a bad fit.\n\n"
    "Your proposal has no authority on its own: it will be checked against hundreds of "
    "known-good cases by a separate deterministic gate, and a human must approve it before "
    "it ever takes effect. Return a RuleProposal."
)


class RuleProposal(BaseModel):
    """The ONLY thing the LLM may author: a template choice + its numeric params."""

    template_id: str = ""
    params: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""


def build_proposer_user(evidence: list[str]) -> str:
    """The proposer prompt payload: every registered template's contract + the evidence."""
    templates_desc = {
        tid: {
            "description": t.description,
            "params": {
                name: {"min": spec.minimum, "max": spec.maximum, "kind": spec.kind}
                for name, spec in t.param_specs.items()
            },
        }
        for tid, t in TEMPLATE_REGISTRY.items()
    }
    return json.dumps(
        {"available_templates": templates_desc, "missed_anomaly_evidence": evidence},
        ensure_ascii=False,
        indent=2,
    )


def evidence_from_anomaly_type(anomaly_type: str, *, per_type: int = 3, seed: int = 42) -> list[str]:
    """Convenience: the detail strings of one anomaly-corpus type, as proposer evidence."""
    from harness.agent.anomaly_corpus import build_anomaly_corpus

    return [c.detail for c in build_anomaly_corpus(seed=seed, per_type=per_type) if c.anomaly_type == anomaly_type]


@dataclass
class ProposalOutcome:
    """Result of one proposal attempt. ``accepted`` means only "a structurally valid
    instantiation of a known template" — NOT "safe to promote"; the gate decides that."""

    accepted: bool
    rule: CandidateRule | None
    reason: str
    raw_template_id: str = ""
    raw_params: dict[str, float] = field(default_factory=dict)


def propose_rule(
    llm: LLMClient, rule_id: str, evidence: list[str], *, proposed_by: str = "llm"
) -> ProposalOutcome:
    """Ask ``llm`` for a rule proposal and structurally validate it (never trust it raw)."""
    user = build_proposer_user(evidence)
    proposal = llm.complete_structured(PROPOSER_SYSTEM, user, RuleProposal)
    raw_params = dict(proposal.params)

    if not proposal.template_id:
        return ProposalOutcome(
            accepted=False,
            rule=None,
            reason="proposer declined: no existing template fits the evidence",
            raw_template_id="",
            raw_params=raw_params,
        )

    template = TEMPLATE_REGISTRY.get(proposal.template_id)
    if template is None:
        return ProposalOutcome(
            accepted=False,
            rule=None,
            reason=(
                f"proposer named an unknown template {proposal.template_id!r} "
                "(the proposer is not permitted to invent one)"
            ),
            raw_template_id=proposal.template_id,
            raw_params=raw_params,
        )

    errors = validate_params(template, proposal.params)
    if errors:
        return ProposalOutcome(
            accepted=False,
            rule=None,
            reason=f"proposed params invalid: {errors}",
            raw_template_id=proposal.template_id,
            raw_params=raw_params,
        )

    rule = CandidateRule(
        rule_id=rule_id,
        template_id=proposal.template_id,
        params=raw_params,
        rationale=proposal.rationale,
        proposed_by=proposed_by,
    )
    return ProposalOutcome(
        accepted=True,
        rule=rule,
        reason=proposal.rationale or "structurally accepted; still subject to the validation gate",
        raw_template_id=proposal.template_id,
        raw_params=raw_params,
    )
