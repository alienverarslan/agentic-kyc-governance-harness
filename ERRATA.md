# Errata

Dated, additive corrections to statements published in this repository. Nothing in this
file rewrites history: frozen artifacts keep their original bytes, and the entries below
record what was wrong, what the correct statement is, and what was and was not changed.

---

## 2026-08-26 — "hand-authored" misdescribed the authorship of all three fixture corpora

### What was wrong

Across the repository, the three synthetic fixture corpora were described as
**"hand-authored"**:

* the 8 seed diagnostic fixtures (`src/harness/data/seed/case_01..08.json`),
* the 18 in-coverage holdout fixtures (`src/harness/data/holdout/case_hold_01..18.json`),
* the 30 out-of-coverage red-team fixtures (`src/harness/data/redteam/case_rt_01..30.json`).

The phrase was used with two different meanings that were never separated:

1. **Derivation** — "curated case by case, never drawn from the synthetic generator."
   In this sense the statement was **accurate**. None of the three corpora is a generator
   draw; that distinction is load-bearing, because a different generator seed would test
   reproducibility rather than generalization.
2. **Authorship provenance** — "written by a human." In this sense the statement was
   **inaccurate**. The case texts were drafted with LLM assistance during an AI-assisted
   development process, then reviewed and approved by the author.

Because the second reading is the ordinary one, the wording gave a materially misleading
impression of who wrote the fixtures.

### The correct statement

All three corpora are **curated, not generator-drawn**, and their case texts are
**LLM-assisted and human-reviewed**. Threat-model labels are the author's reviewed
judgments about the appropriate action; they are not externally validated ground truth.

The red-team corpus already recorded this correctly at the per-case level and always has:
every one of the 30 fixtures carries `authoring_assistance: "llm_assisted"`,
`label_review: "human_approved"`, and `label_basis: "threat_model_judgment"` in its
machine-readable label. The corpus-level prose contradicted its own per-case data.

### What changed

Mutable prose, docstrings, design notes, and the two report-generator label fields were
corrected to separate the two meanings above. See the accompanying diff.

Two machine-readable label values changed, with schema versions bumped rather than
changed silently:

| Artifact | Field | Was | Now | Schema version |
|---|---|---|---|---|
| P4(b) admission record | `corpus.authoring` | `hand_authored` | `llm_assisted_human_reviewed` | `redteam_admission_record` 1.0.0 → **2.0.0** |
| P2 holdout report | `corpus.authoring` | `hand_authored` | `llm_assisted_human_reviewed` | shared report schema 1.0.0 → **2.0.0** |

Both artifacts additionally gained a separate `corpus.derivation:
"curated_not_generator_drawn"` field, so that derivation and authorship provenance are no
longer carried by one conflated value. In the red-team record these now sit beside the
existing `corpus.authoring_context: "author_constructed_non_blinded"`, which describes a
third and distinct fact: the corpus was built inside this project with knowledge of the
coverage, rather than sourced externally or blindly.

**These are major bumps, not additive changes.** Adding `derivation` is additive, but
removing `hand_authored` from the value domain of `corpus.authoring` breaks any consumer
that branches on the literal. No field was removed or renamed, and nothing in this
repository parses either value, but the value-domain change alone is enough to make it
breaking. The development report shares the report envelope version and therefore carries
the bump without any content change of its own.

### What deliberately did **not** change

The following retain the original wording, because this project's cardinal rule is that a
frozen record is never retroactively edited — a later correction is an additive,
versioned observation:

* `src/harness/data/holdout/manifest.json` — `scope_note`, `authoring_note`
* `src/harness/data/redteam/manifest.json` — `scope_note`
* the manifest-text constants inside `src/harness/data/freeze_holdout.py` and
  `src/harness/data/freeze_redteam.py`, which must stay byte-identical to the frozen
  artifacts they produced
* all 56 fixture JSON files, `src/harness/data/seed/hash_pin.json`, and the published
  P4(c) evidence and provenance files under `docs/evidence/`

Consequently, a published P4(b) admission record still reproduces the frozen
`scope_note` verbatim, including the word "Hand-authored". That text is the historical
record of what the manifest said at freeze time. This entry is the correction.

### Why it was not caught earlier

A related provenance error was caught during the P2 review — the draft body of PR #4
described the holdout fixtures as "written by a human", which was identified as false and
corrected before merge — but the same underlying wording already present elsewhere in the
repository was not comprehensively audited at that time.
