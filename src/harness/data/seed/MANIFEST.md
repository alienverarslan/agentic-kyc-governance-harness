# Seed Dataset — doc-consistency-governance-harness (slice-1)

8 curated dossiers — case by case, never a generator draw; texts authored with LLM
assistance and reviewed by the author (see `ERRATA.md`). Each isolates exactly ONE
taxonomy code (clean cases: NONE).
Committed as reference fixtures under `data/seed/`; the generator is validated AGAINST
this set, not the other way around.

| # | file | code | decision | isolation notes |
|---|------|------|----------|-----------------|
| 1 | case_01.json | NONE | approve | Ltd. Şti., sole signatory, 2 shareholders |
| 2 | case_02.json | NONE | approve | A.Ş., joint signatories, 20% shareholder deliberately below 25% UBO threshold (NOT a gap) |
| 3 | case_03.json | A1 | approve | 3 name spellings: official / ALL-CAPS Turkish (İ→İ, I stays I: "IŞIK","İLKER") / ASCII-degraded ("Isik","Ilker"). tax_id identical everywhere. Kills naive casefold + raw string compare |
| 4 | case_04.json | B1a | request_more_info | B3 newer (2025-11 vs 2024-02); one exit, pct absorbed, sum stays 100 |
| 5 | case_05.json | B1b | escalate | B3 OLDER (2023-05 vs 2025-09); structure differs; stale-declaration pattern |
| 6 | case_06.json | C1a | request_more_info | declarant absent; circular 2+ years older than B3; circular signatory is a NON-shareholder professional manager (deliberate — not a finding) |
| 7 | case_07.json | C1b | escalate | declarant absent; circular NEWER than B3 — temporal explanation closed |
| 8 | case_08.json | E1 | request_more_info | ubo=null; ownership+authority checks must be SKIPPED (ran=False), trajectory encodes it |

## Verification invariants (enforced by validation script, must hold after any edit)
- tax_id identical across all documents of a dossier (no unintended A2)
- all shareholder lists sum to exactly 100.0 (no unintended B2a/B2b)
- ownership lists differ ONLY in #4, #5 (and #3 via ASCII variants, which is A1)
- declarant absent from circular ONLY in #6, #7
- date directions: B1a → B3>B1; B1b → B3<B1; C1a → B2<B3; C1b → B2>B3
- no signatory valid_until expires before B3.document_date (no unintended C2)

## Critical eval expectations encoded here
- #3 must NOT trigger identity contradiction after normalization (false-positive trap)
- #4 vs #5 differ structurally ONLY in date direction + delta pattern — the ownership
  check's delta logic MUST separate them
- #6 vs #7 differ ONLY in circular date direction — the authority check MUST separate them
- #8: reporting a skipped check as "ran clean" is a trajectory failure
- middle-class fidelity metric draws on #4, #6, #8
