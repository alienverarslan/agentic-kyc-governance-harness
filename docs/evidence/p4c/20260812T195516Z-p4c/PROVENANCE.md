# Evidence provenance for run `20260812T195516Z-p4c`

An index of identifiers that already exist in the published artifacts. It is **not** new
cryptographic proof, and it does not restore a link to any private repository.

This repository is a public release snapshot. The development history, the raw live-session
artifact, and the commit the session executed from are in a separate private repository.

## What this repository lets you establish

| Claim | Identifier | How to check |
|---|---|---|
| The released corpus hashes to the corpus identifier recorded in the published evidence | `sha256:1291bb82b47a049ca53c1b04da837885099f13e56b3ceccae11a9284536b28ee` | recompute the corpus hash over `src/harness/data/redteam/` and compare with `manifest.json` and with `preflight.corpus_hash` in the summary |
| The released promoted-rule set hashes to the recorded identifier | `sha256:95a0a589e52d12ccd4c0a21a6acb64cd534e4539e112c40f14b98334ca3c9f24` | recompute over the promoted rule set |
| The released triage prompt hashes to the published identifier | `sha256:3ad0135c79d0f5556e7ab2be8bbb4e7ea3680c0474713bc6e83ba942fcf28aab` | SHA-256 of the pinned triage system prompt text |
| The released synthesis prompt hashes to the published identifier | `sha256:141b0c80b5954c24f47582c226d3d20afb370f759357cf9161042f6f812207a5` | SHA-256 of the pinned synthesis system prompt text |
| The released coverage-catalog prompt hashes to the published identifier | `sha256:726a5cd176533b6da98f34f2ad05d7232d6a30c5d3d0ff588d00f2963a45140a` | SHA-256 of the rendered coverage catalogue |
| The released summary matches its published byte count and digest | 39365 bytes, `sha256:fab12830b18c69bd1f6c5c43dafc01479c7a0d247c50f0812cfff091526240ad` | `wc -c` and `sha256sum` on the file beside this one; the test suite pins the same digest |
| The generator source and its validation rules can be inspected | `src/harness/eval/redteam_public_summary.py` | read it, including the field allowlists and the invariants asserted before any write |

## What this repository does NOT establish

* **That the private live session used this public source snapshot.** The session ran in a
  private repository; nothing published here ties that execution to this tree.
* **That the published generator actually produced this exact summary.** You can read the
  generator and you can read the summary. Connecting them requires the input.
* **Equivalence between the private execution tree and this public snapshot**, except where
  a published content hash covers it. The corpus hash covers the corpus. The prompt hashes
  cover the prompts. The summary digest covers the summary. Nothing covers the rest.
* **Replay of the derivation.** The generator is deterministic, but it refuses to run on
  anything except a raw artifact matching its pinned digests, and that artifact is not
  distributed here.

## Recorded but not resolvable here

* `derivation.source_commit_sha` is `a70c12ccb8a4e9c7ff471159e72d8298d6939430`, a commit in the private
  development repository. It does not resolve in this repository.
* The raw artifact digests are
  `sha256:2776167bda0952d4ef500c28d1a8bf271e1230b952a7361f5144f6a0b95bcc60` and
  `sha256:449c44ef01c6f165a155626f002615a97aeb46b7c6202338c70a800e5ae829b0`. The artifact itself is not
  distributed: it holds the model's verbatim text for every concern raised across ninety
  case-runs. A digest lets someone verify a copy they already have; it does not let anyone
  reconstruct one they do not.

## Conclusion

You can confirm that the released corpus and prompts hash to the identifiers recorded in
the published evidence, and that the released summary matches its published byte count and
digest. Because the raw session artifact and execution commit are private, this repository
alone cannot prove that the live session used this public source snapshot or replay the
derivation of the exact summary.
