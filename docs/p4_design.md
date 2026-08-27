# P4 — design note: the frozen out-of-coverage red-team corpus

Status: **P4(a) and P4(b) landed on `main`; P4(c) implemented.** P4(a) — the eval-only
contracts, strict loader, freeze tool, additive provenance helper, all 30 primary fixtures, the
frozen manifest, and the integrity / isolation / immutability test suites — merged via PR #6
(`742fcb2`). The corpus is frozen at `corpus_hash
sha256:1291bb82b47a049ca53c1b04da837885099f13e56b3ceccae11a9284536b28ee`, with
`deterministic_blindness_admission 30/30` recorded **by construction** (§ Interpretation).

P4(b) — `src/harness/eval/redteam_record.py`, the offline corpus-admission-and-provenance
**record** (deliberately never called a "report", "result", or "score") — merged via PR #7
(`278fab5`). § Commit / artifact boundaries carries its exact rulings.

P4(c) — `src/harness/eval/redteam_live.py`, the key-gated live-triage measurement — is
implemented and under review. It is the first and only P4 surface that produces an empirical
number; § Live-triage protocol is its fixed contract. This design was frozen before live
execution. A live P4(c) session was subsequently run on 12 August 2026; see the published
[evidence](evidence/p4c/20260812T195516Z-p4c/redteam_live_session_public.json) and
[provenance record](evidence/p4c/20260812T195516Z-p4c/PROVENANCE.md).
This note is the contract, refined across adversarial design review and approved as a
design input. `DECISIONS.md` carries the short record for each landed phase; this file is the
long form. It deliberately mirrors `docs/p2_design.md` in structure, because P4 is the
structural mirror of P2.

## Motivation and limited purpose

P2 froze an **in-coverage** holdout: novel *cases* whose defects all fall inside the
deterministic checks' existing scope. It measures generalization to new cases, not new
phenomena, and its observed false-approval rate is `0`.

P4 freezes the complement: an **out-of-coverage** red-team corpus. Every case carries a
**synthetic threat-model concern that the human-approved label judges to warrant
`request_more_info` or `escalate`**, and that falls **outside** every deterministic check's
declared scope under the pinned configuration. The intended, honest finding concerns residual
risk: **under the pinned offline deterministic configuration and promoted-rule set, admitted
cases approve with no findings by the corpus admission criterion. Whether a live AI-triage run
detects or raises concern on them is a separate, non-deterministic P4(c) measurement.** P4
**measures the system's behavior on this fixed synthetic out-of-coverage challenge set; it does
not estimate or quantify general real-world residual risk, nor does it drive such risk to
zero.**

**What P4 is not.** P4 is a **synthetic, author-constructed, non-blinded** out-of-coverage
red-team evaluation. *Author-constructed* here is a statement about where the corpus came
from — built inside this project by the party running the evaluation, with knowledge of the
coverage, rather than sourced externally or blindly — and **not** a claim about who typed
the case texts: those were authored with LLM assistance and human-reviewed (see
`ERRATA.md`). It is explicitly **not**:

* a real adversarial assessment or penetration test,
* an external or independently-labelled benchmark,
* a blinded study (`checks.py`, `coverage.py`, and the promoted rule were inspected before
  the cases were constructed, so that they fall *outside* coverage — which requires knowing
  the coverage),
* a real-world robustness, fraud-detection, or production-readiness claim.

The load-bearing, defensible claim is narrow: **under the pinned offline deterministic
configuration and active promoted-rule set, these 30 author-constructed threat-model concerns
are not detected by the deterministic layer, by construction of the admission gate.** Whether the
live AI-triage layer catches any of them is a separate, non-deterministic measurement
(§ Live-triage protocol), never folded into the frozen offline artifact.

## Relationship to the `FAR = 0` invariant

The `false_approval_rate = 0` invariant is **scoped to evaluated in-coverage corpora** (seed,
generator, P2 holdout). P4 is explicitly outside that scope. Deterministic approval of
admitted P4 cases is an intentional corpus-admission property, not a violation of the
in-coverage FAR invariant; it is **not reported as an empirical rate**, and it is not
comparable to P2's `0/14`. Only **SYSTEM-implies-no-approve** remains a structural invariant,
and it is untouched here.

## The corpus: 30 cases, six categories × five

Curated, never a generator draw: `src/harness/data/redteam/case_rt_01..30.json`. Case texts
were authored with LLM assistance and their labels reviewed and approved by the author —
exactly as each fixture's `authoring_assistance` / `label_review` fields record (see
`ERRATA.md`).
The matrix is **fixed** as approved. Observed action distribution — **kept as an observed
result, never a quota** — is **23 request_more_info / 7 escalate / 0 approve**.

`appropriate_action` domain is **`request_more_info | escalate` only**. `approve` is excluded:
a genuinely-approvable case has no place in a false-approval denominator and belongs to a
separate, later negative-control surface (§ Out of scope).

Every case is a **candidate built from a clean base**: constructed so that every deterministic
check plus the promoted rule pass and the case would decide `approve`, with exactly one
out-of-coverage anomaly injected into a field or semantic that no check reads. It becomes an
**admitted** case only after the full frozen-configuration blindness gate runs at freeze time
(§ Blindness admission). The R6 unit probe (below) clears one code path, not an entire case.

### Self-contained-evidence requirement

Every primary P4 case must contain a **self-contained synthetic dossier fact** that
independently supports its `request_more_info` or `escalate` label. A case must **not** depend
on:

* an unstated real-world lookup or external mutable database (notary registries, sanctions
  lists, company registries, maps),
* speculative inference from a company name to its actual activity,
* the assumption that a registered address necessarily equals an operating site,
* geography inference.

Each row therefore records `self_contained_evidence`: the exact dossier fact carrying the
label. `external_dependency` is one of `none`, `pinned_rule_parameter`, or
`pinned_library_behavior` — an **external mutable lookup is forbidden** for the primary corpus.

### Field surface (code-verified)

Fields **no** deterministic check reads under the pinned config, and therefore the raw
material for out-of-coverage cases: `registered_address`, `company_status`, `notary_reference`
(**not even presence-checked** — a correction to an earlier assumption that it was
presence-only), `ownership_path`, `authority_type`. `incorporation_date` and `share_capital`
are read **only** by the promoted `capital-age-v1` rule, and only inside its box
(`0 ≤ age_days ≤ 90` **and** `share_capital > 75,000,000`; the template self-guards
`age_days < 0 → None`). `legal_name` is read only as a normalized matching key by
`check_identity_consistency`, never for sector/activity semantics.

### R1 — internally contradictory declared-premises description

The contradiction lives entirely within `registry.registered_address` (the only document
carrying an address). Each declaration asserts two mutually incompatible premises facts; this
is **not** a generic registered-address plausibility claim and does **not** assume an address
equals an operating site. All five are `request_more_info`: a self-contradicting premises
declaration warrants verification, not a misconduct finding. `external_dependency: none`.

| # | Action | self_contained_evidence (`registry.registered_address` asserts both) |
|---|---|---|
| R1-1 | RMI | "15.000 m² ağır döküm ve haddehane tesisi" (heavy foundry + rolling mill) **and** "Bina 3, Kat 8, Daire 42" (8th-floor residential apartment unit) |
| R1-2 | RMI | "açık deniz platformu operasyon üssü" (offshore-platform operations base) **and** "2. kat, 55 m² kiralık ofis" |
| R1-3 | RMI | "500 hektar besi çiftliği ve tarımsal üretim tesisi" **and** "Çarşı, Dükkan No. 12, 18 m²" (18 m² arcade shop unit) |
| R1-4 | RMI | "Sanal Ofis paylaşımlı adres, Posta Kutusu 214" (shared virtual-office mailbox) **and** "ana üretim tesisi bu adreste" (main production facility at this address) — the virtual-office indicator is stated in-dossier, not looked up |
| R1-5 | RMI | "petrokimya üretim ve tehlikeli madde deposu" **and** "apartman zemin kat daire" (ground-floor apartment flat) |

Coverage exclusion (all R1): `registered_address` read by no check; no field interprets
premises semantics.

### R2 — company status / lifecycle inconsistency

| # | Action | self_contained_evidence | Coverage exclusion | ext_dep |
|---|---|---|---|---|
| R2-1 | escalate | `company_status = "liquidation"` on a dossier submitted to onboard/transact | `company_status` read by no check | none |
| R2-2 | RMI | `company_status = "suspended"` | same | none |
| R2-3 | escalate | `registry.incorporation_date` > `registry.document_date` (document predates incorporation) | `incorporation_date` read only by F1, self-guarded `age<0 → None`; keep `capital ≤ 75M` | none |
| R2-4 | escalate | `incorporation_date` later than **all** document_dates (future incorporation) | same (age<0 guard) | none |
| R2-5 | escalate | `incorporation_date` > `circular` & `ubo` document_dates but ≤ `registry` date | same | none |

Rationale: liquidation and the three temporal impossibilities are direct, self-contained
impossibility/lifecycle facts → escalate; suspension may be administrative/curable → RMI.

### R3 — nominee / indirect-ownership narrative (free-text `ownership_path`)

| # | Action | self_contained_evidence | ext_dep |
|---|---|---|---|
| R3-1 | escalate | a declared-UBO `ownership_path` states "açıklanmayan bir asıl için %100 elde tutulmaktadır" (held 100% on behalf of an undisclosed principal); all ≥25% owners declared, arithmetic valid | none |
| R3-2 | RMI | `ownership_path` states an offshore chain "BVI SPV → Cayman SPV → …" | none |
| R3-3 | RMI | `ownership_path` states "dolaylı, doğrulanamayan zincir" (indirect, unverifiable chain); all ≥25% direct holders declared | none |
| R3-4 | RMI | `ownership_path` states "mütevelli sıfatıyla; nihai menfaat sahibi teyit edilecek" (acting as trustee; UBO to be confirmed) | none |
| R3-5 | RMI | `ownership_path` explicitly states the beneficiary is "hâlihazırda görevde bir milletvekili" (currently-serving MP — explicit PEP self-disclosure) | none |

Coverage exclusion (all R3): `check_ubo_derivation` reads name + `ultimate_ownership_pct` +
threshold membership only; `ownership_path` is never parsed. Rationale: only R3-1 carries an
independent self-contained escalation fact (an *undisclosed* ultimate principal); offshore
layering, unverifiable indirect chains, trustee arrangements, and explicit current-PEP
disclosure are clarification / enhanced-due-diligence signals, not automatic escalation.

### R4 — notary / instrument integrity (fully-unread `notary_reference`)

| # | Action | self_contained_evidence | ext_dep |
|---|---|---|---|
| R4-1 | RMI | `notary_reference` states foreign notarisation **and** "apostil/tasdik: eklenmemiş" (apostille not attached) | none |
| R4-2 | RMI | `notary_reference` states it certifies **only** "sureti aslına uygundur" (a true-copy stamp), not the signatories' authority | none |
| R4-3 | escalate | `notary_reference` embeds a **future** notarisation date (later than the circular's document_date) | none |
| R4-4 | RMI | `notary_reference` self-describes as "onaysız / taslak — noter tasdiki beklemede" (unapproved draft, notarisation pending) | none |
| R4-5 | escalate | `notary_reference` names a **different legal entity** than the dossier's company | none |

Coverage exclusion (all R4): `notary_reference` read by no check, not even completeness.
Rationale: R4-3 (temporal impossibility) and R4-5 (conflicting legal instrument) are
self-contained escalation facts; R4-1/-2/-4 are missing/insufficient-notarisation signals →
RMI. R4-2 replaced an earlier "different province" idea, which is not independently suspicious
(notarisation can legitimately occur elsewhere); R4-4 was reworked to be self-contained rather
than depend on an external "revoked notary" registry lookup.

### R5 — capital plausibility outside `capital-age-v1`'s box

| # | Action | self_contained_evidence | Coverage exclusion | ext_dep |
|---|---|---|---|---|
| R5-1 | RMI | `age ≈ 730d` (>90) with `share_capital = 5,000,000,000` | F1 → None because `age_days > max_age_days(90)` | pinned_rule_parameter |
| R5-2 | RMI | `age < 90d` with `share_capital = 70,000,000` (just under the promoted ceiling) | F1 → None because `capital ≤ max_capital(75M)` | pinned_rule_parameter |
| R5-3 | RMI | established co. (`age>90`) with `share_capital = 10,000` (nominal floor) beside a large declared operation | no check has a capital **floor**; the rule flags only capital *above* a ceiling | none |
| R5-4 | RMI | `age = 30d`, `share_capital = 74,999,999` (one unit under ceiling) | F1 → None (`capital ≤ 75M`) | pinned_rule_parameter |
| R5-5 | RMI | `age = 91d`, `share_capital = 500,000,000` (one day over the age window) | F1 → None because `age_days(91) > 90` | pinned_rule_parameter |

Rationale (all R5): capital plausibility is a clarification (source-of-funds) signal → RMI.

### R6 — completeness-asymmetry / field-integrity

| # | Action | self_contained_evidence | Coverage exclusion | ext_dep |
|---|---|---|---|---|
| R6-1 | RMI | `circular.legal_name = ""`; registry+ubo present & consistent; tax_ids identical | completeness flags `legal_name` emptiness on **registry only** (logged asymmetry); A1 needs `all_same_entity` and `legal_names_match(name,"")=False` (probe-confirmed) → no A1; identical tax_ids → no A2 | pinned_library_behavior |
| R6-2 | RMI | `ubo.legal_name = ""` (same asymmetry, ubo side) | same | pinned_library_behavior |
| R6-3 | RMI | `notary_reference = "N/A"` (present-but-placeholder) | `notary_reference` unread; "N/A" is non-empty | none |
| R6-4 | RMI | a `UboEntry.ownership_path = ""` / "N/A" | `ownership_path` read by no check | none |
| R6-5 | RMI | `tax_id = "0000000000"` consistent across all three documents | A2 fires only on a mismatch (identical zeros → none); "0000000000" is non-empty → no E2 | none |

Rationale: all RMI. R6-5 is not escalated: no existing documented project policy classifies a
placeholder tax ID as escalation-level (the only tax-id escalation is A2, a cross-document
*mismatch*, which this is not); a placeholder identity warrants verified tax-registration
evidence, and inventing an automatic-escalation rule here is out of scope.

## Label and provenance contract

Each case carries a structured label block, truthful about how the label was produced:

```
label_basis: "threat_model_judgment"     # a human threat-model judgment about the
                                          # appropriate action, NOT externally-validated truth
label_review: "human_approved"            # reviewed and approved by a named human
authoring_assistance: "llm_assisted"      # where LLM assistance was used in authoring
appropriate_action: request_more_info | escalate
self_contained_evidence: "<the dossier fact carrying the label>"
external_dependency: none | pinned_rule_parameter | pinned_library_behavior
out_of_coverage_rationale: "<which check's declared scope this falls outside>"
```

The term `"human_threat_model"` is **not** used anywhere (it repeats the P2 provenance error
of describing LLM-assisted authoring as human-only). The label is a human-reviewed judgment
about the appropriate action; it is **not** externally-validated ground truth, and P4 makes no
independent-labeller-agreement claim.

## Pinned-config deterministic-blindness admission gate

**Admission criterion (tightened contract).** Under the frozen offline deterministic
configuration and active promoted-rule set, admission of a case requires:

* final decision `approve`,
* **zero deterministic and SYSTEM-origin findings, and no other finding of any origin**,

observed at freeze time. A case failing this condition is **inadmissible** for the primary P4
corpus — it is redesigned, excluded, or reclassified, and **never relabeled** to preserve the
corpus. "Out of coverage" is a property of *this pinned config*, pinned alongside it.

The frozen manifest records, per case: the observed final decision, the complete observed
finding set, the promoted-rule IDs and hash, the offline stub identity, the
coverage-catalog exclusion rationale, and any `external_dependency`.

**P4-specific manifest schema.** P4 uses its **own manifest schema and `schema_version`**, not
P2's. It reuses P5's canonical **contract-normalization and hashing primitives**
(`provenance.hash_corpus` / `sha256_of` over contract-normalized content — not raw bytes, since
`core.autocrlf=true` makes byte pinning fragile), but the schema itself is distinct: P4 carries
a different label contract (`label_basis` / `label_review` / `authoring_assistance` /
`appropriate_action` / `self_contained_evidence` / `out_of_coverage_rationale`), a different
admission criterion (blindness rather than P2's decision/finding/trajectory match), a
dependency taxonomy (`external_dependency`), and different provenance requirements
(promoted-rule IDs/hash + offline stub identity + per-case observed output at freeze). Reusing
P2's manifest schema would conflate two corpora with genuinely different contracts, so the
schemas are kept separate even though the low-level hashing primitives are shared.

### P4(a) pre-authoring acceptance check (resolves the offline-stub assumption)

The admission contract assumes the offline path emits no non-deterministic/triage finding, so
that "zero findings of any origin" is achievable under the stub. This assumption is **enforced,
not taken on faith**: before authoring the full corpus, run an end-to-end representative-case
probe under the **exact pinned offline configuration**. The probe must establish the origins
and final-decision behavior actually emitted by the offline path. If any offline
non-deterministic/triage finding can occur, the admission contract must be **revised and
re-approved before fixture authoring** — it must not be silently weakened.

### Interpretation: admission is by construction, not an empirical FAR

Because every admitted case is *required* to approve with no findings, the offline
deterministic surface is tautological. It is reported as:

```
deterministic_blindness_admission: 30/30 cases met the pinned-config admission criterion
(final approve, zero findings of any origin). This is by construction and is NOT reported as
an empirical false-approval rate or a confidence interval.
```

This is the same principle by which SYSTEM-implies-no-approve carries no Wilson CI: a
confidence interval on a by-construction property misrepresents a proof as a sampled
observation. The empirical results of the P4 report begin with the **live** measurements
(§ Live-triage protocol), if and when those are performed.

## Pinned dependencies and the versioned-re-triage rule

Two families of cases depend on the pinned configuration rather than on an intrinsic,
config-independent defect:

* **`pinned_rule_parameter` (R5-1, R5-2, R5-4, R5-5)** — admissible because they sit *outside*
  the promoted `capital-age-v1` parameter box (`max_age_days=90`, `max_capital=75,000,000`).
  A future rule/config change (a wider age window or a lower ceiling, or a new floor rule)
  could bring one inside coverage.
* **`pinned_library_behavior` (R6-1, R6-2)** — admissible because
  `legal_names_match("", name)` returns `False` under the pinned rapidfuzz behavior
  (`token_set_ratio(key, "") = 0.0`, `matching_key("") = ""`, probe-confirmed). A library
  version bump could flip this, firing an A1 and disqualifying the case.

**Cardinal rule.** If a later configuration or library version causes one of these cases to
start being detected, that is a **versioned re-triage result** — recorded as a new observation
against the new config — **never** a retroactive edit to the frozen fixture or its label.
Editing a red-team case to keep a number stable would convert the corpus into a mirror of the
code it exists to probe. This is the exact analogue of P2's cardinal rule (a correction is
never used to make a failing case pass) and of the freeze tool's non-overwrite guarantee.

## Strict separation from other corpora and surfaces

P4 is kept structurally and reportorially distinct from every other measured surface:

* **P2 in-coverage holdout** — opposite purpose (in-coverage generalization vs. out-of-coverage
  residual risk); different loader, directory, manifest, and report. P4's FAR is not a P2
  regression, and P2's `FAR=0` says nothing about P4.
* **P5 development reporting** — the generator/dev corpus is in-sample tuning data; P4 is
  neither averaged with it nor rendered in the same artifact.
* **Anomaly / tuning corpus** (`anomaly_corpus.py`, the triage FPR/recall builders) — P4 is
  verified **disjoint** from it; the anomaly corpus feeds the Faz-3 learning loop, and P4
  must never feed tuning or rule promotion. Isolation is enforced as in P2: a static
  import-graph scan proving no route from `agent/`, `generate/`, `rules/`, `api/`, `llm/`,
  `normalize/`, `store/`; a sole-consumer allowlist of **exact module paths** (never a
  directory prefix — the `eval/run.py` lesson); behavioral disjointness; agent/truth
  separation (the loader returns a `Dossier`, never `DecisionTruth`).

## Live-triage protocol — the P4(c) contract (non-deterministic, never in the offline artifact)

`src/harness/eval/redteam_live.py`. The first and only P4 surface producing an empirical
number. Fixed in full before any live run, so no definition can be chosen after seeing a
result.

**What is measured.** The **triage layer**, not "the AI": the synthesis proposal cannot change
an outcome (the guardrail decides), so the only semantic signal is a finding with
`origin="ai_triage"`. The primary outcome is the **clean-run live-triage intervention rate**
`k/n`, where `k` counts cases whose final decision is not `approve` because of at least one
domain triage finding. It is never called a false-approval rate, a detection rate, or an
accuracy, and the labels it is read against remain human-reviewed threat-model judgments, not
externally established ground truth.

**Whole-attempt validity, never a variable denominator.** A terminal SYSTEM/T0 failure in ANY
case invalidates the entire attempt. Contaminated cases are never excluded to compute a rate
over a clean subset: that would make denominator membership depend on operational behavior and
invite selective-exclusion bias. An invalid attempt carries no rate, no interval, and no
numerator/denominator — those fields do not exist in its shape. A SYSTEM-origin non-approval is
never an intervention and never evidence against a false approval; both LLM nodes emit a T0
that escalates, so a provider outage would otherwise manufacture a flawless-looking result out
of pure failure. (Consistent with P5's guardrail-is-not-detection ruling.)

**Structural invariants, asserted rather than measured.** For an admitted case with no
deterministic findings, any domain triage concern is necessarily outcome-changing — severity is
clamped to `explainable`/`unexplainable` and the guardrail takes the maximum — so X1 yields
`request_more_info` and X2 yields `escalate`. There is therefore **no "advisory note that did
not alter the outcome" category**; the property is proven by test against the real graph
instead of being reported as a metric.

**Five terminal session states.** `completed` (three valid attempts; the only state carrying a
distribution) · `operational_failure_exhausted` · `coverage_violation` · `live_call_budget_exhausted`
· `input_integrity_failure`.

A **coverage violation** is any finding whose origin is neither `ai_triage` nor `system`, at
**any severity** including `info` — the frozen admission asserts zero findings of any origin, so
an info-severity deterministic finding disproves it even though it changes no decision. It is
terminal, session-wide, and takes precedence over a co-occurring T0, because it says the frozen
out-of-coverage admission no longer describes the executed configuration rather than reporting a
provider problem. Evidence recorded: final decision, the complete observed finding set, the live
corpus and promoted-rules hashes, and a reason code. The remedy is a versioned re-triage, never
an edit to a fixture, label, hash, or the frozen manifest. **`input_integrity_failure`** is
distinct: a frozen case could not be loaded or executed *after* preflight validated the whole
corpus.

**Session policy.** Exactly **three valid attempts** targeted, at most **two replacements**, an
absolute cap of **five attempts**. The session stops at three regardless of how interesting the
observed variability looks.

**Preflight.** The key must be present and `validate_frozen_contract()` must pass before any
provider call and before any directory exists. Drift fails closed with no artifact and no calls.

**Provider pin and cost control.** `temperature=0.0`, explicit `max_tokens=1024`, per-request
`timeout=60.0`, and `max_retries=0` at the SDK so the P4(c) wrapper is the only retry layer.
The wrapper retries a single structured call — never a node, case, or attempt — at most three
outbound requests per call site, backoff `[1.0, 4.0]` s each with non-negative jitter capped at
1.0 s. Intermediate failures never reach the graph and never become T0 findings; only the
terminal exception does. The 60 s timeout bounds **one SDK request**, not a logical graph call,
whose documented upper bound is **187 s** including the jitter cap. The graph is linear and
`synthesize` is reached even when `ai_triage` failed, so a case has two call sites and at most
six outbound requests. Maximum per clean attempt is `30 × 2 × 3 = 180`; the **session ceiling of
600** is a cost-stop policy, not a promise that five fully retried attempts can complete.
Enforcement is **reservation**: a case never starts unless its full worst case still fits, so
the ceiling cannot be exceeded rather than merely detected.

**Presentation.** Each valid attempt reports its own raw `k/n`, a Wilson 95% interval, and full
provenance (model, transport settings, the three prompt hashes, corpus and promoted-rules
hashes, session id). Across attempts only a **descriptive distribution** is published — the
ordered results plus min, max, and median. Numerators and denominators are **never pooled** and
no interval is computed across attempts: the same cases repeat, so outcomes are correlated by
case difficulty, prompt, model state, and provider behavior, and treating them as independent
would be overconfident in exactly the way this project avoids. Temperature is pinned but the
provider path exposes **no seed**, so repeated attempts estimate provider-side variability, not
seeded replication. **A result of zero interventions is a valid measured outcome**, published
as-is with its interval — never an implementation failure, and never a reason to adjust prompts,
rules, labels, or fixtures.

**Live calls are operator-initiated only.** No test in the project performs a provider call and
none is key-gated; the sole live path is `harness-p4c-live --confirm-live`. Without the flag the
command performs a free dry run: key check, preflight, the full banner, zero calls, no output
directory. Verbatim model text is preserved for auditability, always carrying an explicit
untrusted-model-output marker that is never separated from the quote.

## Commit / artifact boundaries

* **P4(a)** — the frozen corpus, structured labels, hash-pinned manifest, eval-only loader,
  freeze tool (P2-style: all-or-nothing, never-overwrite, initial-freeze-only,
  completion-marker ordering — the freeze-time validation asserts the *blindness gate* rather
  than P2's decision/finding/trajectory match), per-case full-case blindness assertions, and
  isolation/immutability tests.
* **P4(b)** — a separate **offline corpus-admission-and-provenance RECORD**, never a "report",
  "result", or "score" (`run_type: redteam_out_of_coverage`, distinct from P2's
  `deterministic_holdout` and from the dev report). It records the frozen corpus identity, the
  pinned configuration and promoted-rule IDs/hash, and the **30/30 by-construction admission
  result** — **not** deterministic effectiveness, an offline performance measurement, or
  empirical residual risk. Four rulings fix its shape:
  * **No `build_scientific_report`, no `RunRecord`.** The P4 label is a threat-model judgment,
    not a `DecisionTruth` prediction; diffing it against a fresh run would manufacture an
    empirical false-approval rate out of a by-construction admission property.
  * **No agent execution anywhere — not even as an unpublished guard.** Current-config
    blindness is re-observed exclusively by `tests/test_redteam_fixtures.py`; the record only
    ever republishes the manifest's historical `observed_at_freeze` block, unchanged, and
    labelled `observation_kind: "historical_at_freeze"`.
  * **Configuration drift hard-fails; there is no `stale_config` artifact.** The publication
    guard independently re-validates the complete frozen-manifest contract — schema identity,
    the fixed case-id set, the canonical corpus hash, all 30 canonical per-case hashes, every
    live label field the record copies, the structural completeness and internal
    cross-linking of the historical admission observation, and the CURRENT offline-stub /
    promoted-rule identity against the frozen pin — and writes nothing on any mismatch. A
    future legitimate rule promotion will therefore fail this guard; the remedy is a
    separately designed **versioned re-triage**, never a re-freeze or a manifest edit.
  * **No `sample_relationship` / `out_of_sample` language.** P4 draws no sample and supports
    no generalization claim; the corpus is described as `corpus_relationship:
    "fixed_synthetic_challenge_set"` and `authoring_context: "author_constructed_non_blinded"`.
  Publication is lifecycle-controlled: every record lands under a generated, never-reused
  `run_id` directory (`artifacts/p4_redteam/<run_id>/`), claimed with an exclusive `os.mkdir`
  (no check-then-act gap); a valid record is a directory containing EXACTLY the two expected
  regular files, Markdown written last as the completion marker; a pre-existing directory at
  any state is never overwritten or reused; and a post-claim write failure whose cleanup also
  fails raises a distinct, chained `RecordPublicationCleanupError` naming the incomplete
  directory rather than silently discarding or reusing it. It reuses only P5's provenance
  primitives (`build_run_provenance`, `hash_corpus`, `sha256_of`, `hash_promoted_rules`) and
  carries two Git SHAs, kept in separate sections with separate notes:
  `run.git.commit_sha` (the code that generated this record) and
  `corpus.frozen_git.head_commit_sha` (the dirty bootstrap context the freeze ran against) —
  neither is the corpus's reproducibility anchor, which remains `corpus.corpus_hash` alone.
* **P4(c)** — the key-gated live-triage surface (`run_type: redteam_live_triage`, output root
  `artifacts/p4c_live/`), a distinct run type and artifact for live-provider measurement, never
  folded into the offline reproducible record and never compared with it. No test asserts a
  numeric outcome and no test calls a provider. It reuses exactly one thing from P4(b) — the
  `validate_frozen_contract()` drift guard — and nothing from its reporting or publication
  surface. It is also the first caller to pin the shared `AnthropicClient` transport settings,
  which gained two optional keyword-only options (`timeout`, `max_retries`) forwarded only when
  supplied, so every existing caller keeps its current construction and behavior unchanged.
  § Live-triage protocol is the full contract.

* **P4(c-public)** — `src/harness/eval/redteam_public_summary.py`
  (`run_type: redteam_live_public_summary`, tracked root `docs/evidence/p4c/<run_id>/`), the
  sanitized derivative of ONE pinned live session. It exists because the rule above — raw
  sessions are gitignored and never committed — also makes the recorded counts uncheckable
  by anyone outside this machine, and a write-up that cites those counts should be
  verifiable. The exception is narrow and mechanical:
  * **Sanitization is an allowlist, not a filter.** Every source block is projected through
    an exact key set, and per case only `case_id`, `classification`, `final_decision`,
    `triage_severity_codes`, `system_finding_count`, `provider_calls`, `proposal_decision`,
    and `guardrail_overrode_proposal` are carried. `triage_concerns` (with its `detail` and
    model-selected `fields_involved`), `proposal_note`, `findings`, `system_findings`, the
    author label, and `run.python_executable` are dropped, and a walk of the built summary
    refuses any forbidden key at any depth before publication.
  * **It is a re-expression, never a re-analysis.** No provider call, no agent run, no new
    metric. Decision counts and the repeat structure are tabulations of per-case fields the
    source already records; the Wilson intervals are republished exactly as recorded, and
    their interpretation is argued elsewhere rather than restated here.
  * **Pinned by digest.** A session is publishable only if its `run_id` appears in
    `PINNED_SOURCE_DIGESTS` and both files match those digests. The source JSON is read
    once, then hashed and parsed from those same bytes, so the published digest always
    describes the bytes that produced the publication.
  * **Thirteen invariants fail closed before any write**, covering digest identity, source
    schema and run type, run-id/directory agreement, attempt validity, `n` versus case
    count, `k` versus intervention count, decision totals, unique and identical case-id
    sets, summed provider calls against `cost.actual_calls`, distribution agreement, group
    disjointness, a partition totalling 30, and the severity-variable group being a subset
    of the always-intervened group.
  * **Publication is exclusive and never overwritten**, one directory per source `run_id`;
    verification rebuilds into a scratch `--out` and compares digests.

**Reproducible offline reporting and live-provider experimentation are distinct artifacts /
run types** and never share an output surface. The public summary is a third run type, and
it never merges with either: it is a derivative view of one live session, not a report.

## Out of scope for P4

* A negative-control corpus of genuinely-approvable cases (measuring over-triage) — a separate,
  later, named surface with its own denominator, never in P4's false-approval denominator.
* Any schema change to add a structured "business activity" field (R1 uses the existing
  free-text `registered_address`).
* Any change to `checks.py`, `coverage.py`, the taxonomy, or the promoted rule set. P4 probes
  the coverage boundary; it must never drive a change to the checks it probes — including the
  `check_completeness` legal_name/tax_id asymmetry (R6-1/R6-2), logged during P2 and probed
  here, deliberately not fixed.

## Language scope

Claims are validated under the evaluated corpora and tested scenarios — never "proven". P4 is
a synthetic, non-blinded, out-of-coverage red-team evaluation; nothing here is a
production-readiness, fraud-detection, regulatory-compliance, or real-world-robustness claim.
The LLM's role remains bounded: it does not participate in runtime rule execution or the final
decision, only proposing schema-constrained candidate parameters, and no rule becomes active
without the validation gate plus human approval. Every rate that is eventually published
carries raw `k/n` and a Wilson 95% CI (house style from P5), except by-construction and
structural properties, which carry neither.
