"""Provenance capture — hermetic (git subprocess monkeypatched, no dependency on the
actual repo's dirty state), plus the canonical-hash properties P5 relies on.
"""

import pytest

from harness.eval import provenance


# --- git dirty / sha, fully mocked ----------------------------------------------------
def test_git_dirty_true_when_status_has_untracked(monkeypatch):
    def fake(args):
        if args[0] == "rev-parse":
            return "abc123\n"
        if args[0] == "status":
            return "?? some_new_file.py\n"  # untracked shows up here, not in `git diff`
        return None

    monkeypatch.setattr(provenance, "_run_git", fake)
    info = provenance.git_info()
    assert info["commit_sha"] == "abc123"
    assert info["dirty"] is True


def test_git_clean_when_status_empty(monkeypatch):
    monkeypatch.setattr(provenance, "_run_git", lambda args: "" if args[0] == "status" else "def456\n")
    info = provenance.git_info()
    assert info["commit_sha"] == "def456"
    assert info["dirty"] is False


def test_git_dirty_is_none_not_false_when_undeterminable(monkeypatch):
    # git missing / not a repo: dirty must be None, NEVER False (a false 'clean' claim).
    monkeypatch.setattr(provenance, "_run_git", lambda args: None)
    info = provenance.git_info()
    assert info["commit_sha"] is None
    assert info["dirty"] is None


# --- canonical hashing ----------------------------------------------------------------
def test_hash_is_stable_across_calls():
    payload = [("b", {"x": 1}), ("a", {"y": [1, 2, 3]})]
    assert provenance.hash_corpus(payload) == provenance.hash_corpus(payload)


def test_hash_is_order_independent_by_case_id():
    a = [("case_a", {"v": 1}), ("case_b", {"v": 2})]
    b = [("case_b", {"v": 2}), ("case_a", {"v": 1})]
    assert provenance.hash_corpus(a) == provenance.hash_corpus(b)


def test_hash_changes_when_content_changes_even_if_ids_stay():
    # This is the whole reason the hash is over CONTENT, not the seed: same ids, different
    # generated content -> different hash. Seed-only hashing would have collided here.
    base = [("case_a", {"capital": 1_000_000})]
    changed = [("case_a", {"capital": 75_000_000})]
    assert provenance.hash_corpus(base) != provenance.hash_corpus(changed)


def test_hash_prefixed_and_sha256():
    h = provenance.hash_corpus([("a", {"v": 1})])
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_promoted_rules_hash_order_independent():
    r1 = [{"rule_id": "z", "params": {}}, {"rule_id": "a", "params": {}}]
    r2 = [{"rule_id": "a", "params": {}}, {"rule_id": "z", "params": {}}]
    assert provenance.hash_promoted_rules(r1) == provenance.hash_promoted_rules(r2)


# --- env / package versions -----------------------------------------------------------
def test_package_versions_derived_from_pyproject_not_hardcoded():
    versions = provenance.package_versions()
    # pydantic is a declared dependency and is installed in the test env.
    assert "pydantic" in versions
    assert versions["pydantic"] is not None


def test_run_provenance_block_never_raises_and_has_keys(monkeypatch):
    monkeypatch.setattr(provenance, "_run_git", lambda args: None)
    block = provenance.build_run_provenance(
        run_type="deterministic_offline", timestamp_utc="2026-07-17T00:00:00Z", run_id="rid"
    )
    assert block["run_type"] == "deterministic_offline"
    assert block["git"]["dirty"] is None
    assert "package_versions" in block
    assert block["harness_version"]
