"""Ground-truth contracts — used ONLY by the eval harness, NEVER by the agent.

These models are physically separated from the dossier the agent receives. The loader
exposes two disjoint paths (``load_dossier`` vs ``load_case``); the agent-facing path
returns a ``Dossier`` and structurally cannot surface a ``Case``/``DecisionTruth``.
This separation is asserted by ``tests/test_contract_separation.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from harness.contracts.documents import Dossier
from harness.contracts.findings import Decision


class DecisionTruth(BaseModel):
    """The labelled expectation for one seed case."""

    expected_decision: Decision
    expected_trajectory: list[str] = Field(default_factory=list)
    expected_skipped_checks: list[str] = Field(default_factory=list)
    injected_codes: list[str] = Field(default_factory=list)
    rationale: str = ""


class Case(BaseModel):
    """A seed case: the dossier PLUS its ground truth. Only the eval harness ever holds
    one of these. ``Case.dossier`` is the exact same object the agent would receive, so
    the parsed dossier doubles as field-level extraction truth (see
    ``extraction_accuracy``)."""

    dossier: Dossier
    decision_truth: DecisionTruth
