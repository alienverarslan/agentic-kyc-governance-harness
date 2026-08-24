"""Wilson 95% CI helper — edge cases and an external reference value.

The load-bearing property is that Wilson does NOT degenerate at k=0 / k=n the way the
naive Wald interval does (Wald gives [0,0] at k=0, an overconfident lie). n=0 must be an
explicit None, never a fabricated 0.
"""

import math

import pytest

from harness.eval.stats import RateStat, wilson_ci


def test_n_zero_is_none_not_a_fabricated_zero():
    assert wilson_ci(0, 0) is None


def test_k_zero_does_not_degenerate_to_zero_width():
    ci = wilson_ci(0, 10)
    assert ci is not None
    low, high = ci
    assert low == 0.0
    # External reference: Wilson upper bound for 0/10 is ~0.2775 (unlike Wald, which gives 0).
    assert high == pytest.approx(0.2775, abs=2e-3)


def test_k_equals_n_is_symmetric_and_not_zero_width():
    ci = wilson_ci(10, 10)
    assert ci is not None
    low, high = ci
    # The Wilson upper bound at k=n is mathematically 1.0; allow for floating-point noise
    # (the clamp can't round a value that lands just BELOW 1.0 back up). Human-facing output
    # renders this as 100.0% regardless.
    assert high == pytest.approx(1.0)
    # By symmetry with the 0/10 case: 1 - 0.2775.
    assert low == pytest.approx(1 - 0.2775, abs=2e-3)
    # The load-bearing property: NOT a degenerate zero-width [1, 1] band.
    assert low < high


def test_midpoint_reference_value():
    # 90/100: standard Wilson 95% interval, cross-checked by direct recomputation.
    ci = wilson_ci(90, 100)
    assert ci is not None
    low, high = ci
    assert low == pytest.approx(0.8256, abs=2e-3)
    assert high == pytest.approx(0.9448, abs=2e-3)


def test_interval_brackets_point_estimate():
    for k, n in [(1, 3), (5, 18), (17, 18), (250, 500)]:
        ci = wilson_ci(k, n)
        assert ci is not None
        low, high = ci
        assert 0.0 <= low <= k / n <= high <= 1.0


def test_small_n_interval_is_wide():
    # The literal "83% at small n is thin" case: 5/6 should carry a wide interval.
    ci = wilson_ci(5, 6)
    assert ci is not None
    low, high = ci
    assert high - low > 0.30  # honestly wide, not a tight false-confidence band


def test_out_of_range_k_raises():
    with pytest.raises(ValueError):
        wilson_ci(11, 10)
    with pytest.raises(ValueError):
        wilson_ci(-1, 10)


def test_ratestat_to_dict_shape():
    d = RateStat(0, 340).to_dict()
    assert d["k"] == 0
    assert d["n"] == 340
    assert d["point_estimate"] == 0.0
    assert d["wilson_ci_95"] is not None and len(d["wilson_ci_95"]) == 2


def test_ratestat_zero_denominator_is_all_none():
    d = RateStat(0, 0).to_dict()
    assert d["point_estimate"] is None
    assert d["wilson_ci_95"] is None
