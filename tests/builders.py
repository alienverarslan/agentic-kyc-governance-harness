"""Small factory helpers for constructing documents in unit tests."""

from __future__ import annotations

from datetime import date

from harness.contracts.documents import (
    RegistryDoc,
    Shareholder,
    SignatureCircular,
    Signatory,
    UboDeclaration,
    UboEntry,
)


def registry(shareholders: list[tuple[str, float]], doc_date: str, tax_id: str = "1111111111") -> RegistryDoc:
    return RegistryDoc(
        document_date=date.fromisoformat(doc_date),
        legal_name="Test A.Ş.",
        tax_id=tax_id,
        incorporation_date=date(2010, 1, 1),
        registered_address="Test Mah., İstanbul",
        share_capital=100000,
        shareholders=[Shareholder(name=n, ownership_pct=p) for n, p in shareholders],
        company_status="active",
    )


def ubo(
    shareholders: list[tuple[str, float]],
    doc_date: str,
    declarant: str,
    tax_id: str = "1111111111",
    declared: list[tuple[str, float]] | None = None,
) -> UboDeclaration:
    return UboDeclaration(
        document_date=date.fromisoformat(doc_date),
        legal_name="Test A.Ş.",
        tax_id=tax_id,
        shareholders=[Shareholder(name=n, ownership_pct=p) for n, p in shareholders],
        declared_ubo=[
            UboEntry(name=n, ultimate_ownership_pct=p, ownership_path="doğrudan pay sahipliği")
            for n, p in (declared or [])
        ],
        declarant_name=declarant,
    )


def circular(
    signatories: list[tuple[str, str, str | None]],
    doc_date: str,
    tax_id: str = "1111111111",
) -> SignatureCircular:
    return SignatureCircular(
        document_date=date.fromisoformat(doc_date),
        legal_name="Test A.Ş.",
        tax_id=tax_id,
        authorized_signatories=[
            Signatory(
                name=n,
                authority_type=t,  # type: ignore[arg-type]
                valid_until=date.fromisoformat(v) if v else None,
            )
            for n, t, v in signatories
        ],
        notary_reference="Test Noterliği 1/1",
    )
