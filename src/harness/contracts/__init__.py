"""Pydantic v2 data contracts.

Two physically separated families of models live here:

* ``documents``  — the dossier the AGENT is allowed to see.
* ``truth``      — the ground truth used ONLY by the eval harness. The agent code
                   path structurally cannot import or receive these (see
                   ``harness.data.loader`` and the contract-separation test).
* ``findings``   — the agent's own outputs (findings, proposals, decisions).
"""

from harness.contracts.documents import (
    Dossier,
    RegistryDoc,
    Shareholder,
    SignatureCircular,
    Signatory,
    UboDeclaration,
    UboEntry,
)
from harness.contracts.findings import (
    AgentResult,
    CaseRecord,
    CheckResult,
    Finding,
    GuardrailDecision,
    SynthesisProposal,
)
from harness.contracts.truth import Case, DecisionTruth

__all__ = [
    "Dossier",
    "RegistryDoc",
    "Shareholder",
    "SignatureCircular",
    "Signatory",
    "UboDeclaration",
    "UboEntry",
    "AgentResult",
    "CaseRecord",
    "CheckResult",
    "Finding",
    "GuardrailDecision",
    "SynthesisProposal",
    "Case",
    "DecisionTruth",
]
