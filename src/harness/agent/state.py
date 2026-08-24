"""The LangGraph state object threaded through every node.

``trajectory`` and ``skipped_checks`` are the audit backbone: nodes append to them AS
THEY RUN, so the record reflects what actually executed rather than a post-hoc
reconstruction. Non-serializable helpers (the llm client, the store) ride along in
state for the in-process ``invoke`` used here.
"""

from __future__ import annotations

from typing import Any, TypedDict

from harness.agent.checks import EntityMap
from harness.contracts.documents import Dossier
from harness.contracts.findings import (
    CheckResult,
    Finding,
    GuardrailDecision,
    SynthesisProposal,
    TriageResult,
)
from harness.llm.base import LLMClient
from harness.rules.schema import CandidateRule
from harness.store.case_store import CaseStore


class AgentState(TypedDict, total=False):
    # inputs
    dossier_raw: dict[str, Any]
    case_ref: str
    llm: LLMClient
    store: CaseStore
    promoted_rules: list[CandidateRule]
    # produced as the graph runs
    dossier: Dossier
    emap: EntityMap
    check_results: list[CheckResult]
    findings: list[Finding]
    trajectory: list[str]
    skipped_checks: list[str]
    triage: TriageResult
    proposal: SynthesisProposal
    guardrail: GuardrailDecision
