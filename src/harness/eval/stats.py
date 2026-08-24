"""Statistical helpers for the P5 scientific report — stdlib only, on purpose.

The project has no numpy/scipy dependency and none is justified for a single closed-form
formula. ``wilson_ci`` implements the Wilson score interval directly from ``math.sqrt``.

A point estimate alone ("83% recall") is statistically thin at small n. Every rate in the
P5 report is required to carry its raw (k, n) alongside the Wilson 95% interval, via
``RateStat``, so a reader can see the uncertainty rather than a bare percentage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# z for a 95% two-sided normal interval (scipy.stats.norm.ppf(0.975)). Hardcoded because
# only 95% is required; a small {90%, 95%, 99%} table would be the extension point if a
# second confidence level is ever needed, still without a new dependency.
_Z95 = 1.959963984540054


def wilson_ci(k: int, n: int, z: float = _Z95) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion ``k/n``.

    Returns ``None`` iff ``n == 0`` — there is no interval to report, and the caller must
    render this as an explicit N/A, never a fabricated 0.0/[0,0]. ``k == 0`` and ``k == n``
    are handled by the closed-form itself (no special-casing): unlike the naive Wald
    interval, Wilson does not degenerate to a zero-width ``[0, 0]`` or ``[1, 1]`` at those
    extremes, which is exactly the kind of overconfident artifact this module exists to
    avoid.
    """
    if n == 0:
        return None
    if k < 0 or k > n:
        raise ValueError(f"k={k!r} must satisfy 0 <= k <= n={n!r}")

    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return (low, high)


@dataclass(frozen=True)
class RateStat:
    """A rate reported with its raw counts and Wilson 95% interval, never a bare float."""

    k: int
    n: int

    @property
    def point_estimate(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def wilson_ci_95(self) -> tuple[float, float] | None:
        return wilson_ci(self.k, self.n)

    def to_dict(self) -> dict[str, object]:
        ci = self.wilson_ci_95
        return {
            "k": self.k,
            "n": self.n,
            "point_estimate": self.point_estimate,
            "wilson_ci_95": [ci[0], ci[1]] if ci is not None else None,
        }
