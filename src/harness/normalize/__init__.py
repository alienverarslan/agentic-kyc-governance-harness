"""Turkish-aware text normalization + cross-document entity resolution."""

from harness.normalize.turkish import (
    diacritic_fold,
    matching_key,
    canonicalize_suffixes,
    turkish_lower,
)

__all__ = [
    "turkish_lower",
    "diacritic_fold",
    "canonicalize_suffixes",
    "matching_key",
]
