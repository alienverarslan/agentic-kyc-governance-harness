"""The promoted-rule store (Faz 3, part 4): where a candidate rule becomes permanent.

``promote_rule`` is the ONLY way to add a rule here, and it structurally requires two
things no code path can fake: (1) a non-empty ``approved_by`` human identifier, and (2) a
``GateResult`` for the SAME rule with ``passed=True``. This is the human-approval
invariant enforced in code, not by convention: there is no function in this module that
persists a rule without both.

The store starts empty (no file on disk), so wiring ``check_learned_rules`` into the graph
with ``load_promoted_rules()`` as its default input is a no-op today — zero behavior change
for every existing test, eval run, or API call — until a human actually promotes a rule.

The default path lives under ``harness/data/`` (like the seed fixtures) and is resolved
relative to THIS file, not the process's working directory — deliberately NOT under
``artifacts/`` (gitignored, meant for ephemeral eval/report output). A promoted rule is
permanent, human-approved, governance-relevant state: the whole point of the Faz 3 loop is
that the deterministic rule set keeps growing across sessions, so it must be committed to
version control, not silently discarded like a report."""

from __future__ import annotations

import json
from pathlib import Path

from harness.rules.gate import GateResult
from harness.rules.schema import CandidateRule

DEFAULT_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "promoted_rules.json"


def load_promoted_rules(path: Path = DEFAULT_STORE_PATH) -> list[CandidateRule]:
    """Return the currently-promoted rules, or ``[]`` if nothing has been promoted yet."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [CandidateRule.model_validate(r) for r in raw]


def save_promoted_rules(rules: list[CandidateRule], path: Path = DEFAULT_STORE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.model_dump() for r in rules], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def promote_rule(
    rule: CandidateRule,
    *,
    approved_by: str,
    gate_report: GateResult,
    path: Path = DEFAULT_STORE_PATH,
) -> list[CandidateRule]:
    """Promote ``rule`` into the permanent store. Returns the updated promoted-rule list.

    Raises ``ValueError`` (never silently no-ops) if: no human identifier is given, the
    gate did not pass, the gate report is for a different rule, or the rule_id already
    exists — a promotion is a one-way, auditable, non-overwriting action.
    """
    if not approved_by or not approved_by.strip():
        raise ValueError("promote_rule requires a non-empty human `approved_by` identifier")
    if gate_report.rule_id != rule.rule_id or gate_report.template_id != rule.template_id:
        raise ValueError("gate_report does not match the candidate rule being promoted")
    if not gate_report.passed:
        raise ValueError(
            f"cannot promote rule {rule.rule_id!r}: validation gate did not pass "
            f"({len(gate_report.regressions)} regression(s), {len(gate_report.param_errors)} param error(s))"
        )

    existing = load_promoted_rules(path)
    if any(r.rule_id == rule.rule_id for r in existing):
        raise ValueError(f"a rule with rule_id {rule.rule_id!r} is already promoted")

    existing.append(rule)
    save_promoted_rules(existing, path)
    return existing
