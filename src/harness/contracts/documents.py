"""Dossier document contracts — the ONLY structured data the agent is allowed to see.

The three documents mirror a Turkish trade-registry KYC dossier:

* ``RegistryDoc``       (B1) — the Trade Registry record. Authoritative for IDENTITY
                               (tax_id, canonical legal_name). See the authority model
                               in ``harness.agent.guardrail``.
* ``SignatureCircular`` (B2) — the notarised signature circular (imza sirküleri).
* ``UboDeclaration``    (B3) — the ultimate-beneficial-owner declaration.

All three carry ``document_date`` and ``tax_id`` so that cross-document checks can
reason about temporal precedence and identity consistency.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Doc(BaseModel):
    """Base for the dossier documents: forbid unexpected fields so that a malformed
    dossier fails loudly at extract time rather than silently dropping data."""

    model_config = ConfigDict(extra="forbid")


class Shareholder(_Doc):
    name: str
    ownership_pct: float


class Signatory(_Doc):
    name: str
    authority_type: Literal["sole", "joint"]
    valid_until: Optional[date] = None


class UboEntry(_Doc):
    name: str
    ultimate_ownership_pct: float
    ownership_path: str


class RegistryDoc(_Doc):
    """B1 — Trade Registry document. Authoritative for identity fields."""

    document_date: date
    legal_name: str
    tax_id: str
    incorporation_date: date
    registered_address: str
    share_capital: float
    shareholders: list[Shareholder] = Field(default_factory=list)
    company_status: Literal["active", "suspended", "liquidation"]


class SignatureCircular(_Doc):
    """B2 — Notarised signature circular."""

    document_date: date
    legal_name: str
    tax_id: str
    authorized_signatories: list[Signatory] = Field(default_factory=list)
    notary_reference: str


class UboDeclaration(_Doc):
    """B3 — Ultimate-beneficial-owner declaration."""

    document_date: date
    legal_name: str
    tax_id: str
    shareholders: list[Shareholder] = Field(default_factory=list)
    declared_ubo: list[UboEntry] = Field(default_factory=list)
    declarant_name: str


class Dossier(_Doc):
    """The complete input handed to the agent. Any document may be absent (None):
    ``check_completeness`` turns that into an E1 finding and downstream checks that
    need the missing document are SKIPPED (ran=False), never reported as ran-clean."""

    dossier_id: str
    registry: Optional[RegistryDoc] = None
    circular: Optional[SignatureCircular] = None
    ubo: Optional[UboDeclaration] = None
