# P2 — design note: the frozen in-coverage holdout corpus

Status: **implemented.** Commit (a) landed the corpus, pins, loader, freeze tool and
isolation tests (PR #4, merge `ef1d955`); commit (b) adds the separate
`deterministic_holdout` report. `DECISIONS.md` carries the short record of decisions now in
effect; this note is the contract, refined across adversarial design review.

## Motivation

Every number the harness reported before P2 came from corpora that were available during
development: the 8 seed fixtures and the 420-case synthetic generator. Both are legitimate
regression instruments, but neither can answer "does the deterministic engine handle cases
it was never tuned against?" — because the engine *was* tuned with them in view.

P2 adds a corpus that is authored once, frozen, and structurally unreachable from anything
that produces or gates a decision.

## Scope: in-coverage, and what that does and does not buy

P2 is an **in-coverage** holdout. Every case's issue falls inside the deterministic checks'
existing coverage, so it measures generalization to novel **cases**, not to novel
**phenomena**.

Out-of-coverage generalization is deliberately deferred to **P4** (red-team corpus), where
a nonzero deterministic false-approval rate is the *intended finding* rather than a
violated invariant. Conflating the two would be a category error: an in-coverage FAR of 0
says nothing about unknown failure modes, and a P4 FAR above 0 would not be a P2 regression.

### The non-blinded-authoring limitation

**This corpus is not a blinded external benchmark, and must never be described as one.**

The author read `checks.py` in detail before writing the fixtures, and constructed cases
deliberately inside the known taxonomy — which is partly unavoidable, since staying
in-coverage requires knowing the coverage. Consequently:

* "Held out" means **held out from the development and tuning loop**: excluded from the
  rule-promotion gate, the generator, the anomaly builder and normal development runs, and
  frozen before any reporting.
* "Held out" does **not** mean drawn from a different distribution, independently labelled,
  or authored blind to the implementation.
* P2 therefore does not measure discovery of unknown failure modes, independent-labeler
  agreement, or real-world generalization. Those remain P4/red-team questions and would need
  an independently authored corpus.

The load-bearing isolation claim is narrower and defensible: **the system and the
rule-promotion process were never tuned against these frozen cases.**

## The corpus

18 hand-authored cases, `src/harness/data/holdout/case_hold_01..18.json`.

| Property | Value |
|---|---|
| Taxonomy coverage | all 14 codes |
| Decision classes | 4 approve / 8 request_more_info / 6 escalate |
| Compound cases | 2 (`A1+B2a` info+explainable → request_more_info; `A2+B1a` unexplainable+explainable → escalate) |

**Hand-authored, never a generator draw.** Drawing from the generator with a different seed
would test reproducibility, not generalization — the recipes, and therefore the failure
modes they can express, would be identical.

**Compound cases matter** because the single-injector generator structurally cannot produce
them: every generated case isolates exactly one code. The guardrail's max-severity
composition (drop nothing, take the maximum) is therefore exercised on unseen data rather
than only in the P3 compound suite.

**Independence is an authoring convention, not a test-enforced property.** No fixture value
is copied from `generate/pools.py`, but a few surface tokens recur — "Işıklar" alongside the
pool core "Işık"; "Liman Mah." and "Yenişehir" appear inside pool street strings. Stated
plainly rather than claimed away.

**Two fixtures double as false-positive guards.** `hold_12` pairs a genuine `D1a` with a
sub-threshold shareholder that must *not* be flagged, so a check that flagged both would
still "decide correctly" while being wrong. `hold_03` reproduces the `"a ş"` token adjacency
that broke suffix canonicalization in slice-2.

### `hold_15` (E2) — the one open authoring question, and how it was resolved

`check_completeness` emits E2 for present-but-empty mandatory fields, but most single-field
blankings cascade into a second check: blanking `registry.shareholders` produces `B2a`;
blanking `circular.authorized_signatories` or `ubo.declarant_name` produces `C1a`/`C1b`; a
single document's `tax_id` produces `A2`.

Two constructions isolate E2 cleanly: blanking all three `tax_id`s, or blanking
`registry.legal_name` alone. The second was rejected — it leaves the identity check with
differing name surfaces, which reaches `legal_names_match` and therefore
`rapidfuzz.token_set_ratio` on an empty string. **A frozen fixture's ground truth must not
depend on a third-party matcher's empty-string edge behavior**, which a version bump could
silently change.

`hold_15` therefore blanks `legal_name` **consistently across all three documents**: the
surface set has size 1, the A1 branch is never entered, the fuzzy path is unreachable, and
identical `tax_id`s rule out A2. `check_completeness` checks `legal_name` emptiness only on
the registry, so exactly one E2 results.

The cost is stated honestly: this is the *same mechanism* the generator uses for E2
(consistent cross-document blanking). `hold_15`'s independence is in its surfaces and
structure, not in a novel missing-field mechanism. Robustness was preferred over mechanism
novelty.

## Manifest and hash pinning

`holdout/manifest.json` is machine-readable and hash-pinned, reusing P5's
`provenance.hash_corpus` / `sha256_of` verbatim.

Hashing operates on **contract-normalized content** — each case's `{dossier,
decision_truth}` after model round-tripping — not on raw file bytes. This repo has
`core.autocrlf=true` on the Windows side, so byte-level pinning would break across
checkouts; the normalized form is invariant to whitespace, key order and line endings while
remaining sensitive to every meaning-bearing change.

The manifest also records `observed_at_freeze`: what the harness actually did at freeze
time, alongside the per-case `decision_truth` which is what it *should* do. The snapshot is
an observation, not an expectation.

### Bootstrap provenance: `dirty: true` is correct

`manifest.git.commit_sha` records `5cbbc5f` — the base commit — with `dirty: true`.

This is honest, not a defect. The corpus content and its pins were frozen *before* their
atomic commit, so the freeze necessarily ran against a dirty tree. Reproducibility is
anchored by the canonical `corpus_hash` over actual frozen content, not by the commit SHA;
the hash has since been reproduced four times (initial freeze, post-amend re-freeze, and
twice inside sandboxed tests from different working directories), all `sha256:c6602780…`.

Splitting the commit to obtain a cosmetically cleaner SHA was considered and rejected: it
would trade the atomic landing of corpus-plus-pin (no commit exists where the corpus is
present but unpinned) for a provenance detail the content hash already covers.

## Freeze tool: three safeguards

`harness.data.freeze_holdout` is the only writer of both pins.

1. **All-or-nothing.** If any case's observed decision, finding set or trajectory disagrees
   with declared truth, the run aborts nonzero, writes **neither** pin, and records no
   completed freeze. Precisely scoped: this is a per-run property, **not rollback** — a
   failed run does not delete a seed pin left behind by an earlier interruption.
2. **Never overwrites.** An existing manifest refuses the freeze at *any* requested version.
   There is no `--force`.
3. **Initial freeze only.** No correction path, no migration path.

### Completion-marker recovery (not pair atomicity)

`os.replace` makes each individual write atomic, but a two-file write is not transactional.
Rather than claim otherwise, the sequence is made **recoverable by ordering**: the seed pin
is written first and the manifest **last, as the completion marker** safeguard 2 keys on.

* Interrupted before the manifest → no marker exists → a plain rerun is permitted and
  **regenerates** the seed pin from the seed fixtures. It never trusts a pin it finds on
  disk (tested by planting a syntactically valid but wrong pin into an interrupted state).
* Once a manifest exists → re-freeze is refused.
* Invariant: **if the manifest exists, both pins were written.**

### Correction and versioning limitations

P2 supports the initial freeze only. Correcting a case, or producing a holdout v2, requires
a **separately designed versioned-corpus migration that preserves the previous manifest and
corpus** rather than replacing them. A half-built correction path inside the freeze tool was
prototyped and removed: it recreated the silent-replacement hole the tool exists to prevent.
The manifest's `corrections` key is written as an empty list and reserved for that design.

**Cardinal rule.** A correction is never used to make a failing case pass. If the harness
disagrees with a holdout case, that is a *result* — investigate the harness. Editing the
case to match current behavior converts the holdout into a mirror of the code it exists to
test. Safeguard 1 is what makes this enforceable rather than merely stated: you cannot
quietly re-freeze around a failure.

## Isolation

* **Static.** No module under `agent/`, `generate/`, `rules/`, `api/`, `llm/`, `normalize/`
  or `store/` contains any textual route to the holdout. The enforced property is the
  absence of such a route in source — **not physical impossibility at runtime**, which a
  text scan cannot demonstrate.
* **Sole consumer.** An allowlist of **exact module paths, never a directory prefix**. A
  prefix such as `eval/` would silently authorize `eval/run.py`, the development eval
  runner. Each addition is a visible, reviewed diff.
* **Behavioral.** The rule-promotion gate corpus, the generator and the anomaly corpus are
  all verified disjoint from the holdout.
* **Agent/truth.** The agent-input path returns a `Dossier` and structurally cannot surface
  `DecisionTruth`, matching the seed corpus contract.

## Seed-fixture immutability

P2 motivated closing a long-standing gap: "the 8 seed fixtures are never edited" had been an
invariant since slice-1 but was documented only. `seed/hash_pin.json` now pins the corpus
hash and all 8 per-case hashes, so any edit — including a well-meaning one made to get a
test passing — fails loudly.

## Reporting: a wholly separate artifact

`harness.eval.holdout_report` produces `run_type: "deterministic_holdout"`, written to
`artifacts/p2_holdout/`, reusing `build_scientific_report` verbatim. It contributes corpus
selection, provenance, labelling and an integrity guard — no new statistics.

**Never averaged with the development report.** The generator corpus is labelled
`sample_relationship: "in_sample"`; the holdout is `"out_of_sample"` and additionally
`synthetic` / `hand_authored` / `in_coverage`. A merged headline would be neither figure,
and the 420-case in-sample corpus would numerically drown 18 held-out cases.

**Integrity guard.** The report re-verifies the live corpus against its frozen manifest at
publication time and raises — writing neither JSON nor Markdown, and not even creating the
output directory — on drift. The immutability suite already asserts this, but an artifact
embedding `corpus_hash` as a reproducibility claim must not depend on a test having been run.

**Rule exposure is published beside the paired diff.** The promoted `capital_age_ceiling`
rule cannot fire on this corpus — every company is years old — so its paired delta is zero.
A bare zero invites the reading "the rule was validated out-of-sample", which this corpus
cannot support. The artifact therefore carries `rule_applicability_count: 0` and an explicit
`exposure_note` stating that the rule was **evaluated but not triggered**, that a zero delta
means **not exercised** rather than no observed effect under exposure, and that this is
**not** evidence of out-of-sample rule safety or benefit. The Markdown renderer prints both
immediately after the delta, so the caveat travels with the number it qualifies.

### Empirical rate vs. structural invariant

The observed false-approval rate on this corpus is `0/14`, **Wilson 95% CI [0.0%, 21.5%]**.

It is an **empirical, corpus-scoped** result: it holds because the deterministic checks
actually catch each authored case, and it could in principle be nonzero. The interval is
part of the claim — 14 observations do not exclude a materially higher underlying rate, and
publishing "0/14" bare would overstate it. This is why house style from P5 onward requires
raw k/n *and* a Wilson interval wherever a rate is published, including inside the frozen
manifest rather than only in a downstream report.

By contrast, **SYSTEM-origin findings imply no approve** is a *structural* invariant: a
T-finding is always `unexplainable`, and `decision_for_severities` always escalates on any
unexplainable finding. It is reported as a structural invariant citing its test, and never
given a confidence interval — a CI on a code guarantee would misrepresent a proof as a
sampled observation.

## Backlog, logged not fixed

`check_completeness` checks `legal_name` emptiness on the **registry only**, while `tax_id`
emptiness is checked on all three documents. A blank `circular.legal_name` or
`ubo.legal_name` therefore produces no E2 at all.

This is a genuine coverage asymmetry and a **P4/red-team backlog candidate**. It is
deliberately *not* fixed here: P2 must never drive a change to the checks it measures, and
`hold_15` passes on the registry field either way.

## Language scope

Claims are validated under the evaluated corpora and tested scenarios — never "proven".
Nothing here is a production-readiness, fraud-detection, or regulatory-compliance claim. The
LLM's role remains bounded: it does not participate in runtime rule execution or the final
decision, only proposing schema-constrained candidate parameters, and no rule becomes active
without the validation gate plus human approval. The holdout corpus was designed, reviewed,
approved and frozen under human control; freeze-time screening uses the offline
deterministic stub.
