"""P5 scientific report: an audit-ready, reproducible, statistically honest rendering of
the harness's ALREADY-MEASURED behavior. It adds no policy and changes no decision — it
measures and reports.

Five surfaces are kept explicitly separate (never a single headline number):

1. deterministic engine correctness   — domain decision/finding accuracy (SYSTEM-origin
                                         findings excluded, as metrics.py already does).
1b. learned-rule (F1) marginal        — paired before/after diff on the SAME cases.
2. AI triage recall                   — live-only; see harness.agent.triage_recall.
3. AI triage FPR / over-escalation    — live-only; see harness.agent.triage_fpr.
4. final guardrail safety             — false_approval_rate (scoped), override rate, and
                                         the SYSTEM-implies-no-approve STRUCTURAL invariant.
5. operational reliability            — T0/T1 rates + counterfactual system-induced
                                         escalation, fully separate from domain metrics.

Design:
* ``build_scientific_report`` is a PURE function of already-collected per-case records —
  fully unit-testable with no I/O. The impure parts (running the agent, capturing git/env
  provenance, writing files) live in ``collect_records`` / ``main``.
* Every rate is a ``RateStat`` (raw k, n, point estimate, Wilson 95% CI) — never a bare
  float, so small-n uncertainty is always visible.
* Offline (stub) and live (provider) runs write SEPARATE artifacts with distinct
  ``run_type``; live numbers are never asserted as CI expectations.
* On any integrity failure the report raises and NO artifact is written — a partial or
  misleading artifact is worse than none.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.agent.guardrail import decision_for_severities
from harness.agent.runner import run_agent
from harness.contracts.findings import AgentResult
from harness.eval import provenance
from harness.eval.stats import RateStat
from harness.generate.synthetic import GeneratedCase, generate_cases
from harness.llm.stub import PolicyMirrorStub
from harness.rules.schema import CandidateRule
from harness.rules.store import load_promoted_rules

# Decision severity rank, for the counterfactual "strictly lower" comparison in operational
# attribution. Matches the guardrail's precedence: approve < request_more_info < escalate.
_DECISION_RANK = {"approve": 0, "request_more_info": 1, "escalate": 2}

SCHEMA_VERSION = "1.0.0"

# The FAR scope sentence — verbatim, reused wherever false_approval_rate is reported so the
# claim is never accidentally widened into an end-to-end guarantee.
FAR_SCOPE = (
    "false_approval_rate is computed within evaluated harness runs against this corpus's "
    "defined ground truth; it is not an end-to-end guarantee for whatever system calls "
    "this harness."
)

# The SYSTEM-implies-no-approve property is a STRUCTURAL invariant (a T-finding is always
# unexplainable, and decision_for_severities always escalates on any unexplainable finding),
# verified by construction and by this test — NOT an empirical rate with a confidence
# interval. Reporting a CI on a structurally-guaranteed zero would misrepresent a code
# guarantee as a sampled observation.
SYSTEM_INVARIANT_TEST = (
    "tests/test_system_errors.py::test_a_system_failure_can_never_produce_an_approval"
)


@dataclass
class RunRecord:
    """Everything the five surfaces need from ONE screening run, decoupled from the live
    ``AgentResult`` so ``build_scientific_report`` stays a pure function of plain data."""

    case_id: str
    expected_decision: str
    agent_decision: str
    decision_ok: bool
    finding_ok: bool
    injected_codes: list[str]
    agent_codes: list[str]
    # (severity, origin) for every finding — origin lets the counterfactual strip SYSTEM.
    finding_severities_by_origin: list[tuple[str, str]] = field(default_factory=list)
    system_failure_count: int = 0
    system_error_codes: list[str] = field(default_factory=list)
    # The bounded ErrorKind of each SYSTEM finding (timeout / schema_invalid / ...), for the
    # operational error_kind distribution.
    system_error_kinds: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Surface 1: deterministic engine correctness
# --------------------------------------------------------------------------------------
def build_deterministic_engine_metrics(records: list[RunRecord]) -> dict[str, Any]:
    n = len(records)
    decision_correct = sum(r.decision_ok for r in records)
    finding_correct = sum(r.finding_ok for r in records)

    by_code: dict[str, dict[str, int]] = {}
    for r in records:
        key = "+".join(r.injected_codes) if r.injected_codes else "NONE"
        bucket = by_code.setdefault(key, {"correct": 0, "total": 0})
        bucket["total"] += 1
        bucket["correct"] += int(r.decision_ok)

    return {
        "decision_accuracy": RateStat(decision_correct, n).to_dict(),
        "finding_accuracy": RateStat(finding_correct, n).to_dict(),
        "by_taxonomy_code": {
            code: RateStat(b["correct"], b["total"]).to_dict()
            for code, b in sorted(by_code.items())
        },
    }


# --------------------------------------------------------------------------------------
# Surface 1b: learned-rule (F1) marginal contribution — paired before/after diff
# --------------------------------------------------------------------------------------
def build_learned_rule_marginal(
    with_rule: list[RunRecord],
    without_rule: list[RunRecord],
    promoted_rule_ids: list[str],
) -> dict[str, Any]:
    """Paired marginal contribution of the active promoted-rule set.

    This is NOT recall — it is a paired delta on the SAME cases run twice: once with the
    promoted rules active, once with none. The two passes MUST cover the identical set of
    case ids, or the diff is meaningless; a mismatch raises (fail-closed, no artifact).
    """
    ids_with = {r.case_id for r in with_rule}
    ids_without = {r.case_id for r in without_rule}
    if ids_with != ids_without:
        only_with = sorted(ids_with - ids_without)
        only_without = sorted(ids_without - ids_with)
        raise ValueError(
            "learned-rule paired diff requires identical case-id sets across the "
            f"with-rule and without-rule passes; only_with={only_with!r}, "
            f"only_without={only_without!r}"
        )

    with_by_id = {r.case_id: r for r in with_rule}
    without_by_id = {r.case_id: r for r in without_rule}
    n = len(ids_with)

    newly_correct = 0
    newly_incorrect = 0
    for cid in sorted(ids_with):
        w = with_by_id[cid].decision_ok
        wo = without_by_id[cid].decision_ok
        if w and not wo:
            newly_correct += 1
        elif wo and not w:
            newly_incorrect += 1

    correct_without = sum(r.decision_ok for r in without_rule)
    correct_with = sum(r.decision_ok for r in with_rule)

    return {
        "promoted_rule_ids": sorted(promoted_rule_ids),
        "evaluated_cases": n,
        "correct_without_rule": correct_without,
        "correct_with_rule": correct_with,
        "newly_correct_due_to_rule": newly_correct,
        # A promotion that REGRESSES a previously-correct case violates the gate's promise
        # (rules/gate.py rejects regressions at promotion time). This must be 0; a nonzero
        # value is a visible red flag, in both the artifact and the tests.
        "newly_incorrect_due_to_rule": newly_incorrect,
        "net_correctness_delta": newly_correct - newly_incorrect,
        # The ONE optional rate here — explicitly a marginal-gain rate, never called recall.
        "marginal_gain_rate": RateStat(newly_correct, n).to_dict(),
    }


# --------------------------------------------------------------------------------------
# Surface 4: final guardrail safety
# --------------------------------------------------------------------------------------
def build_final_guardrail_safety(records: list[RunRecord], overridden_count: int) -> dict[str, Any]:
    n = len(records)
    catastrophic = [r for r in records if r.expected_decision in ("request_more_info", "escalate")]
    false_approvals = [r for r in catastrophic if r.agent_decision == "approve"]

    return {
        "false_approval_rate": RateStat(len(false_approvals), len(catastrophic)).to_dict(),
        "false_approval_rate_scope": FAR_SCOPE,
        "false_approvals": [r.case_id for r in false_approvals],
        "guardrail_override_rate": RateStat(overridden_count, n).to_dict(),
        "system_present_implies_no_approve": {
            "type": "structural_invariant",
            "statement": (
                "a run carrying any SYSTEM (T0/T1) finding can never finalize to approve"
            ),
            "verified_by": SYSTEM_INVARIANT_TEST,
        },
    }


# --------------------------------------------------------------------------------------
# Surface 5: operational reliability — three-way rates + counterfactual attribution
# --------------------------------------------------------------------------------------
def _counterfactual_without_system(record: RunRecord) -> str:
    """The decision the guardrail WOULD have reached with SYSTEM findings removed."""
    kept = [sev for sev, origin in record.finding_severities_by_origin if origin != "system"]
    return decision_for_severities(kept)


def build_operational_reliability(records: list[RunRecord]) -> dict[str, Any]:
    n = len(records)
    with_failure = [r for r in records if r.system_failure_count > 0]

    # Counterfactual attribution: a run's escalation is SYSTEM-induced iff removing its
    # SYSTEM findings and re-applying the same guardrail reducer yields a strictly LOWER
    # decision. If an independent domain finding (A2/X2/...) already forces the same
    # decision, the T-finding is still counted as a failure but NOT as induced escalation.
    induced = []
    for r in with_failure:
        counterfactual = _counterfactual_without_system(r)
        if _DECISION_RANK[counterfactual] < _DECISION_RANK[r.agent_decision]:
            induced.append(r)

    error_kinds: Counter[str] = Counter()
    for r in records:
        for kind in r.system_error_kinds:
            error_kinds[kind] += 1

    return {
        "system_failure_rate": RateStat(len(with_failure), n).to_dict(),
        "system_induced_escalation_rate_all_runs": RateStat(len(induced), n).to_dict(),
        "system_induced_escalation_rate_given_system_failure": RateStat(
            len(induced), len(with_failure)
        ).to_dict(),
        "error_kind_distribution": dict(sorted(error_kinds.items())),
        "attribution_method": (
            "counterfactual: decision_for_severities re-applied after excluding "
            "origin='system' findings; a run is SYSTEM-induced iff that decision is "
            "strictly lower (less severe) than the actual final decision"
        ),
    }


# --------------------------------------------------------------------------------------
# The pure aggregator
# --------------------------------------------------------------------------------------
def build_scientific_report(
    *,
    run_block: dict[str, Any],
    corpus_meta: dict[str, Any],
    model_meta: dict[str, Any],
    prompt_meta: dict[str, Any],
    rules_meta: dict[str, Any],
    with_rule_records: list[RunRecord],
    without_rule_records: list[RunRecord] | None,
    overridden_count: int,
    include_live_placeholders: bool = True,
) -> dict[str, Any]:
    """Assemble the full P5 artifact from already-collected records. Pure: no I/O, no agent
    runs. Raises on any integrity violation (e.g. mismatched F1 paired-diff case sets)."""
    det = build_deterministic_engine_metrics(with_rule_records)

    if without_rule_records is not None:
        det["learned_rule_marginal"] = build_learned_rule_marginal(
            with_rule_records,
            without_rule_records,
            rules_meta.get("promoted_rule_ids", []),
        )

    metrics: dict[str, Any] = {
        "deterministic_engine": det,
        "final_guardrail_safety": build_final_guardrail_safety(with_rule_records, overridden_count),
        "operational_reliability": build_operational_reliability(with_rule_records),
    }

    if include_live_placeholders:
        # These two surfaces require a live provider; an OFFLINE artifact states they were
        # not measured here rather than fabricating a value. Live runs emit their own
        # artifact (run_type live_*), never merged into this one.
        metrics["ai_triage_recall"] = {
            "note": "live-only; not measured in this offline run",
            "truth_label": "AnomalyCase.expected_triage_concern",
            "see": "harness.agent.triage_recall",
        }
        metrics["ai_triage_fpr"] = {
            "note": "live-only; not measured in this offline run",
            "see": "harness.agent.triage_fpr",
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "run": run_block,
        "corpus": corpus_meta,
        "model": model_meta,
        "prompts": prompt_meta,
        "rules": rules_meta,
        "metrics": metrics,
    }


# --------------------------------------------------------------------------------------
# Impure collection: run the agent over the offline generator corpus
# --------------------------------------------------------------------------------------
def _record_from_result(gc: GeneratedCase, result: AgentResult) -> RunRecord:
    truth = gc.case.decision_truth
    # Domain finding codes only (exclude SYSTEM), matching metrics.agent_finding_codes.
    agent_codes = sorted({f.code for f in result.findings if f.origin != "system"}) or ["NONE"]
    injected = list(truth.injected_codes) or ["NONE"]
    return RunRecord(
        case_id=gc.case_id,
        expected_decision=truth.expected_decision,
        agent_decision=result.decision,
        decision_ok=result.decision == truth.expected_decision,
        finding_ok=set(agent_codes) == set(injected),
        injected_codes=list(truth.injected_codes),
        agent_codes=agent_codes,
        finding_severities_by_origin=[(f.severity, f.origin) for f in result.findings],
        system_failure_count=result.system_failure_count,
        system_error_codes=list(result.system_error_codes),
        system_error_kinds=[
            f.error_kind for f in result.findings if f.origin == "system" and f.error_kind
        ],
    )


def collect_records(
    cases: list[GeneratedCase], promoted_rules: list[CandidateRule]
) -> tuple[list[RunRecord], int]:
    """Screen every case offline with the PolicyMirrorStub under a fixed promoted-rule set.
    Returns (records sorted by case_id, guardrail-override count)."""
    stub = PolicyMirrorStub()
    records: list[RunRecord] = []
    overrides = 0
    for gc in cases:
        result = run_agent(gc.case.dossier, stub, case_ref=gc.case_id, promoted_rules=promoted_rules)
        overrides += int(result.guardrail.overridden)
        records.append(_record_from_result(gc, result))
    records.sort(key=lambda r: r.case_id)
    return records, overrides


def _corpus_payloads(cases: list[GeneratedCase]) -> list[tuple[str, dict[str, Any]]]:
    """Serializable (case_id, content) pairs for the content-based corpus hash."""
    out = []
    for gc in cases:
        out.append(
            (
                gc.case_id,
                {
                    "dossier": gc.case.dossier.model_dump(mode="json"),
                    "decision_truth": gc.case.decision_truth.model_dump(mode="json"),
                },
            )
        )
    return out


def build_offline_report(per_code: int, seed: int) -> dict[str, Any]:
    """End-to-end offline artifact build: generate corpus, run both passes, aggregate."""
    cases = generate_cases(per_code=per_code, seed=seed)
    promoted = load_promoted_rules()

    with_records, overrides = collect_records(cases, promoted)
    without_records, _ = collect_records(cases, [])

    now = datetime.now(timezone.utc)
    run_block = provenance.build_run_provenance(
        run_type="deterministic_offline",
        timestamp_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        run_id=f"{now.strftime('%Y%m%dT%H%M%SZ')}-offline",
    )
    corpus_meta = {
        "name": "synthetic_generator",
        "per_code": per_code,
        "seed": seed,
        "case_count": len(cases),
        # The engine was developed and tuned with this corpus available, so these numbers
        # are IN-SAMPLE. The P2 holdout artifact carries "out_of_sample" and is published
        # separately (harness.eval.holdout_report); the two are never averaged.
        "sample_relationship": "in_sample",
        "corpus_hash": provenance.hash_corpus(_corpus_payloads(cases)),
        "hash_method": (
            "sha256 over canonical json (sorted keys, compact separators, utf-8) of each "
            "case's {dossier, decision_truth}, ordered by case_id; the seed alone is never "
            "the hash input"
        ),
    }
    model_meta = {"provider": "stub", "variant": "policy_mirror", "model_id": None}
    prompt_meta = {
        "note": "offline stub emits no provider prompts; prompt hashing applies to live runs"
    }
    rules_meta = {
        "promoted_rule_ids": sorted(r.rule_id for r in promoted),
        "promoted_rules_hash": provenance.hash_promoted_rules([r.model_dump() for r in promoted]),
    }

    return build_scientific_report(
        run_block=run_block,
        corpus_meta=corpus_meta,
        model_meta=model_meta,
        prompt_meta=prompt_meta,
        rules_meta=rules_meta,
        with_rule_records=with_records,
        without_rule_records=without_records,
        overridden_count=overrides,
    )


# --------------------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------------------
def _fmt_rate(stat: dict[str, Any]) -> str:
    pe = stat["point_estimate"]
    ci = stat["wilson_ci_95"]
    if pe is None or ci is None:
        return f"N/A (k={stat['k']}, n={stat['n']})"
    return f"{pe * 100:.1f}%  (k={stat['k']}, n={stat['n']}, 95% CI [{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%])"


def render_markdown(report: dict[str, Any], *, title: str | None = None) -> str:
    """Render the five surfaces as Markdown.

    ``title`` overrides the H1. It exists so a caller that wraps this output under its own
    heading (the P2 holdout report) can suppress a second, misleading "P5 Scientific
    Report" H1 — a P2 evaluation artifact must not carry the in-sample development report's
    identity. Passing ``""`` emits no H1 at all (the caller supplied one); omitting the
    argument keeps the development report's title exactly as before.
    """
    m = report["metrics"]
    run = report["run"]
    corpus = report["corpus"]
    L: list[str] = []
    heading = "# P5 Scientific Report — doc-consistency-governance-harness" if title is None else title
    if heading:
        L.append(heading)
        L.append("")
    L.append(f"- schema_version: `{report['schema_version']}`")
    L.append(f"- run_type: `{run['run_type']}`   timestamp: `{run['timestamp_utc']}`")
    git = run["git"]
    L.append(f"- git: `{git['commit_sha']}`   dirty: `{git['dirty']}`")
    # `seed` is meaningful only for a GENERATED corpus. A hand-authored corpus (the P2
    # holdout) has none, and inventing one to satisfy the renderer would put a fictional
    # reproducibility parameter into an audit artifact. Render what is actually present.
    corpus_line = f"- corpus: `{corpus['name']}` (n={corpus['case_count']}"
    if "seed" in corpus:
        corpus_line += f", seed={corpus['seed']}"
    if "sample_relationship" in corpus:
        corpus_line += f", {corpus['sample_relationship']}"
    L.append(corpus_line + ")")
    L.append(f"- corpus_hash: `{corpus['corpus_hash']}`")
    L.append(f"- model: `{report['model']['provider']}/{report['model']['variant']}`")
    L.append("")

    det = m["deterministic_engine"]
    L.append("## 1. Deterministic engine correctness")
    L.append(f"- decision_accuracy: {_fmt_rate(det['decision_accuracy'])}")
    L.append(f"- finding_accuracy:  {_fmt_rate(det['finding_accuracy'])}")
    L.append("")
    L.append("Per taxonomy code (decision accuracy):")
    L.append("")
    L.append("| code | k/n | point | 95% CI |")
    L.append("|---|---|---|---|")
    for code, stat in det["by_taxonomy_code"].items():
        pe = stat["point_estimate"]
        ci = stat["wilson_ci_95"]
        pe_s = "N/A" if pe is None else f"{pe * 100:.1f}%"
        ci_s = "N/A" if ci is None else f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]"
        L.append(f"| {code} | {stat['k']}/{stat['n']} | {pe_s} | {ci_s} |")
    L.append("")

    if "learned_rule_marginal" in det:
        lr = det["learned_rule_marginal"]
        L.append("### 1b. Learned-rule (F1) marginal contribution — paired diff")
        L.append(f"- promoted_rule_ids: `{lr['promoted_rule_ids']}`")
        L.append(f"- evaluated_cases: {lr['evaluated_cases']}")
        L.append(f"- correct_without_rule: {lr['correct_without_rule']}")
        L.append(f"- correct_with_rule: {lr['correct_with_rule']}")
        L.append(f"- newly_correct_due_to_rule: {lr['newly_correct_due_to_rule']}")
        L.append(f"- newly_incorrect_due_to_rule: {lr['newly_incorrect_due_to_rule']}  (must be 0)")
        L.append(f"- net_correctness_delta: {lr['net_correctness_delta']}")
        L.append(f"- marginal_gain_rate: {_fmt_rate(lr['marginal_gain_rate'])}")
        # Exposure must be rendered NEXT TO the delta it qualifies: a zero delta from a rule
        # that never fired means "not exercised", which is a different statement from "no
        # regression observed under exposure".
        if "rule_applicability_count" in lr:
            L.append(f"- rule_applicability_count: {lr['rule_applicability_count']}")
        if "exposure_note" in lr:
            L.append(f"- **exposure:** {lr['exposure_note']}")
        L.append("")

    L.append("## 2. AI triage incremental recall")
    L.append(f"- {m['ai_triage_recall']['note']}")
    L.append("")
    L.append("## 3. AI triage false-positive / over-escalation impact")
    L.append(f"- {m['ai_triage_fpr']['note']}")
    L.append("")

    fg = m["final_guardrail_safety"]
    L.append("## 4. Final guardrail safety")
    L.append(f"- false_approval_rate: {_fmt_rate(fg['false_approval_rate'])}")
    L.append(f"  - scope: {fg['false_approval_rate_scope']}")
    L.append(f"- guardrail_override_rate: {_fmt_rate(fg['guardrail_override_rate'])}")
    inv = fg["system_present_implies_no_approve"]
    L.append(f"- SYSTEM-implies-no-approve: **{inv['type']}** — {inv['statement']}")
    L.append(f"  - verified_by: `{inv['verified_by']}`")
    L.append("")

    op = m["operational_reliability"]
    L.append("## 5. Operational reliability")
    L.append(f"- system_failure_rate: {_fmt_rate(op['system_failure_rate'])}")
    L.append(f"- system_induced_escalation_rate (all runs): {_fmt_rate(op['system_induced_escalation_rate_all_runs'])}")
    L.append(f"- system_induced_escalation_rate (given a system failure): {_fmt_rate(op['system_induced_escalation_rate_given_system_failure'])}")
    L.append(f"- error_kind_distribution: `{op['error_kind_distribution']}`")
    L.append(f"- attribution: {op['attribution_method']}")
    L.append("")

    L.append("## Provenance appendix")
    L.append("")
    L.append("| field | value |")
    L.append("|---|---|")
    L.append(f"| python_version | `{run['python_version']}` |")
    L.append(f"| harness_version | `{run['harness_version']}` |")
    for pkg, ver in run["package_versions"].items():
        L.append(f"| pkg:{pkg} | `{ver}` |")
    L.append(f"| promoted_rules_hash | `{report['rules']['promoted_rules_hash']}` |")
    return "\n".join(L)


# --------------------------------------------------------------------------------------
# Thin CLI
# --------------------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Build the P5 offline scientific report.")
    parser.add_argument("--per-code", type=int, default=30, help="cases per taxonomy code")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/p5", help="output directory")
    args = parser.parse_args()

    report = build_offline_report(per_code=args.per_code, seed=args.seed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "p5_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md = render_markdown(report)
    (out_dir / "p5_report.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
