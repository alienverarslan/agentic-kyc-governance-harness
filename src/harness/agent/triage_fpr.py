"""Measure the AI triage layer's FALSE-POSITIVE rate on genuinely clean dossiers (Faz 4).

The live demo showed 0/3 false positives, but 3 is an anecdote. This runs the triage
layer over many generator-produced CLEAN (NONE) dossiers and reports:

* false_positive_rate  — fraction of clean cases where triage raised >= 1 concern
                         (it should stay quiet on clean cases);
* over_escalation_rate — fraction pushed all the way to 'escalate' (the worst kind of
                         over-caution: a clean dossier sent to a human as if serious).

Each clean case is FIRST confirmed deterministically clean offline (free, via the stub),
then the triage layer is called ONCE per case with the provided client. When that client
is the real model this needs ANTHROPIC_API_KEY and costs ~n calls; when it is a stub the
whole thing is offline (used by the smoke test).

Usage (from WSL, venv active):
    set -a; source .env; set +a
    python -m harness.agent.triage_fpr --n 25
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness.agent import coverage, triage
from harness.agent.guardrail import decision_for_severities
from harness.agent.runner import run_agent
from harness.contracts.findings import TriageResult
from harness.generate.synthetic import generate_cases
from harness.llm.base import LLMClient
from harness.llm.stub import PolicyMirrorStub


def measure(triage_client: LLMClient, n: int = 25, seed: int = 42) -> dict[str, Any]:
    """Run triage over ``n`` clean (NONE) dossiers and aggregate false-positive stats."""
    confirm = PolicyMirrorStub()  # free, offline: confirms each case is deterministically clean
    cases = generate_cases(per_code=n, seed=seed, codes=["NONE"])

    per_case: list[dict[str, Any]] = []
    clean_confirmed = 0
    for gc in cases:
        base = run_agent(gc.case.dossier, confirm, case_ref=gc.case_id)
        det_clean = base.decision == "approve" and not base.findings
        clean_confirmed += int(det_clean)

        user = triage.build_triage_user(gc.case.dossier, coverage.catalog_summary())
        tr = triage_client.complete_structured(triage.TRIAGE_SYSTEM, user, TriageResult)
        findings = triage.triage_findings(tr)
        induced = decision_for_severities([f.severity for f in findings]) if findings else "approve"
        per_case.append(
            {
                "case_id": gc.case_id,
                "det_clean": det_clean,
                "n_concerns": len(findings),
                "induced_decision": induced,
                "concerns": [
                    {"severity": f.severity, "code": f.code, "detail": f.detail} for f in findings
                ],
            }
        )

    fp = [c for c in per_case if c["n_concerns"] > 0]
    esc = [c for c in per_case if c["induced_decision"] == "escalate"]
    rmi = [c for c in per_case if c["induced_decision"] == "request_more_info"]
    total_concerns = sum(c["n_concerns"] for c in per_case)

    return {
        "n": n,
        "seed": seed,
        "clean_confirmed": clean_confirmed,
        "false_positive_count": len(fp),
        "false_positive_rate": len(fp) / n if n else 0.0,
        "request_more_info_count": len(rmi),
        "escalate_count": len(esc),
        "over_escalation_rate": len(esc) / n if n else 0.0,
        "mean_concerns_per_case": total_concerns / n if n else 0.0,
        "false_positive_examples": [
            {"case_id": c["case_id"], "induced": c["induced_decision"], "concerns": c["concerns"]}
            for c in fp[:5]
        ],
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    L = ["=" * 66, "TRIAGE FALSE-POSITIVE MEASUREMENT (clean NONE dossiers)", "=" * 66]
    L.append(f"cases                     : {report['n']} (seed={report['seed']})")
    L.append(f"deterministically clean   : {report['clean_confirmed']}/{report['n']}")
    L.append(f"false_positive_rate       : {_pct(report['false_positive_rate'])}"
             f"  ({report['false_positive_count']}/{report['n']} raised >=1 concern)")
    L.append(f"  -> request_more_info     : {report['request_more_info_count']}")
    L.append(f"  -> escalate (worst)      : {report['escalate_count']}"
             f"   over_escalation_rate = {_pct(report['over_escalation_rate'])}")
    L.append(f"mean concerns per case    : {report['mean_concerns_per_case']:.2f}")
    if report["false_positive_examples"]:
        L.append("")
        L.append("sample false positives:")
        for ex in report["false_positive_examples"]:
            L.append(f"  [{ex['case_id']}] -> {ex['induced']}")
            for c in ex["concerns"]:
                L.append(f"     ({c['severity']}) {c['detail'][:140]}")
    L.append("=" * 66)
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure triage false-positive rate.")
    parser.add_argument("--n", type=int, default=25, help="number of clean cases (≈ n live calls)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/triage_fpr.json")
    args = parser.parse_args()

    try:
        from harness.llm.factory import get_llm_client

        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001
        print(f"Live model unavailable ({exc}). Set ANTHROPIC_API_KEY and retry.")
        return

    report = measure(client, n=args.n, seed=args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render(report))


if __name__ == "__main__":
    main()
