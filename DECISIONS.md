# DECISIONS

Design decisions and every assumption made while building slice-1 (and the slice-2
synthetic generator; see the final section). Fixtures under `src/harness/data/seed/` are
read-only ground truth; where the brief's prose and the fixtures disagree, the fixtures
win and the reasoning is recorded here.

## Load-bearing commitments (from the brief)

1. **Three-way decision, no "reject".** The agent may `approve`, `request_more_info`, or
   `escalate`. There is no autonomous reject: in this substrate the agent has no
   authority to reject, so even suspected fraud is an `escalate`. Encoded in
   `GuardrailDecision.final_decision` (a `Literal` with no "reject") and in the guardrail
   policy.

2. **Authority model (no anchor document).**
   - *Identity fields* (`tax_id`, canonical `legal_name`): the Trade Registry document
     (B1) is authoritative. An identity conflict admits no explanation → `unexplainable`
     → escalate (code A2).
   - *Ownership / authority fields*: the document with the most recent `document_date`
     takes precedence, but any delta from an older document creates an EXPLANATION
     OBLIGATION. Whether the obligation is dischargeable is decided by the precise tests
     below, not by a general "anchor". See `harness/agent/guardrail.py` and
     `harness/agent/checks.py`.

3. **Three-condition explainability test (ownership).** A delta between B1 and B3
   shareholder lists is an explainable transfer (B1a → request_more_info) only if ALL
   hold: (i) `ubo.document_date > registry.document_date`; (ii) the name sets overlap
   (≥1 continuing shareholder after normalization); (iii) the arithmetic reconciles as a
   simple transfer — exited holders' total percentage equals the total gained by
   continuing/new holders, with both lists summing to 100. If the lists differ and ANY
   condition fails → B1b → escalate. Date direction alone is neither sufficient nor
   necessary; the three conditions are the rule. Implemented in
   `check_ownership_consistency`.

4. **Ground truth is physically separated.** `Dossier` (agent input) and `Case` /
   `DecisionTruth` (eval only) are distinct models. `loader.load_dossier` returns only a
   `Dossier`; `loader.load_case` is the sole route to truth. Enforced by
   `tests/test_contract_separation.py`.

5. **LLM proposes, deterministic guardrail disposes.** The single LLM call produces a
   `SynthesisProposal`; `apply_guardrail` computes the authoritative decision from
   finding severities and overrides the proposal in BOTH directions (too-lax and
   too-cautious), logging every override.

6. **Deterministic evaluation only.** No LLM-as-judge anywhere. All metrics are
   categorical/set comparisons in `harness/eval/metrics.py`.

7. **`false_approval_rate` is a separate headline metric** — fraction of cases whose
   ground truth is `request_more_info` or `escalate` that the agent approved. Never
   folded into aggregate accuracy.

8. **Turkish normalization is mandatory**, including the diacritic-folding match layer.
   `str.lower()`/`str.casefold()` on Turkish names is forbidden (it inverts the
   dotted/dotless-i rule). See `harness/normalize/turkish.py`.

## Assumptions & interpretations (smallest reasonable, recorded per the brief)

- **`check_ubo_derivation` — slice-1 stub, then promoted to a live node (post-slice-1).**
  During slice-1 the brief scoped this as a stub (`ran=False`, no findings) and the
  read-only fixtures never list it in `expected_trajectory` or `expected_skipped_checks`,
  so it was intentionally kept OUT of the graph. It has since been implemented for real
  and wired in as a genuine node (`extract → … → check_authority_chain →
  check_ubo_derivation → check_completeness → …`). Constraints preserved:
  - **Fixtures were NOT edited.** The node runs on cases 1–7 (producing no findings, since
    every ≥25% owner is declared) and is *skipped* on case_08 (no B3). To keep all 8
    fixtures green without editing them, `trajectory_match`'s skip comparison was relaxed
    from exact-equality to SUBSET (`expected_skipped ⊆ actual_skipped`). This is
    forward-compatibility, not failure-hiding: the presence check still requires every
    expected-to-run check to have run, and the trajectory-membership check still catches
    a skip reported as ran. A correct skip of a check the fixture predates no longer
    spuriously fails.
  - **Taxonomy (new D-family):** `D1a` = an at/above-threshold shareholder is missing from
    `declared_ubo` (incomplete declaration) → explainable → request_more_info; `D1b` = a
    structurally impossible declaration (a declared ultimate percentage outside [0,100],
    or declared holdings summing to >100%) → unexplainable → escalate. Default per the
    stated plan: an incomplete declaration is *explainable*, not fraud-by-assumption; only
    structural impossibility escalates.
  - **Threshold constraint (seed #2):** a shareholder below `UBO_THRESHOLD_PCT` (25%) is
    legitimately absent from `declared_ubo` and is never flagged. Enforced by real code +
    `tests/test_ubo_check.py` (including a run of the real seed #2 dossier), not just a
    docstring.
  - **Scope:** slice-1's flat, direct-holding substrate means "derivation" is a
    threshold membership check; multi-layer ownership-graph derivation and legitimate
    indirect-holding percentage reconciliation remain slice-2 (no graph/network analysis
    here). A declared UBO who is not a direct shareholder is not flagged (may hold
    indirectly; over-declaration is not a risk).

- **Trajectory vs. skipped semantics.** The trajectory is the ordered list of nodes that
  actually RAN. A check that cannot run (missing required document) is recorded in
  `skipped_checks` and its `CheckResult.ran=False` is retained in `check_results`; it is
  NOT appended to the trajectory. This matches case_08's fixture exactly
  (`expected_trajectory` omits the two skipped checks; `expected_skipped_checks` lists
  them). A skipped check appearing in the trajectory, or an empty skipped set when checks
  were skipped, fails `trajectory_match`.

- **Identity check preconditions.** `check_identity_consistency` runs when ≥2 documents
  are present (it needs two to compare). In case_08 (registry + circular present) it runs
  on those two. A1 (info) is emitted only when legal-name SURFACES differ but resolve to
  one entity; byte-identical names produce no finding.

- **A2 vs. benign legal-name variance.** A `tax_id` mismatch is the only identity
  escalation (A2). Legal names that differ in surface but resolve to the same entity with
  consistent tax_ids are benign (A1, info). There is no taxonomy code for "legal names
  genuinely differ while tax_ids match"; no seed exercises it, so no finding is emitted
  for that hypothetical (tax_id / B1 govern identity).

- **B2a / B2b thresholds.** B2b (sum > 100, structurally impossible) is checked before
  B2a (sum < 100, gap closable by one missing record). A positive shortfall is treated as
  "closable by one missing record" (B2a, explainable). Neither has a seed case; unit
  tests are their only coverage (stated in the test docstrings). A float tolerance of
  0.01 percentage points is used for all sum/delta comparisons.

- **Authority staleness threshold** is 365 days (config `AUTHORITY_STALENESS_DAYS`).
  "Materially older" means strictly greater than 365 days: exactly 365 days → C1b, 366 →
  C1a (see `tests/test_authority_check.py`). C2 (declarant is a signatory but authority
  expired before B3) has no seed case; unit-tested only.

- **Matching thresholds** live in `harness/normalize/config.py`: persons use
  `token_sort_ratio ≥ 90`, legal names use `token_set_ratio ≥ 85` (subset-tolerant, so an
  ASCII-degraded variant that drops suffix tokens still matches). `UBO_THRESHOLD_PCT =
  25.0`.

- **Extraction step and `extraction_accuracy`.** The `extract` node re-parses the dossier
  JSON into contract models; the result is exposed as `AgentResult.extracted_dossier` and
  compared field-by-field against the loaded dossier. Because the input is already
  structured JSON, this is ~100% by construction — a deliberate cheap sanity metric.

- **Default eval LLM is the offline `PolicyMirrorStub`** so `python -m harness.eval.run`
  is reproducible without a key. `--live` uses the configured provider. The one live
  end-to-end run (`tests/test_e2e_live.py`) is skipped gracefully without
  `ANTHROPIC_API_KEY`.

- **Model pin.** The Anthropic backend is pinned to `claude-sonnet-4-6`, temperature 0,
  per the brief. The SDK is imported lazily so offline paths never require it.

- **SQLite connection** uses `check_same_thread=False` because the FastAPI TestClient /
  uvicorn may invoke the `act` node from a worker thread; access is serialized within a
  run.

## Slice-2 — synthetic generator (`harness/generate/`)

- **Scope chosen (with the user):** cover ALL taxonomy codes, ~30 variants per code, with
  three difficulty dimensions — clean isolation, boundary/near-threshold, and adversarial.

- **Clean-base + single injector.** Each case starts from a fully-consistent dossier
  (every check clean → approve) and one code-specific injector perturbs exactly one aspect
  to trigger exactly one code, re-establishing consistency everywhere else (e.g. a B1a
  transfer also updates `declared_ubo` and keeps the declarant in the circular). The
  generator computes ground truth directly because it built the case.

- **Metamorphic self-validation is the correctness guarantee.** Every generated case is
  screened by the real harness and its agent finding codes must equal the injected code
  *exactly* (plus correct decision + trajectory). A recipe that trips a second check fails
  loudly (`tests/test_generator.py`). This is the operational meaning of "the generator is
  validated against the fixtures": it is held to the same deterministic harness, and the 8
  read-only seeds remain the independent gold standard (they still pass unchanged).

- **Bug found and fixed via the generator (harness, not generator):** suffix
  canonicalization used naive substring replacement, so the abbreviated `A.Ş.` variant
  `"a ş"` matched *inside* ordinary names ("Mustaf**a Ş**ahin" → "mustaf as ahin"),
  breaking ASCII↔proper matching. Fixed to TOKEN-BOUNDED matching in
  `normalize/turkish.py`; regression test added. The 8 seeds never contained that
  adjacency, which is exactly why statistical scale matters.

- **Adversarial dimension = evaluation mode, not new dossiers.** Two deterministic
  stand-in proposers (`AdversarialStub` = always approve, `OvercautiousStub` = always
  escalate) are run over the generated set. The load-bearing result: under a lax LLM the
  guardrail overrides every non-approve case and **`false_approval_rate` stays 0**; under
  an overcautious LLM it pulls decisions back down. This finally exercises the
  override machinery at scale (the seed set never triggers an override). A live-LLM
  adversarial mode is intentionally deferred (non-deterministic, token cost); the
  deterministic stubs make the safety claim reproducible and offline.

- **E2 isolation.** An empty *mandatory* field cascades into other checks (an empty
  `tax_id` in one doc would read as an A2 identity mismatch). To isolate E2, the generator
  blanks a field CONSISTENTLY across all documents (all tax_ids or all legal_names empty),
  which the completeness check flags as E2 without creating a cross-document mismatch.

- **Trajectory ground truth for generated cases** is computed by `expected_flow()` mirroring
  the graph's check preconditions. That logic is independently anchored by the curated seed
  fixtures (seed #8), so it is not the generator grading its own homework.

## Faz 1+2 — coverage catalog + AI triage layer

- **Coverage catalog (`agent/coverage.py`):** each deterministic check declares its scope
  and taxonomy codes as data. `test_coverage.py` asserts the catalog stays in sync with the
  generator's code set and the graph's check nodes, so it can't silently drift.

- **AI triage layer (`agent/triage.py`, node `ai_triage`):** the LLM flags CONCRETE,
  out-of-coverage red flags the deterministic checks cannot enumerate (e.g. heavy industry
  at a residential address, nominee ownership-path narratives). This is where the agent does
  genuine open-world work — addressing the "the LLM is decorative" critique.

- **Fail-closed / asymmetric initiative (structural, not prompt-based):** triage concerns
  become findings whose severity is CLAMPED to {explainable, unexplainable} in
  `triage_findings()` and fed to the max-severity guardrail. Because findings only ever add
  and the guardrail takes the maximum, the AI can push a decision UP (→request_more_info /
  escalate) but can NEVER pull it down to approve. The safety property does not depend on the
  prompt behaving — it holds by construction. Verified live: no planted anomaly was approved.

- **Calibration is a separate concern from safety.** The first live run held the safety
  invariant but was over-cautious (false positives on 2/3 clean fixtures, one over-escalation)
  — the classic overconfident-LLM failure the selective-prediction literature warns about.
  Fixed by prompt discipline: the triage layer treats the deterministic engine as
  AUTHORITATIVE/SETTLED and must not re-litigate covered or intentionally-acceptable patterns
  (sub-25% shareholders, non-signatory UBOs, family co-ownership, moderate date gaps), must
  not flag out-of-scope missing documents (ID/source-of-funds/PEP), and reserves `unexplainable`
  (escalate) for concrete shell/fraud/sanctions evidence. Result: false positives 2/3 → 0/3
  while all planted anomalies stayed caught. `triage_fpr.py` makes this repeatable: it runs
  triage over many generator-produced clean (NONE) dossiers (each confirmed deterministically
  clean first) and reports false_positive_rate + over_escalation_rate. A first live run
  (n=25) showed a low single-digit false-positive rate with 0% over-escalation — indicative,
  not final: the model is non-deterministic, so a paper-grade figure needs multiple seeds /
  larger n. Formal selective-prediction calibration remains Faz 4.

- **Recall side measured too (`anomaly_corpus.py` + `triage_recall.py`).** A labeled corpus of
  deterministically-clean dossiers, each hiding one out-of-coverage red flag (address/activity
  mismatch, implausible capital, nominee ownership-path, liquidation status, incorporation-after-
  registry, notary-jurisdiction mismatch). Each case is confirmed deterministically clean offline
  (asserted by `test_anomaly_corpus.py`), so only the AI layer can catch it. A live run (n=18)
  gave the full picture: ~4% false-positive rate, 0% over-escalation, ~83% recall. The MISS
  profile is the useful finding: CATEGORICAL/semantic anomalies are caught ~100% (address, dates,
  status, nominee), but the QUANTITATIVE-plausibility anomaly (capital wildly disproportionate to
  company age) is weak (~33%) because it needs numeric + sector reasoning rather than pattern
  recognition. Design consequence: this weak spot should be fixed by CODIFYING a deterministic
  rule (capital-vs-age) via the Faz 3 learning loop — not by pushing the probabilistic LLM
  harder, which would trade away precision. This is the motivating first example for Faz 3.

- **Trajectory impact:** `ai_triage` is an extra node not in the read-only fixtures'
  `expected_trajectory`; the subset-based `trajectory_match` (already relaxed for
  `check_ubo_derivation`) absorbs it without fixture edits. The offline stubs raise no
  concerns, so the deterministic eval (seeds + generated) is unchanged (100% / FAR 0).

## Faz 3 (first pass) — declarative rule schema + validation gate (`harness/rules/`)

Motivated directly by the Faz 4 recall gap: the AI triage layer catches categorical/
semantic anomalies (~100%) but misses a purely quantitative one — share capital wildly
disproportionate to company age — about two-thirds of the time. Rather than push the
probabilistic layer harder (trading away precision), this codifies that one anomaly type
into a deterministic, human-approved check, and builds the general machinery so any future
LLM-detected pattern can be closed the same way.

- **Declarative schema (`rules/schema.py`).** A `RuleTemplate` is vetted, version-controlled
  Python registered once by a developer — exactly like a check in `agent/checks.py`. A
  `CandidateRule` is the ONLY object an LLM-assisted proposer (Faz 3 part 2, not yet built)
  may author: a `template_id` plus a flat `dict[str, float]` of params. There is no field
  for code, severity, or taxonomy code — those are fixed by the template author.
  `validate_params` is the structural fence: every param must be declared by the template
  and within its author-chosen numeric bounds (`ParamSpec.minimum/maximum`), so even a
  hallucinating proposer cannot construct a rule that does anything beyond instantiating a
  pre-approved template with in-range numbers.

- **First template: `capital_age_ceiling` (`rules/templates.py`).** Flags a company younger
  than `max_age_days` declaring `share_capital` above `max_capital`. Severity is FIXED at
  `explainable` by the template (not proposer-settable) — capital size alone is a
  plausibility flag, not proof, matching the project's existing default of "explainable,
  not fraud-by-assumption" (D1a). New taxonomy codes: F1 (a promoted rule fired as its
  template intends) and F2 (a promoted rule raised at runtime — a defensive fail-closed
  guard for a state the gate should have prevented; unexplainable → escalate, so a broken
  rule forces human attention rather than silently vanishing or crashing the whole run).

- **Validation gate (`rules/gate.py`), the strictest reasonable non-regression bar.** A
  candidate rule PASSES iff it produces NO finding at all on any of the 8 seed fixtures or
  the 420 generated cases — mirroring `finding_match`'s exact-code-set semantics rather
  than only checking whether the final decision changes. None of the 428 known cases
  involve a capital/incorporation-age mismatch, so a correctly-parameterized rule passes by
  construction; a badly-parameterized one (too low a capital ceiling, too generous an age
  window) legitimately misfires on the generator's clean bases and is rejected — verified
  by `tests/test_rules_gate.py` with both a passing and a deliberately-bad rule. The gate
  also reports (informationally, not gating) how many of the matching anomaly-corpus cases
  the candidate catches, so a human approver can see efficacy before spending an approval
  cycle; the real recall re-measurement is Faz 3 step 5, after a real promotion.

- **Human-approval store (`rules/store.py`).** `promote_rule` is the only way to persist a
  rule, and it structurally requires (raises `ValueError` otherwise, never silently
  no-ops): a non-empty human `approved_by` identifier, and a `GateResult` for the SAME rule
  with `passed=True`. The store starts empty (no file on disk), so wiring the new check
  node into the graph today is a no-op — zero behavior change for every existing test/eval/
  API call — until a human actually promotes something.

- **Graph wiring (`agent/learned_rules.py`, `agent/graph.py`).** A new node,
  `check_learned_rules`, sits between `check_completeness` and `ai_triage` — the full
  deterministic layer (including any promoted rules) completes before the AI triage prompt
  is built, so `coverage.catalog_summary()` can tell the LLM a promoted rule's code is
  "already covered" and stop re-flagging it. Following the exact precedent set by
  `ai_triage` (an extra node never added to `eval.metrics.CHECK_NODES` or the static
  `COVERAGE_CATALOG`), `check_learned_rules` is likewise NOT added to either — the
  subset-based `trajectory_match` absorbs it without touching any fixture or the two
  existing coverage-sync tests. `coverage.covered_codes()` is deliberately left static
  (reflects only the 5 original checks) so its sync test never depends on filesystem/
  promotion state; a new `learned_rule_codes()` + a dynamic line in `catalog_summary()`
  carry the promoted-rule-aware text instead, computed from an explicit `promoted_rules`
  argument (defaulting to `store.load_promoted_rules()`) rather than a hidden global.

- **Proven offline, end to end, without promoting anything for real
  (`tests/test_learned_rules_graph.py`).** With `promoted_rules=[]` (today's actual
  default) the three sampled seed fixtures decide exactly as before. A deliberately
  malformed rule produces an F2 escalation instead of crashing the run. And the concrete
  payoff: running the SAME implausible-capital anomaly-corpus dossiers that were 100%
  approved (undetected) in Faz 4 through `run_agent` with `promoted_rules=[capital_age_ceiling
  rule]` turns every one of them into `request_more_info` — closing the recall gap
  deterministically, with no LLM call involved in the detection itself.

## Faz 3 completed — proposer, promotion, and the live recall result

- **Rule proposer (`rules/proposer.py`).** `RuleProposal` is the only schema the LLM
  answers here: a `template_id` + numeric `params`, nothing else. `propose_rule` rejects an
  unknown `template_id` (the proposer cannot invent one), rejects out-of-bounds/missing
  params via the same `validate_params` fence, and treats an empty `template_id` as a
  legitimate "nothing fits" decline. A structurally-accepted proposal is still just a
  `CandidateRule` — it is NOT safe-to-promote until it separately clears
  `run_validation_gate` (verified in `tests/test_rule_proposer.py`, including a case where
  a structurally-valid proposal is still gate-rejected for being too aggressive).
  `ScriptedProposerStub` (`llm/stub.py`) makes this testable offline; the real model was
  also run live via `python -m harness.rules.propose`.

- **Live result:** given the 3 `implausible_capital` miss descriptions as evidence, the
  real model proposed `capital_age_ceiling(max_age_days=90, max_capital=75_000_000)` —
  tighter than the example used earlier in this phase — with a sound written
  rationale (avoiding false positives on legitimately young, well-capitalized group
  subsidiaries). The gate passed: 0 regressions across all 428 known cases, 3/3 anomaly
  cases caught. The rule was promoted (`approved_by="Arslan"`).

- **Store location correction.** The rule was first promoted to `artifacts/promoted_rules.json`
  — but `artifacts/` is gitignored (meant for ephemeral eval/report output), which would
  have silently discarded the promotion on the next `git clone`, defeating the entire
  point of a *permanently growing* rule set. `DEFAULT_STORE_PATH` was moved to
  `src/harness/data/promoted_rules.json` (package-relative, like the seed fixtures;
  resolved via `Path(__file__)`, not the process's working directory) and added to
  `pyproject.toml`'s package-data. The already-promoted rule was migrated there and is
  committed to version control, exactly like a seed fixture.

- **Existing-test hermeticity fix.** Because `run_agent` and `coverage.catalog_summary`
  default to loading whatever is *actually* promoted on disk, a test written before Faz 3
  existed — `test_anomaly_corpus.py`'s deterministic-clean check, and `measure_recall`'s two
  offline aggregation unit tests — would start failing the moment a real capital rule was
  promoted (the 3 `implausible_capital` cases correctly stop being "clean", which is the
  *point* of Faz 3, not a bug). Fixed by passing `promoted_rules=[]` explicitly in exactly
  those tests, pinning them to the corpus's original baseline design regardless of
  promotion state — while `measure_recall`'s own default (real state) is deliberately left
  alone, since a live re-measurement is supposed to reflect actual current coverage.

- **Live recall re-measurement (Faz 3 part 5, the capstone payoff):** `triage_recall
  --per-type 3` after promotion reports 3/3 `implausible_capital` cases now excluded from
  `deterministically_clean` (caught by `check_learned_rules`, not the AI layer) — the
  quantitative-plausibility gap is closed at 100%, deterministically, exactly as
  hypothesized after Faz 4. Recall over the remaining 5 (now purely AI-covered) anomaly
  types came out to 93.3% (14/15, missing one `notary_jurisdiction_mismatch` case) —
  in line with the earlier ~83%-with-one-weak-category baseline, confirming the fix was
  targeted and didn't regress anything else. Faz 3 is complete end to end: schema → LLM
  proposer → validation gate → human-approved promotion → measured recall improvement.

## Milestone 2 — operational failures are separated from domain evidence

Full contract in `docs/milestone2_design.md`; the decisions now in effect:

- **`origin` on every finding** (`deterministic` | `ai_triage` | `system`, default
  `deterministic`), plus a bounded `error_kind`. A Pydantic `model_validator` enforces
  `origin == "system"` ⟺ a T-family code, and `error_kind` set ⟺ `origin == "system"` —
  without it, a semantically broken `origin="deterministic", code="T0"` is constructible and
  the claim "the split lives in origin" would be unenforced.

- **Both LLM boundaries are fail-closed.** `ai_triage` and `synthesize` previously let any
  exception crash the whole run — fail-*open-with-a-stack-trace*. Both now emit `T0`
  (`unexplainable` → escalate). Fixing only one would have been arbitrary: same contract,
  same boundary.

- **`F2` retired → `T1`.** A rule-evaluation exception is an operational system failure, not
  domain evidence; modelling it as a domain code polluted rule/triage quality metrics with
  infrastructure noise. `F2` shipped in Faz 3 (PR #1) and is migrated here rather than
  amended away — the draft PR's history honestly shows the design maturing. `F1` is
  unaffected: a rule firing as intended is genuine domain evidence.

- **Provider exceptions are normalized at the client boundary, not in the graph.** The
  `anthropic` SDK is lazily imported and `llm/factory.py` states agent code "never names a
  backend", so `graph.py` cannot catch `anthropic.APITimeoutError` without leaking the
  provider into the agent layer and making offline paths depend on the SDK. `AnthropicClient`
  therefore translates its KNOWN failures into a provider-agnostic `LLMError(kind)`. It
  deliberately does **not** wrap every exception: a bug in our own client code must surface
  as `unexpected_exception` at the node boundary rather than be mislabelled `provider_error`.

- **Two defects fixed while implementing.** (1) `F2`'s detail interpolated the raw exception
  into a user-facing finding (`f"...failed to evaluate: {exc}"`), which can leak provider
  messages, endpoints, or response bodies into an auditable record; all system-finding
  details are now fixed, bounded templates and the exception survives only in DEBUG logs.
  (2) Because `SynthesisProposal` is handed to the model as a tool schema, adding `status`
  meant a *responding* model could declare itself `unavailable` and manufacture a "no
  proposal was available" audit record — dodging the override record. Unavailability is a
  fact the harness observes, never a model's assertion, so `synthesize` rejects it as
  out-of-contract output and handles it fail-closed.

- **No fabricated proposals.** When synthesis fails there is no proposal, and the audit
  record says so (`status="unavailable"`) rather than inventing
  `proposed_decision="escalate"`, which would read as "the LLM proposed escalate".
  `GuardrailDecision.finalization_mode` distinguishes `proposal_guarded` from
  `fail_closed_no_proposal`, so `overridden=False` can never be misread as "the LLM agreed".

- **T-codes are excluded from domain metrics**, counted via `system_failure_count` /
  `system_error_codes`. This does not weaken safety: T-findings are `unexplainable` and
  findings only ever add, so `false_approval_rate` stays 0 **structurally**. Rendering
  reliability rates is deferred to the reporting milestone.

## P2 — the frozen in-coverage holdout corpus

Full contract in `docs/p2_design.md`; the decisions now in effect:

- **In-coverage, not out-of-coverage.** The 18 curated cases all fall inside the
  deterministic checks' existing coverage, so P2 measures generalization to novel CASES, not
  novel PHENOMENA. Out-of-coverage generalization is P4's job, where a nonzero deterministic
  FAR is the intended finding rather than a violated invariant.

- **Not a blinded external benchmark, and never described as one.** The construction process
  was non-blinded: `checks.py` was inspected before the LLM-assisted cases were authored and
  human-reviewed, which staying in-coverage requires. "Held out" means held out
  from the development and tuning loop — excluded from the promotion gate, generator, anomaly
  builder and normal runs, and frozen before reporting. It does NOT mean a different
  distribution, independent labelling, or blind authoring. P2 measures none of: unknown
  failure-mode discovery, independent-labeler agreement, real-world generalization.

- **Curated, never a generator draw.** A different generator seed would test
  reproducibility, not generalization. The 2 compound cases (`A1+B2a`, `A2+B1a`) are shapes
  the single-injector generator structurally cannot produce, so the guardrail's max-severity
  composition is exercised on unseen data. Independence from `generate/pools.py` is an
  authoring convention, not test-enforced: no value is copied, though a few surface tokens
  recur.

- **`hold_15` (E2) blanks `legal_name` consistently across all three documents.** The
  alternative — blanking `registry.legal_name` alone — leaves differing name surfaces, which
  reaches `rapidfuzz.token_set_ratio` on an empty string. A frozen fixture's ground truth
  must not depend on a third-party matcher's empty-string edge behavior. The cost, stated
  rather than hidden: this is the same mechanism the generator uses for E2, so `hold_15`'s
  independence is in surfaces and structure, not in a novel mechanism.

- **Hashing is over contract-normalized content, not raw bytes.** `core.autocrlf=true` on the
  Windows side makes byte-level pinning fragile; the normalized form is invariant to
  whitespace/key-order/line-endings while still sensitive to every meaning-bearing change.
  Reuses P5's `provenance.hash_corpus` / `sha256_of` verbatim.

- **Freeze tool: all-or-nothing, never overwrites, initial freeze only.** A truth mismatch on
  any of decision/finding/trajectory aborts nonzero and writes NEITHER pin — a per-run
  property, explicitly **not rollback** (a failed run does not erase a pin left by an earlier
  interruption). An existing manifest refuses the freeze at any version; there is no
  `--force`. Corrections and a holdout v2 need a separately designed versioned-corpus
  migration that PRESERVES prior versions; a half-built correction path was prototyped and
  removed because it recreated the silent-replacement hole.

- **Completion-marker recovery, not pair atomicity.** Writes are atomic per file
  (`os.replace`); the two-file sequence is not transactional and is not claimed to be.
  Instead the seed pin is written first and the manifest LAST as the completion marker the
  non-overwrite rule keys on, so an interruption before it leaves no marker and a rerun
  REGENERATES the seed pin from the fixtures rather than trusting one on disk. Invariant: if
  the manifest exists, both pins were written.

- **Cardinal rule: a correction is never used to make a failing case pass.** A hash or truth
  mismatch is a result to investigate, not a chore to silence. Editing a case to match
  current behavior would convert the holdout into a mirror of the code it exists to test.

- **Isolation is enforced, not documented.** Statically, no module under `agent/`,
  `generate/`, `rules/`, `api/`, `llm/`, `normalize/` or `store/` contains any textual route
  to the holdout — the enforced property is absence of a route in source, NOT physical
  runtime impossibility, which a text scan cannot demonstrate. The sole-consumer allowlist
  holds EXACT module paths, never a directory prefix: a prefix such as `eval/` would silently
  authorize `eval/run.py`, the development eval runner. Behaviorally, the gate corpus,
  generator and anomaly corpus are all verified disjoint.

- **Seed-fixture immutability is now mechanical.** "The 8 seed fixtures are never edited" had
  been documented since slice-1 but unenforced; `seed/hash_pin.json` pins the corpus hash and
  all 8 per-case hashes. P2 motivated closing this gap.

- **Bootstrap provenance: `dirty: true` is correct.** The manifest records base commit
  `5cbbc5f` as dirty because corpus content and pins were frozen before their atomic commit.
  Reproducibility is anchored by `corpus_hash` over actual content, not by the SHA. Splitting
  the commit for a cleaner SHA was rejected: it would trade the atomic landing of
  corpus-plus-pin for a detail the hash already covers.

- **Reporting is a wholly separate artifact.** `run_type: "deterministic_holdout"`, written
  to `artifacts/p2_holdout/`, reusing `build_scientific_report` verbatim — corpus selection,
  provenance, labelling and an integrity guard only, no new statistics. The generator corpus
  is labelled `in_sample` and the holdout `out_of_sample`; the two are never averaged, since
  a merged headline would be neither and 420 in-sample cases would drown 18 held-out ones.
  The report re-verifies the corpus against its frozen manifest at publication time and
  raises, writing neither JSON nor Markdown (nor creating the output directory), on drift.

- **Rule exposure is published beside the F1 paired diff.** The promoted `capital_age_ceiling`
  rule cannot fire on the holdout (every company is years old), so its paired delta is zero.
  A bare zero would invite reading it as "the rule was validated out-of-sample", which this
  corpus cannot support. The artifact carries `rule_applicability_count: 0` plus an
  `exposure_note` stating the rule was EVALUATED but NOT TRIGGERED, that a zero delta means
  **not exercised** rather than no effect under exposure, and that it is NOT evidence of
  out-of-sample rule safety or benefit. Rendered immediately after the delta so the caveat
  cannot be separated from the number.

- **The P5 Markdown renderer no longer assumes a generated corpus.** It hard-coded
  `corpus['seed']`, which a curated corpus does not have. Fabricating a seed to satisfy
  the renderer would have put a fictional reproducibility parameter into an audit artifact,
  so the renderer now renders the fields actually present (and appends `sample_relationship`
  when set). Caught by the holdout tests, which correctly forbid a seed on this corpus.

- **Empirical rate vs. structural invariant, kept visible everywhere P2 reports.** The
  observed holdout `false_approval_rate` is `0/14`, **Wilson 95% CI [0.0%, 21.5%]** — an
  EMPIRICAL, corpus-scoped result that depends on the checks actually catching each authored
  case, not a structural guarantee; 14 observations do not exclude a materially higher rate.
  House style from P5 onward (raw k/n plus a Wilson interval wherever a rate is published)
  applies inside the frozen manifest itself, not only in downstream reports.
  SYSTEM-implies-no-approve remains the sole STRUCTURAL invariant, test-cited and never
  given a confidence interval.

- **Logged, deliberately not fixed:** `check_completeness` checks `legal_name` emptiness on
  the registry only, while `tax_id` is checked on all three documents, so a blank
  `circular.legal_name` / `ubo.legal_name` produces no E2. A genuine coverage asymmetry and a
  P4/red-team backlog candidate — P2 must never drive a change to the checks it measures.

## P4 — the frozen out-of-coverage red-team corpus

Full contract in `docs/p4_design.md`; the decisions now in effect:

- **Out-of-coverage, synthetic, author-constructed, non-blinded.** 30 curated cases (6
  categories × 5) whose concerns fall outside every deterministic check's declared scope under
  the pinned configuration. Explicitly not an adversarial assessment, external benchmark,
  blinded study, or real-world robustness claim; it measures behavior on a fixed synthetic
  challenge set and does not estimate general real-world residual risk.

- **`30/30` admission is by construction, never an empirical FAR.** Admission *requires* final
  `approve` with zero findings of any origin under the pinned offline configuration, so the
  offline surface is tautological; it carries no rate and no confidence interval, the same
  principle by which SYSTEM-implies-no-approve carries none. Not comparable to P2's empirical
  `0/14`. Empirical measurement begins only at the separate, key-gated live P4(c) surface.

- **Labels are human-reviewed threat-model judgments**, not externally-validated ground truth;
  the term `human_threat_model` is never used. The 23/7/0 `request_more_info`/`escalate`
  action distribution is an observed authoring outcome, never a quota; `approve` is excluded
  from the label domain by schema.

- **P4 uses its own manifest schema** (`redteam_out_of_coverage_manifest`), reusing only P5's
  contract-normalization and hashing primitives — conflating it with P2's holdout schema would
  merge two genuinely different contracts (different label contract, admission criterion, and
  dependency taxonomy).

- **A later catch under a changed configuration is a versioned re-triage result**, never a
  retroactive edit to a fixture, label, canonical hash, or the frozen manifest. Two dependency
  families are recorded for exactly this reason: `pinned_rule_parameter` (cases sitting outside
  `capital-age-v1`'s parameter box) and `pinned_library_behavior` (cases depending on the
  pinned rapidfuzz empty-string edge behavior).

- **P4(a) landed via PR #6** (`742fcb2`, two-parent merge): corpus, labels, loader, freeze
  tool, frozen manifest (`corpus_hash sha256:1291bb82b47a049ca53c1b04da837885099f13e56b3ceccae11a9284536b28ee`),
  and the integrity/isolation/immutability test suites, including a strict pinned-configuration
  identity check that will fail on a future legitimate rule promotion — intentional, since the
  frozen `30/30` describes only the pinned configuration it was frozen under.

- **P4(b) publishes an admission-and-provenance RECORD, never a "report," "result," or
  "score."** `src/harness/eval/redteam_record.py` does not call `build_scientific_report`,
  does not construct a `RunRecord`, and never runs the agent — not even as an unpublished
  guard: the P4 label is a threat-model judgment, not a `DecisionTruth` prediction, so diffing
  it against a fresh run would manufacture an empirical false-approval rate out of a
  by-construction property. Current-config blindness stays exclusively in
  `tests/test_redteam_fixtures.py`; the record only republishes the manifest's historical
  `observed_at_freeze` block, unchanged and explicitly labelled
  `observation_kind: "historical_at_freeze"`.

- **Configuration drift hard-fails; there is no `stale_config` artifact.** The publication
  guard independently re-validates the complete frozen-manifest contract (schema identity, the
  fixed case-id set, the canonical corpus hash, all 30 canonical per-case hashes, every live
  label field the record copies, the structural completeness and internal cross-linking of the
  historical admission observation, and the CURRENT offline-stub / promoted-rule identity
  against the frozen pin) and writes nothing — not even an output directory — on any mismatch.
  The remedy is a separately designed versioned re-triage, never a re-freeze or a manifest edit.

- **No `sample_relationship` or `out_of_sample` language.** Unlike P2's holdout, P4 draws no
  sample and supports no generalization claim, so the record describes the corpus as
  `corpus_relationship: "fixed_synthetic_challenge_set"` and `authoring_context:
  "author_constructed_non_blinded"` instead.

- **Publication is lifecycle-controlled and never overwrites.** Every record lands under a
  generated, never-reused `run_id` directory (`artifacts/p4_redteam/<run_id>/`), claimed with
  an exclusive `os.mkdir` (no check-then-act gap); a valid record is a directory containing
  EXACTLY the two expected regular files, with Markdown written last as the completion marker;
  a pre-existing directory at any state — valid, empty, or partial — is never overwritten or
  reused, only ever refused as a collision. If a post-claim write fails and best-effort cleanup
  of the claimed directory also fails, a distinct, chained `RecordPublicationCleanupError`
  names the incomplete directory and requires manual investigation, rather than silently
  discarding or reusing it.

- **Two Git SHAs are kept in separate sections with separate notes.** `run.git.commit_sha` is
  publication provenance — the code state that generated the record. `corpus.frozen_git.
  head_commit_sha` is the dirty bootstrap context the freeze ran against. Neither is the
  corpus's reproducibility anchor; `corpus.corpus_hash` alone is, and the record says so
  explicitly beside each SHA.

- **P4(b) landed via PR #7** (`278fab5`, two-parent merge), commit `479688d`.

- **P4(c) measures the TRIAGE LAYER, not "the AI."** `src/harness/eval/redteam_live.py`
  (`run_type: redteam_live_triage`, output root `artifacts/p4c_live/`). The synthesis proposal
  cannot change an outcome — the guardrail decides — so the only semantic signal is a finding
  with `origin="ai_triage"`. The primary outcome is the **clean-run live-triage intervention
  rate**, never a false-approval rate, detection rate, or accuracy. This is P4's first and only
  empirical number; it is not comparable to P2's `0/14` or to P4(b)'s by-construction `30/30`.

- **A contaminated attempt is invalid as a whole; there is no variable denominator.** One
  terminal SYSTEM/T0 failure invalidates the entire attempt, which then carries no rate, no
  interval, and no numerator/denominator at all. Excluding contaminated cases to score a clean
  subset would make denominator membership depend on operational behavior and invite
  selective-exclusion bias. A SYSTEM-origin non-approval is never an intervention and never
  evidence against a false approval — both LLM nodes emit a T0 that escalates, so an outage
  would otherwise manufacture a flawless-looking result out of pure failure.

- **The "advisory triage note" category does not exist and is asserted away.** Triage severity
  is clamped to `explainable`/`unexplainable` and the guardrail takes the maximum, so for an
  admitted case any domain concern is necessarily outcome-changing. It is proven as a structural
  invariant against the real graph instead of being reported as a metric.

- **A coverage violation is severity-independent and terminal.** Any finding whose origin is
  neither `ai_triage` nor `system` — including an `info`-severity one that changes no decision —
  disproves the frozen zero-findings admission for the executed configuration. It ends the
  session, takes precedence over a co-occurring T0, and records the full finding set with a
  reason code. Distinct from `input_integrity_failure`, which means a frozen case could not be
  loaded or executed after preflight had already validated the corpus.

- **Three valid attempts, two replacements, five attempts maximum**, and the session stops at
  three regardless of observed variability. Each valid attempt publishes its own raw `k/n` and
  Wilson 95% interval; across attempts only a descriptive distribution (ordered results, min,
  max, median). Numerators and denominators are never pooled and no interval is computed across
  attempts, because the same cases repeat and outcomes are correlated. Temperature is pinned but
  the provider exposes no seed, so repeated attempts estimate provider-side variability, not
  seeded replication. **Zero interventions is a valid published result**, never an
  implementation failure and never a reason to adjust prompts, rules, labels, or fixtures.

- **Cost is bounded by reservation, not detection.** A case never starts unless its full worst
  case (two call sites × three attempts = six outbound requests) still fits under the 600-call
  session ceiling, so the ceiling cannot be exceeded. The 180-call per-clean-attempt figure is a
  theoretical maximum, kept explicitly distinct from the ceiling, which is a cost-stop policy.
  The 60 s timeout bounds one SDK request; a logical graph call is bounded at 187 s including
  the 1 s jitter cap.

- **Live calls are operator-initiated only.** No test in this project calls a provider and none
  is key-gated. The sole live path is `harness-p4c-live --confirm-live`; without the flag the
  command performs a free dry run — preflight and banner, zero calls, no output directory.
  Sessions are written to the gitignored `artifacts/` tree and are never committed. Verbatim
  model text is retained for auditability and always carries an untrusted-model-output marker
  that is never separated from the quote.

- **Sanitized derivatives of explicitly pinned live sessions may be committed under this
  contract; raw live-session artifacts remain gitignored and are never committed.**
  `src/harness/eval/redteam_public_summary.py` (`run_type: redteam_live_public_summary`,
  tracked root `docs/evidence/p4c/`) publishes a hash-pinned, allowlisted derivative of one
  pinned session so that a reader can check the counts a write-up cites without being handed
  the model's verbatim text. It is a narrowly scoped exception to "sessions are never
  committed", not a repeal of it: raw artifacts stay gitignored, and each derivative
  carries no `triage_concerns`, `detail`, `fields_involved`, `proposal_note`, or finding
  objects — only counts, provenance, and per-case classification, decision, severity codes,
  provider calls, proposal decision, and override flag.

- **The derivative is a re-expression, never a re-analysis.** It performs no provider call,
  runs no agent, and introduces no metric: decision counts and the repeat structure are
  tabulations of per-case fields the source already records, and the Wilson intervals are
  republished exactly as the protocol recorded them. A session is publishable only if its
  `run_id` is listed in `PINNED_SOURCE_DIGESTS` **and** both artifact files match those
  digests byte for byte; every source block is projected through an exact key allowlist, so
  a changed source schema fails loudly rather than widening the public one. Thirteen
  invariants are asserted before anything is written, including that each attempt is valid,
  that `k` equals the intervention count, that every attempt shares one case-id set, that
  per-case provider calls total `cost.actual_calls`, and that the repeat-structure groups
  are disjoint and total 30 with the severity-variable group a subset of the
  always-intervened group.

- **Publication is exclusive and never overwritten.** One directory per source `run_id`,
  claimed with an exclusive `os.mkdir`; a pre-existing directory is refused
  (`PublicSummaryCollisionError`), an integrity failure creates nothing, and a post-claim
  write failure removes the claimed directory. Verification rebuilds into a scratch `--out`
  and compares digests rather than regenerating in place.

- **`AnthropicClient` gained two optional keyword-only transport options** (`timeout`,
  `max_retries`), forwarded to the SDK only when explicitly supplied. Omitting both reproduces
  the previous construction exactly, so every existing caller is unaffected; this is asserted by
  dict equality in `tests/test_anthropic_client.py`, which previously did not exist. P4(c) is
  the first caller to pin them, using `max_retries=0` so that its own bounded wrapper is the
  only retry layer and the call ceiling means what it says.

## Provenance correction — "hand-authored" (2026-08-26)

- **The corpora were misdescribed; the correction is recorded prospectively rather than by
  rewriting frozen artifacts.** All three
  fixture corpora were described as "hand-authored". That is accurate as *derivation*
  (curated case by case, never a generator draw) and inaccurate as *authorship provenance*
  (the case texts were LLM-assisted and human-reviewed). The two senses are now stated
  separately everywhere they appear. Full entry in `ERRATA.md`.

- **Frozen records were not rewritten.** `holdout/manifest.json`, `redteam/manifest.json`,
  `seed/hash_pin.json`, all 56 fixture JSONs, and the published P4(c) evidence keep their
  original bytes. The manifest-text constants inside `freeze_holdout.py` and
  `freeze_redteam.py` also keep the original wording, because they must stay byte-identical
  to the artifacts they produced; each now carries an adjacent comment pointing at the
  errata. A published admission record therefore still reproduces "Hand-authored" verbatim
  from the frozen `scope_note` — that is the historical record, and the errata is the
  correction. Editing a frozen record to look better is exactly what this project's
  versioned-re-triage rule exists to forbid.

- **Two label values changed, and the bump is MAJOR, not additive.** `corpus.authoring`
  moved from `hand_authored` to `llm_assisted_human_reviewed` in both the P4(b) admission
  record (`redteam_admission_record` 1.0.0 → **2.0.0**) and the P2 holdout report (shared
  report schema 1.0.0 → **2.0.0**), and both records gained a separate `corpus.derivation:
  "curated_not_generator_drawn"` so derivation and authorship are no longer carried by one
  conflated value. Adding a field is additive; **removing a value from a field's domain is
  not** — it breaks any consumer branching on the literal, which is why this is a major bump
  even though nothing in this repository parses either value. The development report shares
  the envelope version and carries the bump with no content change of its own.
  `authoring_context: "author_constructed_non_blinded"` is deliberately unchanged: it
  describes where the corpus came from and under what blindness, not who typed the cases.

- **The synthetic corpora are licensed separately from the code.** MIT covers the source;
  an MIT grant over program source does not automatically extend to a dataset shipped with
  it. The three corpora and their dataset metadata are CC BY 4.0 (`DATA-LICENSE.md`), which
  permits commercial use with attribution.
