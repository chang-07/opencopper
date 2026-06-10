"""The stochastic simulator: determinism, unbiasedness, band ordering,
calibration. These are the properties that make Monte Carlo trustworthy."""

import pytest

from opencopper.balance import BASELINE
from opencopper.montecarlo import DisruptionParams, simulate_copper

YEARS = range(2024, 2031)


def test_seeded_runs_are_identical():
    a = simulate_copper(BASELINE, n_paths=300, seed=99)
    b = simulate_copper(BASELINE, n_paths=300, seed=99)
    assert a.price.p50 == b.price.p50
    assert a.prob_deficit == b.prob_deficit


def test_different_seeds_differ_but_converge():
    a = simulate_copper(BASELINE, n_paths=2000, seed=1)
    b = simulate_copper(BASELINE, n_paths=2000, seed=2)
    assert a.price.p50 != b.price.p50  # not identical
    # but medians agree within a few percent at this path count
    for x, y in zip(a.price.p50, b.price.p50):
        assert abs(x - y) / y < 0.08


def test_bands_are_ordered():
    mc = simulate_copper(BASELINE, n_paths=1500, seed=7)
    for i in range(len(mc.years)):
        assert mc.balance.p10[i] <= mc.balance.p50[i] <= mc.balance.p90[i]
        assert mc.price.p10[i] <= mc.price.p50[i] <= mc.price.p90[i]
        assert mc.cover_days.p10[i] <= mc.cover_days.p50[i] <= mc.cover_days.p90[i]


def test_baseline_median_is_near_balance_not_biased():
    """The MC adds a mean-zero surprise, so the baseline median must track the
    deterministic near-balance — not drift into a systematic deficit."""
    mc = simulate_copper(BASELINE, n_paths=3000, seed=42)
    assert abs(mc.balance.p50[0]) < 250  # 2024 within +-250kt of balance
    # median 2024 price within 20% of the anchor (no systematic bias)
    assert 7000 < mc.price.p50[0] < 11500


def test_price_distribution_is_right_skewed():
    """Commodity prices spike up more than down: the P90-P50 gap should exceed
    the P50-P10 gap (fat right tail)."""
    mc = simulate_copper(BASELINE, n_paths=4000, seed=42)
    up = mc.price.p90[0] - mc.price.p50[0]
    down = mc.price.p50[0] - mc.price.p10[0]
    assert up > down


def test_more_disruption_variance_widens_bands():
    calm = simulate_copper(BASELINE, n_paths=2000, seed=5,
                           params=DisruptionParams(disruption_cv=0.10, demand_sigma=0.005))
    wild = simulate_copper(BASELINE, n_paths=2000, seed=5,
                           params=DisruptionParams(disruption_cv=0.40, demand_sigma=0.02))
    calm_spread = calm.price.p90[2] - calm.price.p10[2]
    wild_spread = wild.price.p90[2] - wild.price.p10[2]
    assert wild_spread > calm_spread


def test_probabilities_are_valid():
    mc = simulate_copper(BASELINE, n_paths=1000, seed=3)
    for y in mc.years:
        assert 0.0 <= mc.prob_deficit[y] <= 1.0
        assert 0.0 <= mc.prob_price_spike[y] <= 1.0


@pytest.mark.slow
def test_calibration_hits_history():
    from opencopper.calibrate import calibrate_copper

    c = calibrate_copper(n_paths=1200, tol=0.015)
    assert abs(c.achieved_vol - c.target_vol) < 0.04  # within 4 vol points
