"""Normalization + matching configuration.

Kept separate from the algorithm so thresholds and the legal-suffix vocabulary can be
audited and tuned without touching the matching logic. Fixtures are read-only ground
truth: if a seed case fails, fix the check logic, not these thresholds (see README /
DoD).
"""

from __future__ import annotations

# --- UBO ---------------------------------------------------------------------------
# A beneficial owner must be declared only at/above this ownership percentage. Seed #2
# deliberately omits a 20% shareholder from declared_ubo: that is NOT a gap. The
# check_ubo_derivation STUB records this so the slice-2 implementation inherits it.
UBO_THRESHOLD_PCT: float = 25.0

# --- Fuzzy matching (rapidfuzz operates on folded, suffix-canonicalized strings) ----
# Person names: after Turkish-aware folding, spellings of the same person should be
# near-identical, so the bar is high to avoid conflating distinct people.
PERSON_MATCH_THRESHOLD: float = 90.0
# Legal names: one variant may drop entire suffix tokens (ASCII-degraded seed #3), so
# we use token_set_ratio which is subset-tolerant, with a slightly lower bar.
LEGAL_MATCH_THRESHOLD: float = 85.0

# --- Authority staleness -----------------------------------------------------------
# A signature circular older than the UBO declaration by more than this many days is
# "materially older": a newer authorised person plausibly exists → explainable (C1a).
# Contemporaneous-or-newer circulars close that temporal explanation → C1b.
AUTHORITY_STALENESS_DAYS: int = 365

# --- Legal-suffix canonicalization -------------------------------------------------
# Each surface variant (already Turkish-lowercased at match time) maps to a single
# canonical token. This collapses "Ltd. Şti." / "Limited Şirketi" / "Ltd Sti" etc. to
# one form BEFORE diacritic folding + fuzzy comparison. Order matters: longer / more
# specific phrases are applied first (see turkish.canonicalize_suffixes).
LEGAL_SUFFIX_CANONICAL: dict[str, str] = {
    # company-type suffixes
    "limited şirketi": "ltd_sti",
    "ltd. şti.": "ltd_sti",
    "ltd şti": "ltd_sti",
    "ltd. sti.": "ltd_sti",
    "ltd sti": "ltd_sti",
    "anonim şirketi": "as",
    "a.ş.": "as",
    "a.s.": "as",
    "a ş": "as",
    # activity suffixes
    "sanayi ve ticaret": "san_tic",
    "san. ve tic.": "san_tic",
    "san ve tic": "san_tic",
}
