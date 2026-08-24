"""Agent output contracts: findings, the LLM proposal, the guardrail decision, and
the final auditable result.

Design commitment: the LLM PROPOSES, the deterministic guardrail DISPOSES. Findings
are produced deterministically by the check tools; ``SynthesisProposal`` is the only
LLM-authored object; ``GuardrailDecision`` is the authoritative final decision and
records every override.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from harness.contracts.documents import Dossier

# Taxonomy codes. See ``harness.agent.checks`` for the precise definition of each and
# ``harness.agent.guardrail`` for how severity maps to the final decision.
# D-family codes belong to check_ubo_derivation (added after slice-1): D1a = an
# at/above-threshold beneficial owner is missing from declared_ubo (incomplete
# declaration, explainable); D1b = a structurally impossible declaration (unexplainable).
# X-family codes belong to the AI triage layer (ai_triage): X1 = an out-of-coverage
# ("novel") concern the deterministic checks cannot verify, explainable → request_more_info;
# X2 = a serious out-of-coverage concern, unexplainable → escalate. The AI can only ever
# RAISE caution (X1/X2), never approve — see harness.agent.triage.
# F-family (Faz 3, harness.rules): F1 = a gate-validated, human-approved learned rule fired
# as its template intends — genuine domain evidence about the company.
# T-family (Milestone 2): OPERATIONAL failures, not domain evidence. T0 = an LLM call failed
# at a node boundary (ai_triage or synthesize); T1 = a promoted rule raised at runtime.
# Both are fail-closed → escalate, but they are reported separately from domain metrics: a
# timeout is not evidence about the company. T-findings always carry origin="system" and a
# bounded ``error_kind`` — the Finding validator enforces that correspondence.
# (F2 previously carried T1's meaning; it was retired in Milestone 2 because a rule-
# evaluation exception is an operational system failure, not domain evidence.)
TaxonomyCode = Literal[
    "A1", "A2", "B1a", "B1b", "B2a", "B2b", "C1a", "C1b", "C2",
    "D1a", "D1b", "E1", "E2", "X1", "X2", "F1", "T0", "T1", "NONE",
]

Severity = Literal["info", "explainable", "unexplainable"]

Decision = Literal["approve", "request_more_info", "escalate"]

# Which layer produced a finding. ``system`` marks an OPERATIONAL failure (the harness or a
# provider broke) as opposed to domain evidence about the company. Defaults to
# "deterministic" so every pre-Milestone-2 finding keeps its meaning without a migration.
Origin = Literal["deterministic", "ai_triage", "system"]

# The bounded, machine-readable vocabulary of operational failures. Bounded so that failure
# breakdowns can be aggregated later without a breaking change to T-findings.
# ``unexpected_exception`` is produced ONLY at a node boundary (never by an LLM client);
# ``rule_runtime_error`` only by the learned-rule node.
ErrorKind = Literal[
    "timeout",
    "provider_error",
    "schema_invalid",
    "unexpected_exception",
    "rule_runtime_error",
]

# The taxonomy codes that denote an operational failure rather than domain evidence.
SYSTEM_CODES: frozenset[str] = frozenset({"T0", "T1"})

# The only reasoning text an unavailable proposal may carry. Bounded on purpose: the raw
# exception / provider message must never reach an auditable record.
NO_PROPOSAL_REASONING = (
    "The synthesis call did not return a proposal; see the T0 system finding."
)


class Finding(BaseModel):
    """A single taxonomy-coded observation from one check.

    ``severity`` is load-bearing: the guardrail's decision is a pure function of the
    severities present across all findings (see the guardrail docstring).

    ``origin`` separates domain evidence from operational failure. The validator below
    makes that separation real rather than conventional — without it a semantically broken
    finding such as ``origin="deterministic", code="T0"`` would be constructible, and the
    claim "the domain/system split lives in origin" would be unenforced."""

    check_name: str
    code: TaxonomyCode
    severity: Severity
    detail: str
    fields_involved: list[str] = Field(default_factory=list)
    origin: Origin = "deterministic"
    error_kind: Optional[ErrorKind] = None

    @model_validator(mode="after")
    def _system_origin_matches_system_code(self) -> "Finding":
        is_system_code = self.code in SYSTEM_CODES
        is_system_origin = self.origin == "system"
        if is_system_code and not is_system_origin:
            raise ValueError(
                f"code {self.code!r} is a SYSTEM code and requires origin='system', "
                f"got origin={self.origin!r}"
            )
        if is_system_origin and not is_system_code:
            raise ValueError(
                f"origin='system' requires a SYSTEM code {sorted(SYSTEM_CODES)}, "
                f"got code={self.code!r}"
            )
        if (self.error_kind is not None) != is_system_origin:
            raise ValueError(
                "error_kind must be set if and only if origin='system' "
                f"(origin={self.origin!r}, error_kind={self.error_kind!r})"
            )
        return self


class CheckResult(BaseModel):
    """Result of one check node. ``ran=False`` means the check could NOT run because a
    required document was missing. A skipped check must never be reported as ran-clean;
    the eval harness treats that confusion as a trajectory failure."""

    check_name: str
    ran: bool
    findings: list[Finding] = Field(default_factory=list)


class SynthesisProposal(BaseModel):
    """The ONLY LLM-authored object in the pipeline. It is a PROPOSAL, not a decision:
    the guardrail may override it (and logs the override when it does).

    ``status`` exists because an LLM call can FAIL. When it does, the model produced no
    proposal at all, and the audit record must not imply otherwise. Fabricating a sentinel
    ``proposed_decision="escalate"`` would read as "the LLM proposed escalate", which is
    false; ``status="unavailable"`` says what actually happened. The validator enforces the
    two states cannot be mixed, and bounds the reasoning text an unavailable proposal may
    carry so a raw provider/exception message can never reach an auditable record."""

    status: Literal["available", "unavailable"] = "available"
    proposed_decision: Optional[Decision] = None
    reasoning: str = ""
    key_findings: list[str] = Field(default_factory=list)
    unavailable_reason: Optional[Literal["llm_call_failed"]] = None

    @model_validator(mode="after")
    def _status_is_consistent(self) -> "SynthesisProposal":
        if self.status == "available":
            if self.proposed_decision is None:
                raise ValueError("an available proposal must carry a proposed_decision")
            if self.unavailable_reason is not None:
                raise ValueError(
                    "an available proposal must not carry an unavailable_reason"
                )
        else:
            if self.proposed_decision is not None:
                raise ValueError(
                    "an unavailable proposal must not carry a proposed_decision "
                    "(the model produced none)"
                )
            if self.unavailable_reason is None:
                raise ValueError("an unavailable proposal must carry an unavailable_reason")
            if self.reasoning not in ("", NO_PROPOSAL_REASONING):
                raise ValueError(
                    "an unavailable proposal's reasoning must be empty or the bounded "
                    "NO_PROPOSAL_REASONING constant; raw provider/exception text is not "
                    "permitted in an auditable record"
                )
        return self


class NovelConcern(BaseModel):
    """One out-of-coverage concern raised by the AI triage layer.

    ``severity`` is advisory and is CLAMPED downstream to {explainable, unexplainable}
    (see ``harness.agent.triage``): the triage layer may only raise caution, never lower
    it, so a novel concern can never produce an approval."""

    detail: str
    fields_involved: list[str] = Field(default_factory=list)
    severity: str = "explainable"


class TriageResult(BaseModel):
    """The AI triage layer's output: concerns that fall OUTSIDE the deterministic checks'
    coverage. Empty means 'nothing outside coverage caught my eye'. A second LLM-authored
    object alongside ``SynthesisProposal``, but one that can only add caution."""

    novel_concerns: list[NovelConcern] = Field(default_factory=list)


class GuardrailDecision(BaseModel):
    """The authoritative final decision produced by the deterministic guardrail.

    ``finalization_mode`` records HOW the decision was reached. It matters because when the
    synthesis call fails there is no proposal to override, so ``overridden=False`` would
    otherwise be silently misreadable as "the LLM already agreed with the outcome"."""

    final_decision: Decision
    overridden: bool
    override_reason: str = ""
    required_actions: list[str] = Field(default_factory=list)
    finalization_mode: Literal["proposal_guarded", "fail_closed_no_proposal"] = (
        "proposal_guarded"
    )


class CaseRecord(BaseModel):
    """The side-effect record persisted by the ``act`` node (mirrored into SQLite)."""

    case_ref: str
    decision: Decision
    required_actions: list[str] = Field(default_factory=list)
    escalated: bool = False


class AgentResult(BaseModel):
    """The full auditable output of one screening run.

    ``trajectory`` is the ordered list of node names that ACTUALLY RAN — it is the
    record of which checks executed, appended live as the graph runs, never
    reconstructed after the fact. ``skipped_checks`` records checks that could not run.

    ``system_failure_count`` / ``system_error_codes`` are RAW counters for operational
    failures (T-family). Rendering them into a reliability report — rates, breakdowns,
    confidence intervals — is deliberately left to the reporting milestone; these fields
    only make it possible without another contract change.
    """

    case_ref: str
    decision: Decision
    trajectory: list[str] = Field(default_factory=list)
    skipped_checks: list[str] = Field(default_factory=list)
    guardrail: GuardrailDecision
    proposal: SynthesisProposal
    triage: Optional[TriageResult] = None
    findings: list[Finding] = Field(default_factory=list)
    check_results: list[CheckResult] = Field(default_factory=list)
    system_failure_count: int = 0
    system_error_codes: list[str] = Field(default_factory=list)
    # The extract node's own output, retained so extraction_accuracy compares AGENT
    # output against the loaded dossier (not the loaded dossier against itself).
    extracted_dossier: Optional[Dossier] = None
