"""Generate synthetic dossiers at scale, screen them, and report — deterministically.

Three passes, all offline:

* MIRROR       — a well-calibrated proposer (PolicyMirrorStub). Everything should score
                 perfectly; this is statistical proof of correctness (hundreds of cases,
                 not 8).
* ADVERSARIAL  — a maximally LAX proposer (always approve). The catastrophic test: even
                 when the LLM tries to wave everything through, the deterministic
                 guardrail must keep false_approval_rate = 0. Override count will be high
                 — that is the guardrail doing its job.
* OVERCAUTIOUS — a maximally CAUTIOUS proposer (always escalate). The guardrail must pull
                 over-caution back down to the correct decision.

Entry: ``python -m harness.generate.run`` (``--per-code``, ``--seed``, ``--dump DIR``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.agent.runner import run_agent
from harness.eval.metrics import build_report, evaluate_case
from harness.generate.synthetic import GeneratedCase, generate_cases
from harness.llm.base import LLMClient
from harness.llm.stub import AdversarialStub, OvercautiousStub, PolicyMirrorStub


def _screen_pass(cases: list[GeneratedCase], client: LLMClient) -> dict:
    """Run one LLM proposer over all cases and score the FINAL (guardrail) decisions."""
    evals = []
    for gc in cases:
        result = run_agent(gc.case.dossier, client, case_ref=gc.case_id)
        agent_dossier = result.extracted_dossier or gc.case.dossier
        evals.append(
            evaluate_case(gc.case_id, result, gc.case.decision_truth, agent_dossier, gc.case.dossier)
        )
    return build_report(evals)


def _tier_counts(cases: list[GeneratedCase]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for gc in cases:
        counts[gc.tier] = counts.get(gc.tier, 0) + 1
    return counts


def run_generation_eval(per_code: int = 30, seed: int = 42) -> tuple[list[GeneratedCase], dict]:
    """Generate cases and run all three proposer passes; return (cases, report)."""
    cases = generate_cases(per_code=per_code, seed=seed)
    report = {
        "per_code": per_code,
        "seed": seed,
        "n_cases": len(cases),
        "tier_counts": _tier_counts(cases),
        "mirror": _screen_pass(cases, PolicyMirrorStub()),
        "adversarial_lax": _screen_pass(cases, AdversarialStub()),
        "overcautious": _screen_pass(cases, OvercautiousStub()),
    }
    return cases, report


def _dump_cases(cases: list[GeneratedCase], out_dir: Path) -> None:
    """Write generated cases in the seed-file layout (browsable / re-loadable)."""
    gen_dir = out_dir / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    for gc in cases:
        d = gc.case.dossier
        payload = {
            "dossier_id": d.dossier_id,
            "tier": gc.tier,
            "dossier": {
                "registry": d.registry.model_dump(mode="json") if d.registry else None,
                "circular": d.circular.model_dump(mode="json") if d.circular else None,
                "ubo": d.ubo.model_dump(mode="json") if d.ubo else None,
            },
            "decision_truth": gc.case.decision_truth.model_dump(mode="json"),
        }
        (gen_dir / f"{gc.case_id}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_human(report: dict) -> str:
    L: list[str] = []
    L.append("=" * 74)
    L.append("doc-consistency-governance-harness — SYNTHETIC (slice-2) statistical report")
    L.append(f"cases: {report['n_cases']}  (per_code={report['per_code']}, seed={report['seed']})")
    L.append(f"tiers: {report['tier_counts']}")
    L.append("=" * 74)

    mirror = report["mirror"]
    L.append("")
    L.append("MIRROR pass (well-calibrated proposer) — correctness at scale:")
    L.append(f"    decision_accuracy   : {_pct(mirror['decision_accuracy_overall'])}")
    L.append(f"    finding_accuracy    : {_pct(mirror['finding_accuracy'])}")
    L.append(f"    trajectory_correct  : {_pct(mirror['trajectory_correctness'])}")
    L.append(f"    false_approval_rate : {_pct(mirror['false_approval_rate'])}")
    L.append(f"    guardrail_overrides : {mirror['guardrail_override_count']}")

    adv = report["adversarial_lax"]
    L.append("")
    L.append("ADVERSARIAL pass (LLM always proposes APPROVE) — the safety net:")
    L.append(f"    final decision_acc  : {_pct(adv['decision_accuracy_overall'])}   (guardrail-enforced)")
    L.append(f"    *** false_approval_rate : {_pct(adv['false_approval_rate'])}  <- must stay 0 ***")
    L.append(f"    guardrail_overrides : {adv['guardrail_override_count']}  (the guardrail correcting the lax LLM)")

    over = report["overcautious"]
    L.append("")
    L.append("OVERCAUTIOUS pass (LLM always proposes ESCALATE) — over-caution corrected:")
    L.append(f"    final decision_acc  : {_pct(over['decision_accuracy_overall'])}")
    L.append(f"    false_approval_rate : {_pct(over['false_approval_rate'])}")
    L.append(f"    guardrail_overrides : {over['guardrail_override_count']}")

    L.append("")
    L.append("MIRROR decision accuracy by taxonomy code (n per code shown):")
    for code, b in sorted(mirror["decision_accuracy_by_taxonomy_code"].items()):
        L.append(f"    {code:<6} {b['correct']:>3}/{b['total']:<3}  ({_pct(b['accuracy'])})")
    L.append("=" * 74)
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate + evaluate synthetic dossiers.")
    parser.add_argument("--per-code", type=int, default=30, help="variants per taxonomy code")
    parser.add_argument("--seed", type=int, default=42, help="random seed (reproducible)")
    parser.add_argument("--out", default="artifacts/synthetic", help="output directory")
    parser.add_argument("--dump", action="store_true", help="also write the generated dossiers to disk")
    args = parser.parse_args()

    cases, report = run_generation_eval(per_code=args.per_code, seed=args.seed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "synthetic_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    human = render_human(report)
    (out_dir / "synthetic_report.txt").write_text(human, encoding="utf-8")
    if args.dump:
        _dump_cases(cases, out_dir)
    print(human)


if __name__ == "__main__":
    main()
