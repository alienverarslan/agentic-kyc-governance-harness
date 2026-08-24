"""P4 — eval-only contracts for the frozen out-of-coverage red-team corpus.

These models are used ONLY by the eval/freeze path, never by the agent — the exact
separation the seed and holdout corpora already have. The agent-input loader path returns
a bare ``Dossier`` and structurally cannot surface a ``RedTeamCase``/``RedTeamLabel``.

Why a P4-specific label rather than reusing ``DecisionTruth`` (see docs/p4_design.md):
P4 has a genuinely different contract. A holdout ``DecisionTruth`` is an expectation about
what the harness *should* do on an in-coverage case. A red-team ``RedTeamLabel`` is a
human-approved threat-model JUDGMENT about the appropriate action on an out-of-coverage
concern — **not** externally-validated ground truth, and never described as one. Reusing
``DecisionTruth`` would conflate the two, so the schema is separate even though both live
under ``contracts/``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from harness.contracts.documents import Dossier

# The primary red-team corpus admits no "approve": a genuinely-approvable case has no place
# in a false-approval denominator and belongs to a separate negative-control surface.
AppropriateAction = Literal["request_more_info", "escalate"]

# Truthful about how the label was produced. NOT "human_threat_model" (that would repeat the
# P2 provenance error of describing LLM-assisted authoring as human-only).
ExternalDependency = Literal["none", "pinned_rule_parameter", "pinned_library_behavior"]


class RedTeamLabel(BaseModel):
    """A human-reviewed threat-model judgment about the appropriate action on one
    out-of-coverage concern. Not externally-validated ground truth."""

    model_config = ConfigDict(extra="forbid")

    appropriate_action: AppropriateAction
    # The provenance triple is stated explicitly per case (required, not silently defaulted),
    # so the record can never overclaim how a label was produced.
    label_basis: Literal["threat_model_judgment"]
    label_review: Literal["human_approved"]
    authoring_assistance: Literal["llm_assisted", "human_only"]
    # The exact dossier fact that independently supports the action label (no external
    # mutable lookup, no name->activity inference, no address==operating-site assumption).
    self_contained_evidence: str
    # Which deterministic check's declared scope this concern falls outside of.
    out_of_coverage_rationale: str
    external_dependency: ExternalDependency

    @field_validator("self_contained_evidence", "out_of_coverage_rationale")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty, self-contained statement")
        return v


class RedTeamCase(BaseModel):
    """One frozen red-team case: the dossier PLUS its structured label. Only the eval/freeze
    path ever holds one of these; the agent-input path returns ``dossier`` alone."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: Literal["R1", "R2", "R3", "R4", "R5", "R6"]
    dossier: Dossier
    label: RedTeamLabel
