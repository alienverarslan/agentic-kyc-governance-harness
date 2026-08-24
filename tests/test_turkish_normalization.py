"""Turkish normalization suite, including the diacritic-folding layer.

These tests encode the seed #3 requirement: the false-positive trap where naive casing
would fabricate an identity contradiction.
"""

from harness.normalize.turkish import (
    canonicalize_suffixes,
    diacritic_fold,
    legal_names_match,
    matching_key,
    person_names_match,
    turkish_lower,
)


def test_dotted_and_dotless_i_roundtrips():
    # Turkish rule: İ -> i (dotted), I -> ı (dotless). Both spellings of the name must
    # lowercase to the SAME string.
    assert turkish_lower("İLKER IŞIK") == "ilker ışık"
    assert turkish_lower("İlker Işık") == "ilker ışık"
    assert turkish_lower("IŞIK") == "ışık"
    assert turkish_lower("İNŞAAT") == "inşaat"


def test_naive_lower_is_the_bug_we_avoid():
    # Demonstrate WHY turkish_lower exists: Python's str.lower gets the dotted/dotless
    # i backwards, so the two spellings diverge. Our folded keys still unify them.
    assert "İLKER IŞIK".lower() != "İlker Işık".lower()
    assert matching_key("İLKER IŞIK") == matching_key("İlker Işık")


def test_folding_table():
    assert diacritic_fold(turkish_lower("IŞIK")) == "isik"
    assert diacritic_fold("çğüöşı") == "cguosi"
    assert diacritic_fold(turkish_lower("İnşaat")) == "insaat"


def test_suffix_canonicalization():
    # All company-type surface variants collapse to the same canonical token.
    keys = {
        matching_key("Işık İnşaat Sanayi ve Ticaret Limited Şirketi"),
        matching_key("IŞIK İNŞAAT SAN. VE TİC. LTD. ŞTİ."),
    }
    assert len(keys) == 1  # identical canonical key
    assert "ltd_sti" in canonicalize_suffixes(turkish_lower("Foo Ltd. Şti."))
    assert "as" in canonicalize_suffixes(turkish_lower("Bar A.Ş."))


def test_case3_triple_resolves_to_one_entity():
    # The three legal-name spellings from seed #3.
    official = "Işık İnşaat Taahhüt Sanayi ve Ticaret Limited Şirketi"
    allcaps = "IŞIK İNŞAAT TAAHHÜT SAN. VE TİC. LTD. ŞTİ."
    ascii_degraded = "Isik Insaat Taahhut Ltd Sti"
    assert legal_names_match(official, allcaps)
    assert legal_names_match(official, ascii_degraded)
    assert legal_names_match(allcaps, ascii_degraded)

    # And the declarant person name across the same spellings.
    assert person_names_match("İlker Işık", "İLKER IŞIK")
    assert person_names_match("İlker Işık", "Ilker Isik")
    assert person_names_match("İLKER IŞIK", "Ilker Isik")


def test_distinct_people_do_not_match():
    assert not person_names_match("İlker Işık", "Sıla Işık")
    assert not person_names_match("Ahmet Kaya", "Murat Öztürk")


def test_suffix_canon_does_not_corrupt_ordinary_names():
    # Regression (found by the synthetic generator): the abbreviated suffix "a ş" must
    # NOT match as a substring inside a name like "Mustafa Şahin". Suffix canonicalization
    # is token-bounded, so an ASCII-degraded name still resolves to its proper spelling.
    for name in ["Mustafa Şahin", "Fatma Şimşek", "Sıla Şahin", "Hülya Şimşek"]:
        assert " as " not in f" {matching_key(name)} ", matching_key(name)
        assert person_names_match(name, diacritic_fold(turkish_lower(name)))
