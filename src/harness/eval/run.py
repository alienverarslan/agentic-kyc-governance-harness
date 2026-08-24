"""Batch evaluator CLI: ``python -m harness.eval.run``.

Runs the SAME ``run_agent`` used by the API over the 8 seed dossiers, scores every run
deterministically, and prints + writes the stratified report. Uses the offline stub LLM
by default so the run is reproducible and needs no API key; pass ``--live`` to use the
configured provider (requires ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.agent.runner import run_agent
from harness.data.loader import list_seed_ids, load_case
from harness.eval.metrics import build_report, evaluate_case
from harness.llm.base import LLMClient
from harness.llm.stub import PolicyMirrorStub
from harness.store.case_store import CaseStore


def _select_client(live: bool) -> tuple[LLMClient, str]:
    if live:
        from harness.llm.factory import get_llm_client

        return get_llm_client(), "live provider (LLM_PROVIDER)"
    return PolicyMirrorStub(), "offline PolicyMirrorStub"


def run_eval(client: LLMClient) -> dict:
    """Evaluate all seed cases with ``client`` and return the report dict."""
    store = CaseStore()  # in-memory: side effects still exercised, not persisted
    evals = []
    for case_id in list_seed_ids():
        case = load_case(case_id)  # EVAL path: dossier + truth
        # Agent receives ONLY the dossier (truth never passed).
        result = run_agent(case.dossier, client, store=store, case_ref=case_id)
        # extraction_accuracy compares the agent's OWN extract output vs the truth.
        agent_dossier = result.extracted_dossier or case.dossier
        evals.append(
            evaluate_case(case_id, result, case.decision_truth, agent_dossier, case.dossier)
        )
    return build_report(evals)


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_human(report: dict, label: str) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("doc-consistency-governance-harness — eval report")
    lines.append(f"LLM: {label}   cases: {report['n_cases']}")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"decision_accuracy_overall : {_fmt_pct(report['decision_accuracy_overall'])}")
    lines.append(f"finding_accuracy          : {_fmt_pct(report['finding_accuracy'])}")
    lines.append(f"trajectory_correctness    : {_fmt_pct(report['trajectory_correctness'])}")
    lines.append(f"extraction_accuracy_mean  : {_fmt_pct(report['extraction_accuracy_mean'])}")
    lines.append("")
    lines.append("*** FALSE APPROVAL RATE (catastrophic, headline) ***")
    lines.append(f"    false_approval_rate   : {_fmt_pct(report['false_approval_rate'])}")
    if report["false_approvals"]:
        lines.append(f"    false_approvals       : {report['false_approvals']}")
    lines.append("")
    mc = report["middle_class_fidelity"]
    lines.append(f"middle_class_fidelity     : {_fmt_pct(mc['accuracy'])}  [{mc['label']}]  cases={mc['cases']}")
    lines.append("")
    lines.append("decision accuracy by taxonomy code:")
    for code, b in sorted(report["decision_accuracy_by_taxonomy_code"].items()):
        lines.append(f"    {code:<6} {b['correct']}/{b['total']}  ({_fmt_pct(b['accuracy'])})")
    lines.append("")
    lines.append("decision accuracy by decision class:")
    for cls, b in sorted(report["decision_accuracy_by_decision_class"].items()):
        lines.append(f"    {cls:<18} {b['correct']}/{b['total']}  ({_fmt_pct(b['accuracy'])})")
    lines.append("")
    lines.append(f"guardrail_override_count  : {report['guardrail_override_count']}  {report['guardrail_overrides']}")
    lines.append("")
    lines.append("per-case:")
    header = f"    {'case':<9}{'inject':<8}{'agent_codes':<14}{'exp→agent':<40}{'dec':<4}{'find':<5}{'traj':<5}"
    lines.append(header)
    for c in report["per_case"]:
        arrow = f"{c['expected_decision']}→{c['agent_decision']}"
        lines.append(
            f"    {c['case_id']:<9}{'+'.join(c['injected_codes']):<8}"
            f"{','.join(c['agent_codes']):<14}{arrow:<40}"
            f"{'ok' if c['decision_match'] else 'X':<4}"
            f"{'ok' if c['finding_match'] else 'X':<5}"
            f"{'ok' if c['trajectory_match'] else 'X':<5}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic eval harness.")
    parser.add_argument("--live", action="store_true", help="use the configured LLM provider")
    parser.add_argument("--out", default="artifacts", help="output directory for reports")
    args = parser.parse_args()

    client, label = _select_client(args.live)
    report = run_eval(client)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    human = render_human(report, label)
    (out_dir / "eval_report.txt").write_text(human, encoding="utf-8")
    print(human)


if __name__ == "__main__":
    main()
