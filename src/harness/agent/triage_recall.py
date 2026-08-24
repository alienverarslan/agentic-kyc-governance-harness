"""Measure the AI triage layer's RECALL on the labeled anomaly corpus (Faz 4).

Complements ``triage_fpr`` (false positives on clean dossiers). Here every dossier is
deterministically clean but hides one out-of-coverage red flag; the triage layer should
catch it. We report:

* recall            — fraction of anomalies where triage raised >= 1 concern;
* miss_rate         — 1 - recall;
* per-type recall   — which anomaly categories it reliably catches vs misses;
* escalate share    — of caught anomalies, how many it (rightly) escalated.

Each case is first confirmed deterministically clean offline (so the deterministic layer
is NOT the one catching it). One triage call per case with the provided client.

Usage (from WSL, venv active):
    set -a; source .env; set +a
    python -m harness.agent.triage_recall --per-type 3
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from harness.agent import coverage, triage
from harness.agent.anomaly_corpus import build_anomaly_corpus
from harness.agent.guardrail import decision_for_severities
from harness.agent.runner import run_agent
from harness.contracts.findings import TriageResult
from harness.llm.base import LLMClient
from harness.llm.stub import PolicyMirrorStub
from harness.rules.schema import CandidateRule


def measure_recall(
    triage_client: LLMClient,
    per_type: int = 3,
    seed: int = 42,
    *,
    promoted_rules: list[CandidateRule] | None = None,
) -> dict[str, Any]:
    """Run triage over the anomaly corpus and aggregate recall / miss stats.

    ``promoted_rules`` defaults to whatever is currently promoted on disk (Faz 3) — a
    live re-measurement is supposed to reflect real deterministic coverage, so a case a
    promoted rule now catches correctly drops out of ``deterministically_clean`` (only
    the AI layer's job is measured here). Pass ``promoted_rules=[]`` for a hermetic
    measurement of the corpus's original baseline, independent of promotion state."""
    confirm = PolicyMirrorStub()
    corpus = build_anomaly_corpus(seed=seed, per_type=per_type)

    per_case: list[dict[str, Any]] = []
    for a in corpus:
        base = run_agent(a.dossier, confirm, case_ref=a.dossier.dossier_id, promoted_rules=promoted_rules)
        det_clean = base.decision == "approve" and not base.findings

        user = triage.build_triage_user(a.dossier, coverage.catalog_summary(promoted_rules))
        tr = triage_client.complete_structured(triage.TRIAGE_SYSTEM, user, TriageResult)
        findings = triage.triage_findings(tr)
        caught = len(findings) > 0
        induced = decision_for_severities([f.severity for f in findings]) if findings else "approve"
        per_case.append(
            {
                "case_id": a.dossier.dossier_id,
                "anomaly_type": a.anomaly_type,
                "detail": a.detail,
                "det_clean": det_clean,
                "expected_triage_concern": a.expected_triage_concern,
                "caught": caught,
                "induced_decision": induced,
                "concerns": [{"severity": f.severity, "detail": f.detail} for f in findings],
            }
        )

    # Recall's denominator is scoped to cases that are BOTH (a) labeled positive (the
    # corpus expects triage to catch something here) and (b) deterministically clean (the
    # only component that COULD catch it is triage). Today every corpus case satisfies (a)
    # by construction, so this filter is currently a no-op vs. det_clean alone — but it is
    # asserted explicitly (P5) so a future non-positive case in this corpus cannot silently
    # widen the eligible set and make a recall change misreadable as triage improving when
    # it was actually the labeled-positive population changing.
    labeled_positive = [c for c in per_case if c["expected_triage_concern"]]
    excluded_not_clean = [c for c in labeled_positive if not c["det_clean"]]
    eligible = [c for c in labeled_positive if c["det_clean"]]
    caught = [c for c in eligible if c["caught"]]
    escalated = [c for c in caught if c["induced_decision"] == "escalate"]

    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"caught": 0, "total": 0})
    for c in eligible:
        by_type[c["anomaly_type"]]["total"] += 1
        by_type[c["anomaly_type"]]["caught"] += int(c["caught"])
    for b in by_type.values():
        b["recall"] = b["caught"] / b["total"] if b["total"] else 0.0  # type: ignore[assignment]

    n_elig = len(eligible)
    return {
        "seed": seed,
        "per_type": per_type,
        "corpus_size": len(per_case),
        "deterministically_clean": n_elig,
        # Explicit eligibility breakdown (P5): makes the denominator's composition visible
        # rather than a single opaque count.
        "anomaly_cases_labeled_positive": len(labeled_positive),
        "anomaly_cases_excluded_not_deterministically_clean": len(excluded_not_clean),
        "triage_recall_eligible_cases": n_elig,
        # Precisely-named alias for the P5 report (the exact same value as "recall" below,
        # kept for backward compatibility with existing tests/tools): the metric is
        # conditioned on deterministic coverage staying fixed, and the name says so.
        "incremental_triage_recall_on_deterministically_clean_positives": (
            len(caught) / n_elig if n_elig else None
        ),
        "recall": len(caught) / n_elig if n_elig else 0.0,
        "miss_rate": 1 - (len(caught) / n_elig) if n_elig else 0.0,
        "escalate_of_caught": len(escalated),
        "recall_by_type": dict(by_type),
        "misses": [
            {"case_id": c["case_id"], "anomaly_type": c["anomaly_type"], "detail": c["detail"]}
            for c in eligible
            if not c["caught"]
        ],
        "not_det_clean": [
            {"case_id": c["case_id"], "anomaly_type": c["anomaly_type"]}
            for c in per_case
            if not c["det_clean"]
        ],
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    L = ["=" * 66, "TRIAGE RECALL MEASUREMENT (labeled anomaly corpus)", "=" * 66]
    L.append(f"corpus                    : {report['corpus_size']} (per_type={report['per_type']}, seed={report['seed']})")
    L.append(f"labeled positive          : {report['anomaly_cases_labeled_positive']}/{report['corpus_size']}")
    L.append(f"  excluded (not det-clean): {report['anomaly_cases_excluded_not_deterministically_clean']}")
    L.append(f"eligible (recall denom)   : {report['triage_recall_eligible_cases']}")
    L.append(f"deterministically clean   : {report['deterministically_clean']}/{report['corpus_size']}")
    if report["not_det_clean"]:
        L.append(f"  !! not-clean (excluded) : {[c['case_id'] for c in report['not_det_clean']]}")
    L.append(f"recall                    : {_pct(report['recall'])}"
             f"   miss_rate = {_pct(report['miss_rate'])}")
    L.append(f"escalated (of caught)     : {report['escalate_of_caught']}")
    L.append("")
    L.append("recall by anomaly type:")
    for atype, b in sorted(report["recall_by_type"].items()):
        L.append(f"    {atype:<32} {b['caught']}/{b['total']}  ({_pct(b['recall'])})")
    if report["misses"]:
        L.append("")
        L.append("MISSED anomalies (triage stayed silent):")
        for m in report["misses"]:
            L.append(f"    [{m['case_id']}] {m['anomaly_type']}: {m['detail']}")
    L.append("=" * 66)
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure triage recall on the anomaly corpus.")
    parser.add_argument("--per-type", type=int, default=3, help="variants per anomaly type (6 types)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/triage_recall.json")
    args = parser.parse_args()

    try:
        from harness.llm.factory import get_llm_client

        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001
        print(f"Live model unavailable ({exc}). Set ANTHROPIC_API_KEY and retry.")
        return

    report = measure_recall(client, per_type=args.per_type, seed=args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render(report))


if __name__ == "__main__":
    main()
