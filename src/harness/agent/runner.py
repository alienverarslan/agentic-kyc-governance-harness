"""``run_agent`` — the single entry point shared by the API and the CLI eval.

Both POST /screen and the batch evaluator call THIS function, so they exercise the
exact same graph, guardrail, and side effects.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from harness.agent.graph import build_graph
from harness.agent.state import AgentState
from harness.contracts.documents import Dossier
from harness.contracts.findings import AgentResult
from harness.llm.base import LLMClient
from harness.rules.schema import CandidateRule
from harness.rules.store import load_promoted_rules
from harness.store.case_store import CaseStore


@lru_cache(maxsize=1)
def _app():
    """Compile the graph once and reuse it across runs."""
    return build_graph()


def run_agent(
    dossier: Dossier | dict[str, Any],
    llm: LLMClient,
    *,
    store: CaseStore | None = None,
    case_ref: str | None = None,
    promoted_rules: list[CandidateRule] | None = None,
) -> AgentResult:
    """Screen one dossier and return the full auditable result.

    ``dossier`` may be a validated ``Dossier`` or raw JSON-like dict; either way the
    ``extract`` node re-parses it into contract models (so extraction is a real step).
    ``store`` defaults to an in-memory sink; pass a persistent one for durable effects.
    ``promoted_rules`` defaults to whatever is currently promoted on disk (Faz 3); pass
    an explicit list (e.g. ``[]``) to make a run hermetic regardless of that file's state.
    """
    if isinstance(dossier, Dossier):
        raw = dossier.model_dump(mode="json")
    else:
        raw = dict(dossier)

    if case_ref is None:
        case_ref = str(raw.get("dossier_id", "unknown"))
    if store is None:
        store = CaseStore()
    if promoted_rules is None:
        promoted_rules = load_promoted_rules()

    initial: AgentState = {
        "dossier_raw": raw,
        "case_ref": case_ref,
        "llm": llm,
        "store": store,
        "promoted_rules": promoted_rules,
        "trajectory": [],
        "skipped_checks": [],
        "check_results": [],
        "findings": [],
    }
    final = _app().invoke(initial)

    findings = final.get("findings", [])
    # Raw operational-failure counters (T-family). Rendering them into a reliability
    # report is deliberately left to the reporting milestone; these only make it possible.
    system_findings = [f for f in findings if f.origin == "system"]

    return AgentResult(
        case_ref=case_ref,
        decision=final["guardrail"].final_decision,
        trajectory=final.get("trajectory", []),
        skipped_checks=final.get("skipped_checks", []),
        guardrail=final["guardrail"],
        proposal=final["proposal"],
        triage=final.get("triage"),
        findings=findings,
        check_results=final.get("check_results", []),
        system_failure_count=len(system_findings),
        system_error_codes=[f.code for f in system_findings],
        extracted_dossier=final.get("dossier"),
    )
