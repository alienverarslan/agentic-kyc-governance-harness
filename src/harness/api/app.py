"""FastAPI app exposing POST /screen.

The endpoint calls the exact same ``run_agent`` as the batch evaluator, so the HTTP
path and the CLI path can never diverge in behavior. It uses the configured live
provider when ANTHROPIC_API_KEY is present, and otherwise falls back to the offline
stub so the service is usable without a key.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from harness.agent.runner import run_agent
from harness.contracts.documents import Dossier
from harness.contracts.findings import AgentResult
from harness.llm.base import LLMClient
from harness.llm.stub import PolicyMirrorStub
from harness.store.case_store import CaseStore

app = FastAPI(
    title="doc-consistency-governance-harness",
    description=(
        "Governance and evaluation framework for action-taking LLM agents, demonstrated "
        "on cross-document consistency screening of corporate dossiers. Not a KYC product."
    ),
    version="0.1.0",
)

# A persistent store for the running service (side effects survive across requests).
_STORE = CaseStore(os.environ.get("HARNESS_DB", "harness_cases.db"))


def get_default_client() -> LLMClient:
    """Live provider if a key is configured; otherwise the offline stub."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from harness.llm.factory import get_llm_client

        return get_llm_client()
    return PolicyMirrorStub()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/screen", response_model=AgentResult)
def screen(dossier: Dossier) -> AgentResult:
    """Screen one dossier and return the full auditable result (decision + trajectory +
    guardrail + findings)."""
    client = get_default_client()
    return run_agent(dossier, client, store=_STORE, case_ref=dossier.dossier_id)
