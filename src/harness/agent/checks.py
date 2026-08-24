"""The deterministic check tools + cross-document entity map + synthesis prompt.

Each check is a SEPARATE tool that emits taxonomy-coded, severity-tagged findings. A
single monolithic "check everything" function is a design failure: the trajectory must
record which checks actually ran. The graph (``harness.agent.graph``) decides whether a
check CAN run (its required documents are present); these functions assume they can and
compute findings.

Severity contract (drives the guardrail):
* ``info``          — noted, never changes the decision (A1).
* ``explainable``   — an explanation obligation that a human can plausibly discharge
                      (B1a, B2a, C1a, C2, E1, E2) → request_more_info.
* ``unexplainable`` — no benign single-document explanation (A2, B1b, B2b, C1b) →
                      escalate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from harness.contracts.documents import (
    Dossier,
    RegistryDoc,
    Shareholder,
    SignatureCircular,
    UboDeclaration,
)
from harness.contracts.findings import CheckResult, Finding
from harness.normalize.config import AUTHORITY_STALENESS_DAYS, UBO_THRESHOLD_PCT
from harness.normalize.turkish import (
    legal_names_match,
    matching_key,
    person_names_match,
)

_EPS = 0.01  # percentage-point tolerance for float comparisons


# --------------------------------------------------------------------------------------
# Cross-document entity map (produced by the resolve_entities node)
# --------------------------------------------------------------------------------------
@dataclass
class EntityMap:
    """Normalized cross-document view of every name in the dossier.

    Matching is delegated to the Turkish-aware predicates so ALL name comparisons in
    the checks flow through folded, suffix-canonicalized keys — never raw strings. The
    ``person_keys`` / ``legal_keys`` clusters are retained purely for auditability.
    """

    person_keys: dict[str, list[str]] = field(default_factory=dict)
    legal_keys: dict[str, list[str]] = field(default_factory=dict)

    def same_person(self, a: str, b: str) -> bool:
        return person_names_match(a, b)

    def same_legal(self, a: str, b: str) -> bool:
        return legal_names_match(a, b)


def build_entity_map(dossier: Dossier) -> EntityMap:
    """Collect and cluster every name across all present documents by matching key."""
    emap = EntityMap()

    def add(bucket: dict[str, list[str]], name: str) -> None:
        bucket.setdefault(matching_key(name), []).append(name)

    for doc in (dossier.registry, dossier.circular, dossier.ubo):
        if doc is None:
            continue
        add(emap.legal_keys, doc.legal_name)
    if dossier.registry is not None:
        for sh in dossier.registry.shareholders:
            add(emap.person_keys, sh.name)
    if dossier.circular is not None:
        for sig in dossier.circular.authorized_signatories:
            add(emap.person_keys, sig.name)
    if dossier.ubo is not None:
        for sh in dossier.ubo.shareholders:
            add(emap.person_keys, sh.name)
        add(emap.person_keys, dossier.ubo.declarant_name)
    return emap


# --------------------------------------------------------------------------------------
# check_identity_consistency  (A1 / A2)
# --------------------------------------------------------------------------------------
def check_identity_consistency(dossier: Dossier, emap: EntityMap) -> CheckResult:
    """3-way legal_name + tax_id consistency after normalization.

    A2: a tax_id mismatch across documents. Identity conflicts admit no explanation →
        unexplainable → escalate.
    A1: legal names vary only in SURFACE form but resolve to the same entity (diacritic
        folding + suffix canonicalization) with consistent tax_ids → info, NOT a
        contradiction. This is the false-positive trap: naive casing would fabricate an
        identity conflict here (seed #3).
    """
    name = "check_identity_consistency"
    docs = [
        (label, doc)
        for label, doc in (
            ("registry", dossier.registry),
            ("circular", dossier.circular),
            ("ubo", dossier.ubo),
        )
        if doc is not None
    ]
    findings: list[Finding] = []

    # tax_id: any pairwise mismatch is an unexplainable identity conflict (A2).
    tax_ids = {label: doc.tax_id.strip() for label, doc in docs}
    distinct_tax = {t for t in tax_ids.values()}
    if len(distinct_tax) > 1:
        findings.append(
            Finding(
                check_name=name,
                code="A2",
                severity="unexplainable",
                detail=f"tax_id differs across documents: {tax_ids}",
                fields_involved=["tax_id"],
            )
        )

    # legal_name: surface variation that still resolves to one entity is benign (A1).
    surfaces = {doc.legal_name for _label, doc in docs}
    if len(surfaces) > 1:
        # Confirm they really are the same entity after normalization.
        base = docs[0][1].legal_name
        all_same_entity = all(emap.same_legal(base, doc.legal_name) for _l, doc in docs)
        if all_same_entity:
            findings.append(
                Finding(
                    check_name=name,
                    code="A1",
                    severity="info",
                    detail=(
                        "legal_name varies in surface form but normalizes to one "
                        f"entity: {sorted(surfaces)}"
                    ),
                    fields_involved=["legal_name"],
                )
            )
    return CheckResult(check_name=name, ran=True, findings=findings)


# --------------------------------------------------------------------------------------
# check_ownership_consistency  (B1a / B1b / B2a / B2b)
# --------------------------------------------------------------------------------------
def _match_shareholders(
    s1: list[Shareholder], s3: list[Shareholder], emap: EntityMap
) -> tuple[list[tuple[Shareholder, Shareholder]], list[Shareholder], list[Shareholder]]:
    """Return (continuing pairs, exited (in s1 only), new (in s3 only))."""
    used_s3: set[int] = set()
    continuing: list[tuple[Shareholder, Shareholder]] = []
    exited: list[Shareholder] = []
    for a in s1:
        match_idx = None
        for j, b in enumerate(s3):
            if j in used_s3:
                continue
            if emap.same_person(a.name, b.name):
                match_idx = j
                break
        if match_idx is None:
            exited.append(a)
        else:
            used_s3.add(match_idx)
            continuing.append((a, s3[match_idx]))
    new = [b for j, b in enumerate(s3) if j not in used_s3]
    return continuing, exited, new


def check_ownership_consistency(
    registry: RegistryDoc, ubo: UboDeclaration, emap: EntityMap
) -> CheckResult:
    """Reconcile B1.shareholders against B3.shareholders.

    Sum checks first (structural sanity):
    * B2b: a shareholder list sums to > 100 — structurally impossible → unexplainable.
    * B2a: a shareholder list sums to < 100 — a gap closable by one missing record →
           explainable.

    If both lists sum to 100 and their structures differ, apply the THREE-CONDITION
    explainability test. ALL must hold for an explainable transfer (B1a):
      (i)   B3 is the newer view: ubo.document_date > registry.document_date;
      (ii)  the shareholder name sets OVERLAP (>= 1 continuing shareholder);
      (iii) the arithmetic reconciles as a simple transfer: the exited holders' total
            percentage equals the total gained by continuing/new holders (both lists
            already sum to 100).
    If the lists differ and ANY of (i)-(iii) fails → B1b, unexplainable → escalate.
    Date direction ALONE is neither sufficient nor necessary; the three conditions are
    the rule. (Seed #4 passes all three; seed #5 fails (i).)
    """
    name = "check_ownership_consistency"
    s1, s3 = registry.shareholders, ubo.shareholders
    sum1 = sum(sh.ownership_pct for sh in s1)
    sum3 = sum(sh.ownership_pct for sh in s3)

    # --- sum sanity (B2b before B2a: impossibility outranks a gap) ---
    if sum1 > 100 + _EPS or sum3 > 100 + _EPS:
        return CheckResult(
            check_name=name,
            ran=True,
            findings=[
                Finding(
                    check_name=name,
                    code="B2b",
                    severity="unexplainable",
                    detail=f"shareholding sums exceed 100% (B1={sum1}, B3={sum3})",
                    fields_involved=["shareholders.ownership_pct"],
                )
            ],
        )
    if sum1 < 100 - _EPS or sum3 < 100 - _EPS:
        return CheckResult(
            check_name=name,
            ran=True,
            findings=[
                Finding(
                    check_name=name,
                    code="B2a",
                    severity="explainable",
                    detail=(
                        f"shareholding sums below 100% (B1={sum1}, B3={sum3}); a single "
                        "missing record could close the gap"
                    ),
                    fields_involved=["shareholders.ownership_pct"],
                )
            ],
        )

    # --- both sum to 100: compare structure ---
    continuing, exited, new = _match_shareholders(s1, s3, emap)
    structurally_same = (
        not exited
        and not new
        and all(abs(a.ownership_pct - b.ownership_pct) < _EPS for a, b in continuing)
    )
    if structurally_same:
        return CheckResult(check_name=name, ran=True, findings=[])

    # --- lists differ: three-condition explainability test ---
    cond_i = ubo.document_date > registry.document_date
    cond_ii = len(continuing) >= 1
    exited_total = sum(sh.ownership_pct for sh in exited)
    gained = sum(max(0.0, b.ownership_pct - a.ownership_pct) for a, b in continuing)
    gained += sum(sh.ownership_pct for sh in new)
    cond_iii = abs(exited_total - gained) < _EPS

    if cond_i and cond_ii and cond_iii:
        return CheckResult(
            check_name=name,
            ran=True,
            findings=[
                Finding(
                    check_name=name,
                    code="B1a",
                    severity="explainable",
                    detail=(
                        "ownership delta reconciles as a simple transfer (B3 newer, "
                        f"overlap, exited {exited_total}% == gained {gained}%); a "
                        "transfer document should be requested"
                    ),
                    fields_involved=["shareholders"],
                )
            ],
        )

    failed = [
        label
        for label, ok in (("(i) B3-newer", cond_i), ("(ii) overlap", cond_ii), ("(iii) arithmetic", cond_iii))
        if not ok
    ]
    return CheckResult(
        check_name=name,
        ran=True,
        findings=[
            Finding(
                check_name=name,
                code="B1b",
                severity="unexplainable",
                detail=(
                    "ownership delta is not an explainable transfer; failed "
                    f"conditions: {failed}"
                ),
                fields_involved=["shareholders"],
            )
        ],
    )


# --------------------------------------------------------------------------------------
# check_authority_chain  (C1a / C1b / C2)
# --------------------------------------------------------------------------------------
def check_authority_chain(
    circular: SignatureCircular, ubo: UboDeclaration, emap: EntityMap
) -> CheckResult:
    """Verify the UBO-declaration signer against the signature circular (B2 x B3).

    Membership runs on NORMALIZED names (seed #3's declarant matches a signatory only
    after folding).

    * C1a: declarant absent from the circular AND the circular is MATERIALLY older than
           B3 (> AUTHORITY_STALENESS_DAYS) — a newer appointee plausibly exists →
           explainable → request_more_info.
    * C1b: declarant absent AND the circular is contemporaneous-with or NEWER than B3 —
           the temporal explanation is closed → unexplainable → escalate.
    * C2:  declarant IS a signatory but their authority expired before B3 →
           explainable → request_more_info.

    A signatory who is not a shareholder is NOT a finding (professional managers hold
    signing authority; seed #6 encodes this deliberately).
    """
    name = "check_authority_chain"
    declarant = ubo.declarant_name
    matched = next(
        (s for s in circular.authorized_signatories if emap.same_person(declarant, s.name)),
        None,
    )

    if matched is not None:
        if matched.valid_until is not None and matched.valid_until < ubo.document_date:
            return CheckResult(
                check_name=name,
                ran=True,
                findings=[
                    Finding(
                        check_name=name,
                        code="C2",
                        severity="explainable",
                        detail=(
                            f"declarant {declarant!r} authority expired "
                            f"{matched.valid_until} before declaration {ubo.document_date}"
                        ),
                        fields_involved=["declarant_name", "authorized_signatories.valid_until"],
                    )
                ],
            )
        return CheckResult(check_name=name, ran=True, findings=[])

    delta_days = (ubo.document_date - circular.document_date).days
    if delta_days > AUTHORITY_STALENESS_DAYS:
        return CheckResult(
            check_name=name,
            ran=True,
            findings=[
                Finding(
                    check_name=name,
                    code="C1a",
                    severity="explainable",
                    detail=(
                        f"declarant {declarant!r} not in circular, but circular is "
                        f"{delta_days} days older than the declaration; a current "
                        "circular should be requested"
                    ),
                    fields_involved=["declarant_name", "authorized_signatories"],
                )
            ],
        )
    return CheckResult(
        check_name=name,
        ran=True,
        findings=[
            Finding(
                check_name=name,
                code="C1b",
                severity="unexplainable",
                detail=(
                    f"declarant {declarant!r} not in circular, and circular is "
                    f"contemporaneous-or-newer ({delta_days} days delta); temporal "
                    "explanation is closed"
                ),
                fields_involved=["declarant_name", "authorized_signatories"],
            )
        ],
    )


# --------------------------------------------------------------------------------------
# check_completeness  (E1 / E2)
# --------------------------------------------------------------------------------------
def check_completeness(dossier: Dossier) -> CheckResult:
    """Presence of documents (E1) and mandatory business fields (E2).

    Both are explainable → request_more_info (a human can supply the missing item).
    E1 additionally drives downstream check SKIPPING in the graph.
    """
    name = "check_completeness"
    findings: list[Finding] = []

    for label, doc in (
        ("registry", dossier.registry),
        ("circular", dossier.circular),
        ("ubo", dossier.ubo),
    ):
        if doc is None:
            findings.append(
                Finding(
                    check_name=name,
                    code="E1",
                    severity="explainable",
                    detail=f"required document missing: {label}",
                    fields_involved=[label],
                )
            )

    # E2: present-but-empty mandatory business fields (no seed case; unit-tested).
    def flag_empty(label: str, present: bool) -> None:
        if not present:
            findings.append(
                Finding(
                    check_name=name,
                    code="E2",
                    severity="explainable",
                    detail=f"mandatory field empty: {label}",
                    fields_involved=[label],
                )
            )

    if dossier.registry is not None:
        flag_empty("registry.tax_id", bool(dossier.registry.tax_id.strip()))
        flag_empty("registry.legal_name", bool(dossier.registry.legal_name.strip()))
        flag_empty("registry.shareholders", len(dossier.registry.shareholders) > 0)
    if dossier.circular is not None:
        flag_empty("circular.tax_id", bool(dossier.circular.tax_id.strip()))
        flag_empty(
            "circular.authorized_signatories",
            len(dossier.circular.authorized_signatories) > 0,
        )
    if dossier.ubo is not None:
        flag_empty("ubo.tax_id", bool(dossier.ubo.tax_id.strip()))
        flag_empty("ubo.declarant_name", bool(dossier.ubo.declarant_name.strip()))

    return CheckResult(check_name=name, ran=True, findings=findings)


# --------------------------------------------------------------------------------------
# check_ubo_derivation  (D1a / D1b)
# --------------------------------------------------------------------------------------
def check_ubo_derivation(ubo: UboDeclaration, emap: EntityMap) -> CheckResult:
    """Reconcile the declared UBOs against the declaration's own ownership structure.

    THRESHOLD CONSTRAINT (load-bearing): only owners at/above ``UBO_THRESHOLD_PCT``
    (25%) must be declared. A shareholder BELOW the threshold is legitimately absent
    from declared_ubo and MUST NOT be flagged — seed #2 encodes exactly this (a 20%
    shareholder is intentionally not a declared UBO, and that is not a gap).

    Slice-1 substrate is a FLAT ownership structure (direct holdings only; no graph /
    network analysis — that is out of scope). So "derivation" here is: every direct
    shareholder whose stake ≥ threshold must appear in declared_ubo. Findings:

    * D1b (unexplainable → escalate): a structurally impossible declaration — a declared
      ultimate ownership percentage outside [0, 100], or the declared ultimate holdings
      summing to more than 100%. No benign reading; escalate.
    * D1a (explainable → request_more_info): an at/above-threshold shareholder is missing
      from declared_ubo. The declaration is incomplete; request a corrected one. (Default
      per DECISIONS.md: an incomplete declaration is explainable, not fraud-by-assumption.)

    A declared UBO who is not a direct shareholder is NOT flagged: they may hold
    indirectly via ``ownership_path``, and over-declaration is not a risk. Percentage
    mismatches for legitimately indirect holders are likewise left to slice-2's graph
    model rather than guessed at here.
    """
    name = "check_ubo_derivation"
    findings: list[Finding] = []

    # D1b: structural impossibility.
    declared_sum = sum(u.ultimate_ownership_pct for u in ubo.declared_ubo)
    impossible = declared_sum > 100 + _EPS or any(
        u.ultimate_ownership_pct < -_EPS or u.ultimate_ownership_pct > 100 + _EPS
        for u in ubo.declared_ubo
    )
    if impossible:
        findings.append(
            Finding(
                check_name=name,
                code="D1b",
                severity="unexplainable",
                detail=(
                    "declared UBO holdings are structurally impossible "
                    f"(sum={declared_sum}%, entries={[u.ultimate_ownership_pct for u in ubo.declared_ubo]})"
                ),
                fields_involved=["declared_ubo.ultimate_ownership_pct"],
            )
        )
        return CheckResult(check_name=name, ran=True, findings=findings)

    # D1a: an at/above-threshold shareholder is missing from declared_ubo.
    for sh in ubo.shareholders:
        if sh.ownership_pct + _EPS < UBO_THRESHOLD_PCT:
            continue  # below threshold: legitimately not a declared UBO (seed #2)
        declared = any(emap.same_person(sh.name, u.name) for u in ubo.declared_ubo)
        if not declared:
            findings.append(
                Finding(
                    check_name=name,
                    code="D1a",
                    severity="explainable",
                    detail=(
                        f"shareholder {sh.name!r} holds {sh.ownership_pct}% "
                        f"(≥{UBO_THRESHOLD_PCT}%) but is not in declared_ubo; a corrected "
                        "declaration should be requested"
                    ),
                    fields_involved=["shareholders", "declared_ubo"],
                )
            )
    return CheckResult(check_name=name, ran=True, findings=findings)


# --------------------------------------------------------------------------------------
# Synthesis prompt construction (the single LLM call's input)
# --------------------------------------------------------------------------------------
SYNTHESIS_SYSTEM = (
    "You are a compliance analyst reviewing the results of deterministic cross-document "
    "consistency checks on a corporate KYC dossier. You do NOT re-run the checks; you "
    "PROPOSE a decision from their findings. Allowed decisions: 'approve', "
    "'request_more_info', 'escalate'. There is no 'reject': even suspected fraud is an "
    "escalation. Guidance: any unexplainable finding warrants escalate; explainable "
    "findings warrant request_more_info; only info/no findings warrant approve. Return "
    "a SynthesisProposal. A deterministic guardrail may override your proposal."
)


def build_synthesis_user(dossier_id: str, results: list[CheckResult]) -> str:
    """Serialize check results as the JSON user payload for the synthesize call.

    The payload is machine-readable so the offline stubs can parse it while a real LLM
    reads it as structured context.
    """
    return json.dumps(
        {
            "dossier_id": dossier_id,
            "check_results": [
                {
                    "check_name": r.check_name,
                    "ran": r.ran,
                    "findings": [
                        {"code": f.code, "severity": f.severity, "detail": f.detail}
                        for f in r.findings
                    ],
                }
                for r in results
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
