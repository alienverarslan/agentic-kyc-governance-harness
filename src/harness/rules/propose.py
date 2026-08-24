"""CLI: ask the LLM to propose a rule for a recall gap, then gate it, then optionally
promote it. This is the full Faz 3 loop end to end (parts 2 -> 3 -> 4).

The LLM call needs a real client (ANTHROPIC_API_KEY); the gate and promotion steps that
follow are the same deterministic, offline machinery ``run_gate.py`` uses.

Usage (from WSL, venv active):
    set -a; source .env; set +a
    python -m harness.rules.propose --anomaly-type implausible_capital --rule-id capital-age-v1

    # after reviewing the proposal + gate report, promote it:
    python -m harness.rules.propose --anomaly-type implausible_capital --rule-id capital-age-v1 \\
        --promote --approved-by "Arslan"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.rules.gate import render as render_gate, run_validation_gate
from harness.rules.proposer import evidence_from_anomaly_type, propose_rule
from harness.rules.store import promote_rule


def _render_proposal(outcome) -> str:
    L = ["=" * 66, "RULE PROPOSAL (Faz 3, part 2)", "=" * 66]
    L.append(f"raw template_id : {outcome.raw_template_id!r}")
    L.append(f"raw params      : {outcome.raw_params}")
    L.append(f"structurally    : {'ACCEPTED' if outcome.accepted else 'REJECTED'}")
    L.append(f"reason          : {outcome.reason}")
    L.append("=" * 66)
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose, gate, and (optionally) promote a Faz 3 rule.")
    parser.add_argument("--anomaly-type", required=True, help="anomaly_corpus type to use as evidence")
    parser.add_argument("--rule-id", required=True)
    parser.add_argument("--per-type", type=int, default=3)
    parser.add_argument("--per-code", type=int, default=30, help="generated-corpus size for the gate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/rule_proposal.json")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--approved-by", default="")
    args = parser.parse_args()

    try:
        from harness.llm.factory import get_llm_client

        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001
        print(f"Live model unavailable ({exc}). Set ANTHROPIC_API_KEY and retry.")
        return

    evidence = evidence_from_anomaly_type(args.anomaly_type, per_type=args.per_type, seed=args.seed)
    outcome = propose_rule(client, args.rule_id, evidence, proposed_by="llm")
    print(_render_proposal(outcome))

    report: dict = {
        "anomaly_type": args.anomaly_type,
        "evidence": evidence,
        "raw_template_id": outcome.raw_template_id,
        "raw_params": outcome.raw_params,
        "structurally_accepted": outcome.accepted,
        "reason": outcome.reason,
    }

    if not outcome.accepted:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    gate_result = run_validation_gate(outcome.rule, per_code=args.per_code, seed=args.seed)
    print(render_gate(gate_result))
    report["gate"] = {
        "passed": gate_result.passed,
        "n_checked": gate_result.n_checked,
        "regressions": gate_result.regressions,
        "anomaly_preview": gate_result.anomaly_preview,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.promote:
        if not args.approved_by:
            print("\n--promote given without --approved-by; NOT promoting (human identifier required).")
            return
        if not gate_result.passed:
            print("\nGate did not pass; NOT promoting.")
            return
        promote_rule(outcome.rule, approved_by=args.approved_by, gate_report=gate_result)
        print(f"\nPromoted {outcome.rule.rule_id!r} (approved by {args.approved_by!r}).")


if __name__ == "__main__":
    main()
