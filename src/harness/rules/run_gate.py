"""CLI: run the Faz 3 validation gate against a candidate rule, and optionally promote it.

Entirely offline — no LLM, no API key. This is the human-in-the-loop entry point: the gate
result is printed for review, and promotion only happens if the gate passed AND
``--promote --approved-by "<name>"`` was explicitly given.

Usage (from WSL, venv active):
    python -m harness.rules.run_gate --rule-id capital-age-v1 \\
        --template capital_age_ceiling --param max_age_days=180 --param max_capital=10000000

    # after reviewing the report, promote it:
    python -m harness.rules.run_gate --rule-id capital-age-v1 \\
        --template capital_age_ceiling --param max_age_days=180 --param max_capital=10000000 \\
        --promote --approved-by "Arslan"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.rules.gate import render, run_validation_gate
from harness.rules.schema import CandidateRule
from harness.rules.store import promote_rule


def _parse_param(item: str) -> tuple[str, float]:
    if "=" not in item:
        raise argparse.ArgumentTypeError(f"--param must be key=value, got {item!r}")
    key, raw_value = item.split("=", 1)
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--param {item!r}: value must be numeric") from exc
    return key, value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Faz 3 rule validation gate.")
    parser.add_argument("--rule-id", required=True)
    parser.add_argument("--template", required=True, dest="template_id")
    parser.add_argument("--param", action="append", default=[], help="key=value, repeatable")
    parser.add_argument("--rationale", default="")
    parser.add_argument("--proposed-by", default="human")
    parser.add_argument("--per-code", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/rule_gate_report.json")
    parser.add_argument("--promote", action="store_true", help="promote if the gate passes")
    parser.add_argument("--approved-by", default="", help="human identifier; required with --promote")
    args = parser.parse_args()

    params = dict(_parse_param(p) for p in args.param)
    rule = CandidateRule(
        rule_id=args.rule_id,
        template_id=args.template_id,
        params=params,
        rationale=args.rationale,
        proposed_by=args.proposed_by,
    )

    result = run_validation_gate(rule, per_code=args.per_code, seed=args.seed)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "rule": rule.model_dump(),
                "passed": result.passed,
                "param_errors": result.param_errors,
                "n_checked": result.n_checked,
                "regressions": result.regressions,
                "anomaly_preview": result.anomaly_preview,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(render(result))

    if args.promote:
        if not args.approved_by:
            print("\n--promote given without --approved-by; NOT promoting (human identifier required).")
            return
        if not result.passed:
            print("\nGate did not pass; NOT promoting.")
            return
        promote_rule(rule, approved_by=args.approved_by, gate_report=result)
        print(f"\nPromoted {rule.rule_id!r} (approved by {args.approved_by!r}).")


if __name__ == "__main__":
    main()
