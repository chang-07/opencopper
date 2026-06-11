"""Country-tier Monte Carlo: closed-form calibration, determinism, clamp
behavior, exclusions."""

from pathlib import Path

import pytest

from opencopper.commodities import load_commodity_scenario
from opencopper.montecarlo import simulate_commodity


def test_baseline_calibration_by_construction():
    """The gap-noise sigma is derived analytically from realized vol — the
    simulated vol must land near the target without any fitting loop."""
    for name in ("nickel", "zinc", "aluminum"):
        mc = simulate_commodity(name, n_paths=2500, seed=7)
        assert mc.simulated_annual_vol == pytest.approx(mc.target_vol, rel=0.30)


def test_baseline_centers_on_anchor_and_orders_bands():
    mc = simulate_commodity("nickel", n_paths=2000, seed=11)
    from opencopper.pricing import load_pricebook

    anchor = load_pricebook().commodities["nickel"].anchor_usd
    assert mc.price.p50[0] == pytest.approx(anchor, rel=0.05)
    for i in range(len(mc.years)):
        assert mc.price.p10[i] <= mc.price.p50[i] <= mc.price.p90[i]


def test_gold_is_excluded():
    assert simulate_commodity("gold", n_paths=100) is None


def test_seeded_determinism():
    a = simulate_commodity("tin", n_paths=500, seed=3)
    b = simulate_commodity("tin", n_paths=500, seed=3)
    assert a.price.p50 == b.price.p50 and a.prob_double == b.prob_double


def test_quota_scenario_saturates_clamp_then_reverts():
    sc = load_commodity_scenario(
        Path(__file__).resolve().parents[1] / "scenarios" / "commodities" / "drc-cobalt-quota.yaml"
    )
    mc = simulate_commodity("cobalt", sc, n_paths=1500, seed=5)
    from opencopper.pricing import INCIDENCE_CLAMP, load_pricebook

    anchor = load_pricebook().commodities["cobalt"].anchor_usd
    cap = anchor * INCIDENCE_CLAMP[1]
    i26 = mc.years.index(2026)
    i29 = mc.years.index(2029)
    assert mc.price.p50[i26] == pytest.approx(cap)       # quota years pin the cap
    assert mc.prob_double[2026] > 0.95
    assert mc.price.p50[i29] < cap * 0.5                 # reverts after the quota lapses
    assert mc.prob_double[2029] < 0.1
