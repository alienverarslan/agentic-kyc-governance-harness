"""Curated Turkish data pools for synthetic dossiers.

Small, hand-picked vocabularies so generated dossiers look plausibly Turkish (correct
diacritics, real legal-form suffixes) while varying enough to exercise normalization and
matching. Kept separate from the generation logic so the vocabulary can grow without
touching recipes.
"""

from __future__ import annotations

# Given names carrying Turkish-specific letters (İ, ı, ş, ğ, ü, ö, ç) so normalization is
# genuinely exercised.
FIRST_NAMES: list[str] = [
    "Ahmet", "Mehmet", "Mustafa", "Ali", "Hüseyin", "Hasan", "İbrahim", "Osman",
    "Emine", "Fatma", "Ayşe", "Hatice", "Zeynep", "Elif", "Meryem", "Şeyma",
    "İlker", "Sıla", "Cemil", "Nurten", "Oğuz", "Deniz", "Pelin", "Serkan",
    "Hülya", "Burak", "Çağrı", "Gökhan", "Şükrü", "Ömer", "Ümit", "Işıl",
]

SURNAMES: list[str] = [
    "Kardelen", "Yıldız", "Işık", "Kaya", "Öztürk", "Demir", "Arslan", "Karaca",
    "Koç", "Yılmaz", "Şahin", "Aydın", "Çelik", "Doğan", "Güneş", "Şimşek",
    "Aslan", "Erdem", "Çetin", "Güler", "Özdemir", "Taş", "Ünal", "Böcek",
]

# Company name cores + sectors (combine into "<Core> <Sector>").
COMPANY_CORES: list[str] = [
    "Kardelen", "Yıldız", "Işık", "Meltem", "Aslan", "Pelin", "Karaca", "Derya",
    "Anadolu", "Ege", "Marmara", "Toros", "Deniz", "Zirve", "Öz", "Güven",
]

SECTORS: list[str] = [
    "Gıda", "Makine", "İnşaat", "Tekstil", "Otomotiv", "Bilişim", "Enerji",
    "Lojistik", "Kimya", "Mobilya", "Turizm", "Tarım",
]

CITIES: list[tuple[str, str]] = [
    ("İstanbul", "Kadıköy"), ("Ankara", "Çankaya"), ("İzmir", "Konak"),
    ("Bursa", "Nilüfer"), ("Antalya", "Muratpaşa"), ("Gaziantep", "Şehitkamil"),
    ("Denizli", "Merkezefendi"), ("Konya", "Selçuklu"), ("Kayseri", "Melikgazi"),
]

STREETS: list[str] = [
    "Cumhuriyet Mah. Atatürk Cad.", "Yenişehir Mah. İnönü Bulvarı",
    "Merkez Mah. Fevzi Çakmak Cad.", "Organize Sanayi Bölgesi 12. Cad.",
    "Barbaros Mah. Halk Cad.", "Liman Mah. Gümrük Cad.",
]

OWNERSHIP_PATH_TR = "doğrudan pay sahipliği"

# Legal-form suffix variants. Each tuple is (canonical full form, ALL-CAPS abbreviated,
# ASCII-degraded) so A1 recipes can spell the SAME entity three plausible ways.
LEGAL_SUFFIXES: list[tuple[str, str, str]] = [
    ("Sanayi ve Ticaret Ltd. Şti.", "SAN. VE TİC. LTD. ŞTİ.", "San ve Tic Ltd Sti"),
    ("Limited Şirketi", "LTD. ŞTİ.", "Ltd Sti"),
    ("Sanayi ve Ticaret A.Ş.", "SAN. VE TİC. A.Ş.", "San ve Tic AS"),
    ("Anonim Şirketi", "A.Ş.", "AS"),
]
