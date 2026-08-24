"""Turkish-aware text normalization.

WHY this exists (safety reasoning): a naive ``str.lower()`` / ``str.casefold()`` on
Turkish text is WRONG and produces false positives. Python lowercases ``I`` to ``i``,
but in Turkish ``I`` lowercases to the dotless ``ı`` and the dotted ``İ`` lowercases to
``i``. Getting this backwards makes "İLKER IŞIK" and "İlker Işık" compare as different
people, which would fabricate an identity contradiction (seed #3). The whole point of
seed #3 is to catch exactly that bug, so normalization is mandatory, not optional.

Two layers, deliberately separated:

1. ``turkish_lower`` — a DISPLAY-safe, correctness-preserving lowercasing that keeps
   Turkish letters (ş, ğ, ü, ö, ç, ı, i) intact.
2. ``diacritic_fold`` — a MATCH-ONLY folding (ş→s, ğ→g, ü→u, ö→o, ç→c, ı→i) used to
   build comparison keys so an ASCII-degraded document ("Isik Insaat") still resolves
   to its correctly-spelled counterpart. Never use folded text for display.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from harness.normalize.config import (
    LEGAL_MATCH_THRESHOLD,
    LEGAL_SUFFIX_CANONICAL,
    PERSON_MATCH_THRESHOLD,
)

# Turkish uppercase→lowercase pairs that Python's generic lowercasing gets wrong. We
# apply these BEFORE calling str.lower() so the dotted/dotless-i distinction is correct.
_TR_UPPER_TO_LOWER = {
    "İ": "i",  # dotted capital I  -> dotted i
    "I": "ı",  # dotless capital I -> dotless i
}

# Match-only diacritic folding. Applied AFTER turkish_lower, i.e. to lowercase text.
_FOLD = str.maketrans(
    {
        "ş": "s",
        "ğ": "g",
        "ü": "u",
        "ö": "o",
        "ç": "c",
        "ı": "i",
        "â": "a",
        "î": "i",
        "û": "u",
    }
)


def turkish_lower(text: str) -> str:
    """Lowercase ``text`` with correct Turkish casing rules.

    ``İ`` -> ``i`` and ``I`` -> ``ı`` are applied explicitly first; the remaining
    characters (including Ş, Ğ, Ü, Ö, Ç) are lowercased by Python's Unicode-aware
    ``str.lower`` while keeping their diacritics.
    """
    for upper, lower in _TR_UPPER_TO_LOWER.items():
        text = text.replace(upper, lower)
    return text.lower()


def diacritic_fold(text: str) -> str:
    """Fold Turkish diacritics to ASCII for MATCHING keys only (never for display).

    Expects already-lowercased text (call ``turkish_lower`` first)."""
    return text.translate(_FOLD)


def canonicalize_suffixes(text: str) -> str:
    """Collapse legal-form suffix variants to canonical tokens.

    Operates on Turkish-lowercased (diacritic-preserving) text because the suffix
    vocabulary is defined with Turkish spelling ("ltd. şti.", "a.ş."). Longer phrases
    are replaced first so that "san. ve tic." is consumed before any shorter fragment.

    Matching is TOKEN-BOUNDED (the surface must be preceded by start-or-whitespace and
    followed by whitespace-or-end). Without this, a short abbreviated suffix such as
    "a ş" would match the substring inside an ordinary name like "Mustafa Şahin" and
    corrupt it — a bug the synthetic generator surfaced that the 8 seeds never hit.
    """
    for surface in sorted(LEGAL_SUFFIX_CANONICAL, key=len, reverse=True):
        canonical = LEGAL_SUFFIX_CANONICAL[surface]
        pattern = r"(?:(?<=\s)|^)" + re.escape(surface) + r"(?=\s|$)"
        text = re.sub(pattern, f" {canonical} ", text)
    return text


def matching_key(text: str) -> str:
    """Produce the canonical comparison key for a name.

    Pipeline: turkish_lower -> canonicalize_suffixes -> diacritic_fold -> strip
    punctuation and collapse whitespace. rapidfuzz operates ONLY on these keys.
    """
    text = turkish_lower(text)
    text = canonicalize_suffixes(text)
    text = diacritic_fold(text)
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def person_names_match(a: str, b: str, *, threshold: float = PERSON_MATCH_THRESHOLD) -> bool:
    """True if ``a`` and ``b`` denote the same natural person after normalization.

    Person names should be near-identical once folded, so we use token_sort_ratio
    (order-insensitive) with a high threshold to avoid conflating distinct people.
    """
    ka, kb = matching_key(a), matching_key(b)
    if ka == kb:
        return True
    return fuzz.token_sort_ratio(ka, kb) >= threshold


def legal_names_match(a: str, b: str, *, threshold: float = LEGAL_MATCH_THRESHOLD) -> bool:
    """True if ``a`` and ``b`` denote the same legal entity after normalization.

    An ASCII-degraded variant may drop whole suffix tokens, so we use token_set_ratio
    (subset-tolerant) with a slightly lower threshold.
    """
    ka, kb = matching_key(a), matching_key(b)
    if ka == kb:
        return True
    return fuzz.token_set_ratio(ka, kb) >= threshold
