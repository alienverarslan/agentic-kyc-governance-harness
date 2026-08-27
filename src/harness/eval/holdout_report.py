"""P2 commit (b): the ``deterministic_holdout`` scientific report.

A WHOLLY SEPARATE artifact from the development report produced by
``scientific_report.build_offline_report``. The two are never averaged, never aggregated,
and never share an output file — because they answer different questions:

* ``deterministic_offline`` — the synthetic generator corpus. The engine was developed and
  tuned with this corpus available, so its numbers are **in-sample**.
* ``deterministic_holdout`` — the frozen P2 corpus. Nothing in the decision path can read
  it and no rule was ever gated against it, so its numbers are **out-of-sample** in the
  narrow sense that matters here.

Merging them would produce a headline figure that is neither, and would let the larger
in-sample corpus numerically drown the 18-case holdout. Separation is the point of P2, so
it is enforced structurally: a distinct ``run_type``, a distinct output directory, and
tests asserting no shared artifact path.

**Scope of the holdout claim, restated at the point of publication.** This corpus is
curated (not generator-drawn), synthetic, and in-coverage. Its construction process was
non-blinded: the deterministic checks were inspected before the LLM-assisted cases were
authored and human-reviewed.
It is NOT a blinded external benchmark. "Out-of-sample" here means *held out from
the development and tuning loop*, not *drawn from a different distribution* and not
*independently labelled*. Every rate below carries raw k/n and a Wilson 95% CI, per P5
house style, and the observed false-approval rate is empirical and corpus-scoped — never a
structural guarantee. The one structural invariant, SYSTEM-implies-no-approve, is reported
as such by the shared machinery and never given a confidence interval.

Everything statistical is delegated to ``scientific_report.build_scientific_report``
verbatim. This module contributes corpus selection, provenance, labelling, and an
integrity guard — no new statistics.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.agent.runner import run_agent
from harness.contracts.findings import AgentResult
from harness.contracts.truth import Case
from harness.data.holdout_corpus import (
    holdout_payloads,
    list_holdout_ids,
    load_holdout_case,
    load_manifest,
)
from harness.eval import provenance
from harness.eval.scientific_report import RunRecord, build_scientific_report
from harness.llm.stub import PolicyMirrorStub
from harness.rules.schema import CandidateRule
from harness.rules.store import load_promoted_rules

# Distinct from scientific_report's "deterministic_offline". Any code that merges artifacts
# would have to ignore this field deliberately.
HOLDOUT_RUN_TYPE = "deterministic_holdout"

# Distinct output directory, so a holdout artifact can never overwrite or be mistaken for
# the development one even by a careless `--out` argument.
DEFAULT_OUT_DIR = "artifacts/p2_holdout"

SAMPLE_RELATIONSHIP = "out_of_sample"

# The scope qualifiers travel WITH the numbers, inside the artifact, so a reader who sees
# only the JSON still cannot mistake this for a blind external benchmark.
CORPUS_SCOPE_NOTE = (
    "Curated (not generator-drawn), synthetic, in-coverage corpus held out from the "
    "development and tuning "
    "loop: excluded from the rule-promotion gate, the generator, the anomaly builder and "
    "normal development runs, and frozen (hash-pinned) before any reporting. It is NOT a "
    "blinded external benchmark — the construction process was non-blinded: the "
    "deterministic checks were inspected before the cases were authored, placing them "
    "inside the known taxonomy. 'Out-of-sample' therefore means held out from tuning, "
    "not drawn from a different distribution and not independently labelled. It does not "
    "measure discovery of unknown failure modes, independent-labeler agreement, or "
    "real-world generalization; those remain P4/red-team questions. Case texts were "
    "authored with LLM assistance and reviewed and approved by the author; the labels "
    "are reviewed judgments, not externally validated ground truth (see ERRATA.md)."
)

FAR_EMPIRICAL_NOTE = (
    "The false_approval_rate reported for this corpus is an EMPIRICAL, corpus-scoped rate: "
    "it holds because the deterministic checks actually catch each authored case, and it is "
    "not a structural guarantee. Its Wilson 95% CI quantifies sampling uncertainty at this "
    "corpus size and does not extend the claim beyond this corpus. The only structural "
    "invariant is SYSTEM-origin-implies-no-approve, reported separately as a structural "
    "invariant and never given a confidence interval."
)


class HoldoutIntegrityError(RuntimeError):
    """Raised when the holdout on disk no longer matches its frozen manifest.

    Fail-closed, mirroring P5's rule that an integrity failure emits NO artifact: reporting
    numbers measured on a corpus that has silently drifted from its pin would be worse than
    reporting nothing, because the artifact would carry a hash that no longer describes what
    was actually screened.
    """


def verify_holdout_matches_manifest() -> dict[str, Any]:
    """Confirm the live corpus still hashes to its frozen pin; return the manifest.

    The immutability suite already asserts this, but a report must not depend on a test
    having been run: the artifact embeds ``corpus_hash`` as a reproducibility claim, so it
    re-verifies at the moment of publication.
    """
    manifest = load_manifest()
    actual = provenance.hash_corpus(holdout_payloads())
    if actual != manifest["corpus_hash"]:
        raise HoldoutIntegrityError(
            "the holdout corpus on disk does not match its frozen manifest "
            f"(manifest={manifest['corpus_hash']}, actual={actual}). No report was written. "
            "This is a RESULT to investigate, not a pin to regenerate."
        )
    if sorted(c["case_id"] for c in manifest["cases"]) != list_holdout_ids():
        raise HoldoutIntegrityError(
            "the holdout case set on disk differs from the frozen manifest. No report was "
            "written."
        )
    return manifest


def _record_from_case(case_id: str, case: Case, result: AgentResult) -> RunRecord:
    """Adapt a holdout ``Case`` + screening result into P5's ``RunRecord``.

    Mirrors ``scientific_report._record_from_result`` exactly — including excluding
    SYSTEM-origin findings from the domain code set — but reads a ``Case`` rather than a
    ``GeneratedCase``, since the holdout is curated rather than generated.
    """
    truth = case.decision_truth
    agent_codes = sorted({f.code for f in result.findings if f.origin != "system"}) or ["NONE"]
    injected = list(truth.injected_codes) or ["NONE"]
    return RunRecord(
        case_id=case_id,
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


def collect_holdout_records(
    promoted_rules: list[CandidateRule],
) -> tuple[list[RunRecord], int]:
    """Screen every holdout case offline under a fixed promoted-rule set.

    Uses the eval-only loader's ``load_holdout_case``; the agent still receives only the
    ``Dossier`` (``case.dossier``), never the truth, exactly as the seed and generator paths
    do. Returns (records sorted by case_id, guardrail-override count).
    """
    stub = PolicyMirrorStub()
    records: list[RunRecord] = []
    overrides = 0
    for case_id in list_holdout_ids():
        case = load_holdout_case(case_id)
        result = run_agent(
            case.dossier, stub, case_ref=case_id, promoted_rules=promoted_rules
        )
        overrides += int(result.guardrail.overridden)
        records.append(_record_from_case(case_id, case, result))
    records.sort(key=lambda r: r.case_id)
    return records, overrides


def build_holdout_report() -> dict[str, Any]:
    """End-to-end holdout artifact build. Raises (writing nothing) on integrity failure."""
    manifest = verify_holdout_matches_manifest()
    promoted = load_promoted_rules()

    with_records, overrides = collect_holdout_records(promoted)
    without_records, _ = collect_holdout_records([])

    now = datetime.now(timezone.utc)
    run_block = provenance.build_run_provenance(
        run_type=HOLDOUT_RUN_TYPE,
        timestamp_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        run_id=f"{now.strftime('%Y%m%dT%H%M%SZ')}-holdout",
    )

    corpus_meta = {
        "name": manifest["corpus_name"],
        "corpus_version": manifest["corpus_version"],
        "case_count": manifest["case_count"],
        "corpus_hash": manifest["corpus_hash"],
        "frozen_at_utc": manifest["frozen_at_utc"],
        "hash_method": manifest["hash_method"],
        # Labels that keep this artifact from being read as the development one.
        "sample_relationship": SAMPLE_RELATIONSHIP,
        "data_class": "synthetic",
        # Two facts that the earlier single value "hand_authored" conflated: how the cases
        # were DERIVED (curated case by case, never a generator draw) and who AUTHORED
        # them (LLM-assisted drafting, human-reviewed labels). See ERRATA.md.
        "authoring": "llm_assisted_human_reviewed",
        "derivation": "curated_not_generator_drawn",
        "coverage_scope": "in_coverage",
        "scope_note": CORPUS_SCOPE_NOTE,
        "separation_note": (
            "Never averaged or aggregated with the deterministic_offline development "
            "report; the two corpora answer different questions and are published as "
            "separate artifacts."
        ),
    }
    model_meta = {"provider": "stub", "variant": "policy_mirror", "model_id": None}
    prompt_meta = {
        "note": "offline stub emits no provider prompts; prompt hashing applies to live runs"
    }
    rules_meta = {
        "promoted_rule_ids": sorted(r.rule_id for r in promoted),
        "promoted_rules_hash": provenance.hash_promoted_rules(
            [r.model_dump() for r in promoted]
        ),
    }

    report = build_scientific_report(
        run_block=run_block,
        corpus_meta=corpus_meta,
        model_meta=model_meta,
        prompt_meta=prompt_meta,
        rules_meta=rules_meta,
        with_rule_records=with_records,
        without_rule_records=without_records,
        overridden_count=overrides,
    )
    # Attach the empirical-vs-structural note next to the surface that publishes the rate,
    # so the qualifier cannot be separated from the number it qualifies.
    report["metrics"]["final_guardrail_safety"]["holdout_far_interpretation"] = FAR_EMPIRICAL_NOTE

    # Rule EXPOSURE, published beside the paired diff. Without it, "net_correctness_delta: 0"
    # reads as "the rule was safe out-of-sample" when the truthful reading may be "the rule
    # never fired here, so this corpus says nothing about it either way".
    lr = report["metrics"]["deterministic_engine"].get("learned_rule_marginal")
    if lr is not None:
        applicability = sum(1 for r in with_records if "F1" in r.agent_codes)
        lr["rule_applicability_count"] = applicability
        lr["exposure_note"] = _exposure_note(applicability, len(with_records))
    return report


def _exposure_note(applicability: int, n_cases: int) -> str:
    """Wording that cannot be misread as an out-of-sample rule-safety result."""
    if applicability == 0:
        return (
            "The promoted rule set was EVALUATED on this corpus but did not trigger on any "
            "case (rule_applicability_count = 0). A paired delta of zero therefore means "
            "NOT EXERCISED — no observed effect on this corpus — and is NOT evidence of "
            "out-of-sample rule safety or benefit. Nothing here supports or contradicts the "
            "promoted rule; the corpus simply contains no case it applies to."
        )
    return (
        f"The promoted rule set triggered on {applicability} of {n_cases} cases; the paired "
        "delta is measured over that exposure. It remains a corpus-scoped observation, not a "
        "general claim about the rule."
    )


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable rendering. Delegates the five surfaces to P5's renderer and prepends
    the holdout-specific scope header, so the qualifiers are read before the numbers."""
    from harness.eval.scientific_report import render_markdown as render_p5

    corpus = report["corpus"]
    h1 = "# P2 Holdout Report — deterministic_holdout (OUT-OF-SAMPLE)"
    header = [
        h1,
        "",
        "> **This is not the development report.** It is a separate artifact over the frozen",
        "> P2 holdout corpus and is never averaged with `deterministic_offline` results.",
        "",
        f"- corpus_version: `{corpus['corpus_version']}`  frozen_at: `{corpus['frozen_at_utc']}`",
        f"- sample_relationship: **{corpus['sample_relationship']}** · "
        f"{corpus['data_class']} · {corpus['authoring']} · {corpus['coverage_scope']}",
        "",
        f"**Scope.** {corpus['scope_note']}",
        "",
        f"**On the false-approval rate.** {report['metrics']['final_guardrail_safety']['holdout_far_interpretation']}",
        "",
        "---",
        "",
    ]
    # Suppress P5's own H1: this artifact has ONE identity (P2 holdout), never the in-sample
    # development report's title. The five surfaces follow under our heading.
    return "\n".join(header) + render_p5(report, title="")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the P2 deterministic_holdout report (separate from the dev report)."
    )
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="output directory")
    args = parser.parse_args()

    report = build_holdout_report()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "holdout_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md = render_markdown(report)
    (out_dir / "holdout_report.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
