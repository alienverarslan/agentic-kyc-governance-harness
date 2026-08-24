# P5 — design note: scientific metrics (Wilson CIs, provenance, five separated surfaces)

Status: **design pinned, implemented on branch `p5-scientific-metrics`.** This file is
committed together with the implementation it describes. `DECISIONS.md` remains the short
record of decisions already in effect; this note is the contract agreed before coding
(refined across adversarial design review).

## Motivation

The harness already measures its own behavior, but the reporting is neither statistically
honest nor audit-ready: rates are bare point estimates ("83% recall at n=18" hides its
own uncertainty), there is no run provenance, and the several distinct things being
measured risk collapsing into one misleading headline number.

P5 adds **no policy and changes no decision**. It measures and reports what the system
already does, with three properties: statistically honest (Wilson intervals, raw counts),
reproducible (versioned JSON artifact with full provenance), and *separated* — five
surfaces that must never be averaged into a single figure.

## Scope

**In scope:** a stdlib Wilson-CI helper; a provenance module (git SHA + dirty, Python +
package versions, canonical content hashes of corpus and promoted rules); a pure
aggregator producing a versioned JSON artifact (source of truth) plus a Markdown summary;
the learned-rule (F1) marginal contribution as its own paired-diff surface; tests.

**Consciously deferred (not P5):** any change to `guardrail.py`, the taxonomy, or the
matching functions in `metrics.py`; live AI-triage numbers going into any CI-asserted
artifact; a frozen holdout corpus (P2); a red-team corpus (P4); `EvidenceRef` (P6); a
public showcase (P7); dashboards/visualization; cross-run trend tracking.

## The five surfaces (never one headline number)

1. **Deterministic engine correctness** — domain decision/finding accuracy. SYSTEM-origin
   (T0/T1) findings are excluded, exactly as `metrics.agent_finding_codes` already does.
2. **AI triage incremental recall** — live-only; offline artifacts state it was not
   measured rather than fabricating a value.
3. **AI triage false-positive / over-escalation impact** — live-only; finding-FPR and
   decision-level over-escalation are two distinct numbers, never merged.
4. **Final guardrail safety** — false_approval_rate (scoped), override rate, and the
   SYSTEM-implies-no-approve **structural invariant** (see below).
5. **Operational reliability** — T0/T1 rates and system-induced escalation, fully separate
   from every domain metric.

## Statistics: Wilson 95% CI (stdlib only)

`harness/eval/stats.py::wilson_ci(k, n)` implements the Wilson score interval from
`math.sqrt`. No numpy/scipy — none is justified for one closed-form formula; the 95% `z`
is a hardcoded constant (a `{90,95,99}` table is the extension point if ever needed).

Edge cases, handled explicitly:
- **n = 0** → `None`, rendered as explicit `N/A`. Never a fabricated `0.0`/`[0,0]`.
- **k = 0** and **k = n** → the closed form does NOT degenerate to zero width (unlike the
  naive Wald interval, whose `[0,0]` at k=0 is exactly the overconfident artifact P5
  exists to avoid). Unit-tested against the external reference upper bound for 0/10.
- **small n** → wide interval, as it should be; every rate carries raw `k`, `n`, point
  estimate, and interval **together** via `RateStat`, never a bare percentage.

## Learned-rule (F1) marginal contribution — a paired diff, NOT recall

Computed by running the SAME corpus twice — once with the promoted rules active, once with
`promoted_rules=[]` — and diffing per case. The shape is:

```
evaluated_cases, correct_without_rule, correct_with_rule,
newly_correct_due_to_rule, newly_incorrect_due_to_rule, net_correctness_delta
```

The one optional rate is `marginal_gain_rate = newly_correct_due_to_rule / evaluated_cases`
— explicitly named a marginal-gain rate, **never** "recall". `newly_incorrect_due_to_rule`
must be `0` (the gate rejects regressions at promotion time); a nonzero value is a visible
red flag in both the artifact and the tests.

**Integrity guard (fail-closed):** the two passes must cover the identical set of case
ids. `set(case_ids_without) != set(case_ids_with)` raises and NO artifact is written — a
paired diff over different populations is meaningless. Cases and promoted-rule ids are
sorted for byte-stable output.

## Operational reliability — three rates + counterfactual attribution

Three distinct rates, not one:
- `system_failure_rate` = runs with any T-finding / all runs.
- `system_induced_escalation_rate_all_runs` = T-attributable escalations / all runs.
- `system_induced_escalation_rate_given_system_failure` = T-attributable escalations /
  runs with any T-finding.

**Attribution is a precise counterfactual:** strip a run's `origin="system"` findings,
re-apply the same `decision_for_severities` reducer, and compare. The escalation is
T-attributable iff the counterfactual decision is *strictly lower* (less severe) than the
actual final decision. If an independent domain finding (A2/X2/…) already forces the same
decision, the T-finding is still counted in `system_failure_rate` but **not** as induced
escalation.

## Triage-recall eligibility (visibility)

The recall denominator is cases that are BOTH labeled positive AND deterministically
clean. `AnomalyCase.expected_triage_concern` (new, default `True`) makes the positive
label explicit data rather than an inferred convention. The report exposes three counts —
`anomaly_cases_labeled_positive`, `anomaly_cases_excluded_not_deterministically_clean`,
`triage_recall_eligible_cases` — and names the metric
`incremental_triage_recall_on_deterministically_clean_positives`, so that a shift in
deterministic coverage (which changes the denominator) can never be misread as triage
improving.

## SYSTEM-implies-no-approve is a structural invariant, not a rate

A T-finding is always `unexplainable`, and `decision_for_severities` always escalates on
any `unexplainable` finding — so a run carrying a T-finding can never finalize to
`approve`, by construction. This is reported as a **structural invariant** citing
`tests/test_system_errors.py::test_a_system_failure_can_never_produce_an_approval`, NOT as
a Wilson-CI'd empirical zero. A confidence interval on a code guarantee would misrepresent
it as a sampled observation.

## Provenance and reproducibility

`harness/eval/provenance.py` captures, per run:
- `schema_version`, run timestamp (UTC), `run_id`, `run_type`.
- git commit SHA and `dirty` flag — dirty from `git status --porcelain` (includes
  untracked files, which `git diff` misses); undeterminable is `null`, **never** `false`.
- Python version, and installed versions of every dependency **derived from
  `pyproject.toml`** (never hardcoded, so it can't drift).
- corpus name / case count / **content hash** — SHA-256 over canonical JSON (sorted keys,
  compact separators, UTF-8) of each case's `{dossier, decision_truth}`, ordered by
  case_id. The **seed alone is never the hash input**: a generator change that alters
  output for the same seed changes the hash.
- promoted-rules hash and ids; model/provider identifier; prompt/template note.

The JSON is the source of truth; the Markdown is a human-readable rendering of it. Raw
provider responses, API keys, full exception text, and sensitive dossier content are never
written to the artifact.

## Offline vs live: separate artifacts

Offline (stub) and live (provider) runs write **separate** artifacts with distinct
`run_type`. Live numbers are never asserted as exact CI expectations — the same discipline
as `tests/test_e2e_live.py`, which skips without an API key. The offline artifact is fully
reproducible and is the one under deterministic test.

## Failure policy (three different answers)

- **Unexpected exception during report computation** → propagate, write nothing, non-zero
  exit. A partial/misleading artifact is worse than none.
- **Empty denominator (n=0) for one metric** → explicit `N/A` for that metric only; the
  rest of the report still generates.
- **Missing/incomplete provenance** → explicit `null`/`"unknown"` per field, non-fatal;
  except the git `dirty` flag, which reports `null` (unknown) rather than `false`.

## Files

New: `src/harness/eval/stats.py`, `src/harness/eval/provenance.py`,
`src/harness/eval/scientific_report.py`, `docs/p5_design.md`, `tests/test_stats.py`,
`tests/test_provenance.py`, `tests/test_scientific_report.py`.

Touched (additive, no behavior change): `src/harness/agent/anomaly_corpus.py`
(`expected_triage_concern` field), `src/harness/agent/triage_recall.py` (eligibility
breakdown + precisely-named metric alias; existing keys retained),
`tests/test_anomaly_corpus.py` (label + FPR-corpus contract tests).

## Language scope

Reporting language stays within tested/evaluated scope: no "proven", no "gap closed", no
production-readiness or regulatory-compliance claims. The LLM's bounded role is unchanged —
it does not participate in runtime rule execution or the final decision; it only proposes
schema-constrained candidates, and no rule is active without the validation gate plus
human approval.
