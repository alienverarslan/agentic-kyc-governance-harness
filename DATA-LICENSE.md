# Data license — the synthetic corpora

The **source code** in this repository is licensed under the MIT License (see `LICENSE`).
An MIT grant over program source does not automatically extend to a dataset shipped
alongside it, so the synthetic corpora are licensed separately and explicitly here.

## What this license covers

The following synthetic fixture data, and the dataset metadata and manifests that describe
it, are licensed under the **Creative Commons Attribution 4.0 International licence
(CC BY 4.0)**:

| Corpus | Files | Cases |
|---|---|---|
| Seed diagnostic fixtures | `src/harness/data/seed/case_01..08.json`, `src/harness/data/seed/MANIFEST.md`, `src/harness/data/seed/hash_pin.json` | 8 |
| In-coverage holdout corpus | `src/harness/data/holdout/case_hold_01..18.json`, `src/harness/data/holdout/manifest.json` | 18 |
| Out-of-coverage red-team corpus | `src/harness/data/redteam/case_rt_01..30.json`, `src/harness/data/redteam/manifest.json` | 30 |

Full licence text: <https://creativecommons.org/licenses/by/4.0/>

CC BY 4.0 permits sharing and adaptation, **including for commercial purposes**, provided
you give appropriate credit, link to the licence, and indicate whether changes were made.

**Attribution string:** *Synthetic KYC dossier corpora by Ali Enver Arslan
(alienverarslan), from the agentic-kyc-governance-harness project, licensed under
CC BY 4.0.*

## Provenance and creator

The corpora were created for this project. They are **curated, not generator-drawn**: no
case is a draw from the repository's synthetic generator. The case texts are **LLM-assisted
and human-reviewed** — drafted with LLM assistance during an AI-assisted development
process, then reviewed and approved by the author. See `ERRATA.md` for the correction of
earlier wording that described this as "hand-authored".

The labels attached to the cases are project labels reviewed and approved by the author —
decision truth for the seed and holdout corpora, threat-model judgments for the red-team
corpus. They are **not externally validated ground truth**, and no second reviewer has
independently labelled the corpora.

## What the data is, and is not

Every dossier is **entirely synthetic**. The dossiers were created synthetically and were
not sourced from customer, employer, client, registry, or other real-person records.
Company names, tax identifiers, addresses, notary references, personal names, ownership
structures, and dates are fictional test values; any resemblance is coincidental.

The corpora are evaluation fixtures for a research harness. They are not a benchmark, not a
representative sample of any real population, and carry no claim about real-world
prevalence of the concerns they encode.

## Scope of the grant

This licence grants only the rights the author holds in the synthetic data itself. It makes
no representation about third-party rights, and no warranty of fitness for any purpose is
given or implied.

## Generated data

Cases produced at runtime by the synthetic generator (`src/harness/generate/`) are derived
from the seed fixtures and from the generator's own pools, and fall under the same CC BY
4.0 grant.

---

Copyright (c) 2026 Ali Enver Arslan. Code: MIT (`LICENSE`). Data: CC BY 4.0 (this file).
