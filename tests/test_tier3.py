"""Tier-3 honesty upgrades: Pink Sheet source, AR(1) tails, depletion,
byproduct metadata, price-parameter sensitivity."""

import pytest

from opencopper.balance import BASELINE, run
from opencopper.ledger import load_assumptions, load_ledger


def test_silver_history_via_pinksheet_when_cached():
    from opencopper.pinksheet import CACHE_DIR
    from opencopper.history import load_price_history

    if not (CACHE_DIR / "pinksheet-silver.csv").exists():
        pytest.skip("no pinksheet cache")
    h = load_price_history("silver")
    assert h is not None and h.series.startswith("PinkSheet:")
    assert len(h.months) > 600 and 0.15 < h.annual_volatility < 0.45


def test_depletion_retires_la_caridad_within_horizon():
    ledger, assumptions = load_ledger(), load_assumptions()
    mine = ledger.get("La Caridad")
    remaining = mine.remaining_reserves(2024)
    assert remaining is not None and 0 < remaining < 800
    rr = run(ledger, assumptions, BASELINE, range(2024, 2031))
    # an unreserved twin world: remove the reserve and compare late-horizon supply
    mine.reserves_kt = None
    rr_infinite = run(ledger, assumptions, BASELINE, range(2024, 2031))
    assert rr.row(2030).mine_supply_kt < rr_infinite.row(2030).mine_supply_kt
    # the shortfall is at most La Caridad's full output
    gap = rr_infinite.row(2030).mine_supply_kt - rr.row(2030).mine_supply_kt
    assert 0 < gap <= 130 + 1


def test_mines_without_reserves_are_unconstrained():
    mine = load_ledger().get("Escondida")
    assert mine.remaining_reserves(2030) is None


def test_ar1_persistence_widens_integrated_outcomes():
    """Stationary AR(1) keeps each YEAR's surprise variance identical to iid —
    the persistence signature lives in INTEGRATED quantities: inventory (and so
    cover) accumulates correlated shocks, so its multi-year spread widens."""
    from opencopper.montecarlo import DisruptionParams, simulate_copper

    persistent = simulate_copper(BASELINE, n_paths=1200, seed=9,
                                 params=DisruptionParams(disruption_rho=0.85, demand_rho=0.85))
    iid = simulate_copper(BASELINE, n_paths=1200, seed=9,
                          params=DisruptionParams(disruption_rho=0.0, demand_rho=0.0))
    spread_p = persistent.cover_days.p90[-1] - persistent.cover_days.p10[-1]
    spread_i = iid.cover_days.p90[-1] - iid.cover_days.p10[-1]
    assert spread_p > spread_i


def test_tail_shape_moments():
    from opencopper.calibrate import _moments

    skew, kurt = _moments([0.0, 0.0, 0.0, 10.0])  # one huge outlier
    assert skew > 0 and kurt > -3


def test_byproduct_metadata_loaded_and_reported():
    from opencopper.commodities import load_commodity, render_commodity_report, run_commodity

    cobalt = load_commodity("cobalt")
    assert cobalt.byproduct_of and "copper" in cobalt.byproduct_of[0]
    text = render_commodity_report(cobalt, run_commodity(cobalt))
    assert "BYPRODUCT" in text


def test_price_sensitivity_gamma_is_a_real_lever():
    from opencopper.sensitivity import run_price_sensitivity

    rows = run_price_sensitivity(year=2026)
    by_param = {r.param: r for r in rows}
    gamma = by_param["gamma"]
    assert gamma.swing > 1000  # thousands of $/t under the 2026 squeeze
    # higher gamma -> more convex -> higher price when tight
    assert gamma.high > gamma.base > gamma.low
