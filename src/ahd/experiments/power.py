"""Power of the E2 cluster-level sign-flip permutation test (E0d-F, rule D8).

No reference source: written fresh for ahd. Model: for N clusters the per-cluster effect
estimate is y_i = delta + b_i + e_i, in pass-rate points, with b_i ~ N(0, spread^2) the
between-cluster spread of true effects and e_i ~ N(0, sigma_seed^2 / (k * passes)) the
held-out measurement noise of a cluster averaged over k proposal replicates, each evaluated
with ``passes`` independent held-out passes of sigma_seed noise each. The test is the exact
two-sided sign-flip permutation test on the mean of y (all 2^N sign vectors), alpha = 0.05.
Power at delta is the fraction of simulated data sets rejecting; the minimum detectable effect
(MDE) is the smallest delta (to ``step`` points) with power >= ``target``.
"""

from __future__ import annotations

from functools import lru_cache
from math import sqrt

import numpy as np
from numpy.typing import NDArray

ALPHA = 0.05
TARGET_POWER = 0.8
_CHUNK = 256


@lru_cache(maxsize=8)
def _signs(n: int) -> NDArray[np.float64]:
    """All 2^n sign vectors as an (2^n, n) matrix of +-1."""
    if n > 20:
        raise ValueError("exact enumeration is limited to n <= 20 clusters")
    codes = np.arange(1 << n, dtype=np.int64)[:, None]
    bits = (codes >> np.arange(n, dtype=np.int64)) & 1
    return (2.0 * bits - 1.0).astype(np.float64)


def sign_flip_p(y: NDArray[np.float64]) -> float:
    """Exact two-sided p-value of the sign-flip test on the mean of ``y``."""
    signs = _signs(len(y))
    observed = abs(float(y.sum()))
    distribution = np.abs(signs @ y)
    return float(np.mean(distribution >= observed - 1e-12))


def power(
    delta: float,
    *,
    n_clusters: int,
    k: int,
    passes: int,
    sigma_seed: float,
    spread: float,
    sims: int = 1000,
    seed: int = 0,
    alpha: float = ALPHA,
) -> float:
    rng = np.random.default_rng([seed, n_clusters, k, passes, round(delta * 1000)])
    signs = _signs(n_clusters)
    noise_sd = sigma_seed / sqrt(k * passes)
    rejected = 0
    done = 0
    while done < sims:
        m = min(_CHUNK, sims - done)
        y = (
            delta
            + rng.normal(0.0, spread, (m, n_clusters))
            + rng.normal(0.0, noise_sd, (m, n_clusters))
        )
        observed = np.abs(y.sum(axis=1))
        distribution = np.abs(y @ signs.T)
        p = (distribution >= observed[:, None] - 1e-12).mean(axis=1)
        rejected += int((p <= alpha).sum())
        done += m
    return rejected / sims


def mde(
    *,
    n_clusters: int,
    k: int,
    passes: int,
    sigma_seed: float,
    spread: float,
    target: float = TARGET_POWER,
    sims: int = 1000,
    seed: int = 0,
    step: float = 0.25,
    max_delta: float = 40.0,
) -> float | None:
    """Smallest delta (pass-rate points, rounded up to ``step``) with power >= ``target``;
    ``None`` when even ``max_delta`` is not detected."""

    def ok(delta: float) -> bool:
        return (
            power(
                delta,
                n_clusters=n_clusters,
                k=k,
                passes=passes,
                sigma_seed=sigma_seed,
                spread=spread,
                sims=sims,
                seed=seed,
            )
            >= target
        )

    if not ok(max_delta):
        return None
    lo, hi = 0.0, max_delta
    while hi - lo > step:
        mid = (lo + hi) / 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return float(round(float(np.ceil(hi / step) * step), 4))
