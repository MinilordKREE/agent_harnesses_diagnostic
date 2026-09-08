"""Sign-flip power simulation (E0d-F)."""

from __future__ import annotations

import numpy as np
import pytest

from ahd.experiments.power import mde, power, sign_flip_p


def test_sign_flip_p_is_exact_and_symmetric() -> None:
    y = np.array([1.0, 2.0, 3.0])
    # all eight sign vectors: |sum| >= 6 only for ++ + and ---: p = 2/8
    assert sign_flip_p(y) == pytest.approx(0.25)
    assert sign_flip_p(-y) == pytest.approx(0.25)
    assert sign_flip_p(np.array([0.0, 0.0])) == pytest.approx(1.0)


def test_power_is_alpha_at_zero_and_one_for_a_large_effect() -> None:
    null = power(0.0, n_clusters=8, k=3, passes=1, sigma_seed=5.0, spread=5.0, sims=400)
    assert null <= 0.12
    strong = power(40.0, n_clusters=8, k=3, passes=1, sigma_seed=5.0, spread=5.0, sims=200)
    assert strong == 1.0
    again = power(10.0, n_clusters=7, k=3, passes=2, sigma_seed=4.0, spread=5.0, sims=200)
    assert again == power(10.0, n_clusters=7, k=3, passes=2, sigma_seed=4.0, spread=5.0, sims=200)


def test_mde_shrinks_with_more_clusters_and_replicates() -> None:
    small = mde(n_clusters=7, k=3, passes=1, sigma_seed=6.0, spread=6.0, sims=300, step=0.5)
    large = mde(n_clusters=14, k=5, passes=2, sigma_seed=6.0, spread=6.0, sims=300, step=0.5)
    assert small is not None and large is not None and large <= small
    assert (
        mde(n_clusters=7, k=3, passes=1, sigma_seed=6.0, spread=6.0, sims=100, max_delta=0.5)
        is None
    )
