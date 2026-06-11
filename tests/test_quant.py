"""Quant layer: backtest machinery, regime-conditional vol, elasticity-range
bands, and the correlated book risk. Synthetic where the math must be exact;
real cached series where the claim is about the data."""

import math

import pytest

from opencopper.backtest import (
    backtest_commodity,
    deviations,
    nw_slope,
    summary,
)


# ---------------------------------------------------------------- machinery


def test_nw_slope_recovers_exact_linear_fit():
    x = [float(i) for i in range(40)]
    y = [2.0 * v + 1.0 for v in x]
    b, t = nw_slope(x, y, lag=3)
    assert b == pytest.approx(2.0, abs=1e-9)
    assert t > 1e6  # zero residuals -> enormous t


def test_nw_widens_se_when_score_is_autocorrelated():
    # NW matters when x_i*e_i is autocorrelated — which needs BOTH a
    # persistent regressor and persistent errors (iid x kills it even under
    # AR errors). That's exactly the backtest's situation: dev is persistent
    # and overlapping forward returns are MA(h-1).
    import random

    rng = random.Random(7)
    x, e, xs, ys = 0.0, 0.0, [], []
    for i in range(1000):
        x = 0.9 * x + rng.gauss(0, 1)
        e = 0.9 * e + rng.gauss(0, 1)
        xs.append(x)
        ys.append(0.05 * x + e)
    _, t0 = nw_slope(xs, ys, lag=0)
    _, t11 = nw_slope(xs, ys, lag=11)
    assert abs(t11) < abs(t0)  # the naive t overstates confidence here


def test_deviations_zero_on_constant_series():
    months = [(f"2020-{m:02d}-01", 100.0) for m in range(1, 13)] * 5
    assert all(abs(d) < 1e-12 for d in deviations(months))


def _sine_history(n=480, amp=0.35, period=48):
    """A perfectly mean-reverting world: log price oscillates around flat."""
    months = []
    for i in range(n):
        y, m = 2000 + i // 12, i % 12 + 1
        months.append((f"{y}-{m:02d}-01", 100 * math.exp(amp * math.sin(2 * math.pi * i / period))))
    return months


def test_backtest_detects_mean_reversion_in_synthetic_world(monkeypatch):
    import opencopper.backtest as bt

    class _H:
        months = _sine_history()

    monkeypatch.setattr(bt, "load_price_history", lambda name: _H())
    row = backtest_commodity("synthetic", horizon=12)
    assert row.slope < 0 and row.t_stat < -3
    # glut months (deep below trend) recover; tight months fall
    assert row.mean_fwd["glut"] > 0 > row.mean_fwd["tight"]
    # The deterministic version of the empirical asymmetry: the trailing
    # trend LAGS the cycle, so "tight" months sit mid-ascent and the
    # monthly-gated short loses even though 12m-forward stats mean-revert.
    # The signal is horizon-dependent; the rule's monthly gate is not.
    s = summary([row])
    assert s["ew_short_tight"]["ann_ret"] < 0 < row.mean_fwd["glut"]
    assert s["n_mean_reverting"] == 1
    assert 0 <= s["sign_test_p"] <= 1


def test_backtest_real_copper_runs_and_is_sane():
    row = backtest_commodity("copper", horizon=12)
    if row is None:
        pytest.skip("no copper price cache")
    assert row.n_months > 200
    assert abs(row.t_stat) < 10
    assert set(row.mean_fwd) == {"glut", "balanced", "tight"}


# ---------------------------------------------------------------- regime vol


def test_regime_volatility_buckets_and_min_obs():
    from opencopper.history import regime_volatility

    rv = regime_volatility("copper")
    if rv is None:
        pytest.skip("no copper price cache")
    assert set(rv) <= {"glut", "balanced", "tight"}
    assert all(0.03 < v < 1.5 for v in rv.values())
    assert regime_volatility("copper", min_obs=10**6) is None


# ---------------------------------------------------------------- elasticity bands


def test_impact_range_brackets_point_and_respects_clamp():
    from opencopper.pricing import impact_range, load_pricebook, price_impact_from_shock

    book = load_pricebook()
    cu = book.commodities["copper"]
    pt = price_impact_from_shock(cu, 0.07).price_change_pct
    lo, hi = impact_range(cu, 0.07)
    assert lo <= pt <= hi
    # clamp caps the inelastic end
    lo_c, hi_c = impact_range(book.commodities["cobalt"], 0.40)
    assert hi_c == pytest.approx(300, abs=0.5)
    # no seeded range -> None, not a fake band
    assert impact_range(book.commodities["zinc"], 0.1) is None
    # band widens with shock size
    lo2, hi2 = impact_range(cu, 0.14)
    assert (hi2 - lo2) > (hi - lo)


def test_ripple_direct_row_carries_band():
    from opencopper.linkages import ripple

    rows = ripple("copper", "Chile", 0.3)
    direct = next(r for r in rows if r.channel == "direct")
    assert direct.range_pct is not None
    assert direct.range_pct[0] <= direct.price_change_pct <= direct.range_pct[1]


# ---------------------------------------------------------------- book risk


def test_book_risk_hedged_book_nets_to_zero():
    from opencopper.book import Position, book_risk

    r = book_risk([Position("copper", 100, "long"), Position("copper", -100, "short")])
    if r.window_months == 0:
        pytest.skip("no price cache")
    assert r.sigma_usd == 0 and r.var95_usd == 0


def test_book_risk_excludes_no_history_and_orders_quantiles():
    from opencopper.book import Position, book_risk

    r = book_risk([
        Position("copper", -1200, "cu"),
        Position("crude-oil", -50000, "oil"),
        Position("cobalt", 40, "co"),
    ])
    if r.window_months == 0:
        pytest.skip("no price cache")
    assert r.excluded == ["co"]
    assert 0 < r.var95_usd < r.var99_usd
    assert r.var95_usd < r.es95_usd < r.var99_usd  # ES95 sits between for a normal
    assert r.sigma_usd <= r.undiversified_sigma
    assert abs(sum(row["contribution_pct"] for row in r.rows) - 100) < 1.5
    assert -1 <= r.corr["copper"]["crude-oil"] <= 1
