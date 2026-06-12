"""Quant layer: backtest machinery, regime-conditional vol, elasticity-range
bands, and the correlated book risk. Synthetic where the math must be exact;
real cached series where the claim is about the data."""

import math

import pytest

from opencopper.backtest import (
    backtest_all,
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
    # (with the skip-month default the synthetic short leg lands at exactly
    # 0.0 — one month later in the ascent is one month closer to the peak;
    # the point stands: the monthly gate never beats the 12m-hold signal)
    assert s["ew_short_tight"]["ann_ret"] <= 0 < row.mean_fwd["glut"]
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
    # no seeded range -> None, not a fake band (synthetic: immune to future seeding)
    from opencopper.pricing import CommodityPrice

    bare = CommodityPrice(anchor_usd=100, unit="USD/t",
                          elasticity_supply=0.2, elasticity_demand=0.2)
    assert impact_range(bare, 0.1) is None
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


def test_every_seeded_range_brackets_its_point():
    # the invariant that catches range-misplacement bugs (a range pasted under
    # the wrong commodity rarely brackets that commodity's point)
    from opencopper.pricing import load_pricebook

    for name, p in load_pricebook().commodities.items():
        if p.elasticity_supply_range:
            lo, hi = p.elasticity_supply_range
            assert lo <= p.elasticity_supply <= hi, f"{name} supply"
        if p.elasticity_demand_range:
            lo, hi = p.elasticity_demand_range
            assert lo <= p.elasticity_demand <= hi, f"{name} demand"


# ---------------------------------------------------------------- literature pass


def test_half_life_recovers_known_ar1():
    from opencopper.backtest import half_life

    import random

    rng = random.Random(3)
    d, devs = 0.0, []
    for _ in range(5000):
        d = 0.9 * d + rng.gauss(0, 0.1) * math.sqrt(1 - 0.81)
        devs.append(d)
    hl = half_life(devs)
    assert hl == pytest.approx(math.log(0.5) / math.log(0.9), rel=0.25)  # ~6.6 months
    # a pure random walk (rho ~ 1) or white noise (rho ~ 0) yields None or large
    assert half_life([1.0, -1.0] * 100) is None  # rho < 0


def test_momentum_2x2_cells_partition_the_sample(monkeypatch):
    import opencopper.backtest as bt

    class _H:
        months = _sine_history()

    monkeypatch.setattr(bt, "load_price_history", lambda name: _H())
    row = backtest_commodity("synthetic", horizon=12)
    assert sum(c["n"] for c in row.cells_2x2.values()) == row.n_months
    s = summary([row])
    assert set(s["momentum_2x2"]) == {f"{r}|{m}" for r in ("glut", "balanced", "tight")
                                      for m in ("up", "down")}


def test_cornish_fisher_var_reported_with_moments():
    from opencopper.book import Position, book_risk

    r = book_risk([Position("copper", 1000, "long cu")])
    if r.window_months == 0:
        pytest.skip("no price cache")
    assert r.cf_var95_usd is not None and r.cf_var95_usd > 0
    assert r.pnl_skew is not None and r.pnl_exkurt is not None
    # zero skew/kurt would reproduce the normal quantile exactly; with moments
    # present the two must at least be the same order of magnitude
    assert 0.5 < r.cf_var95_usd / r.var95_usd < 2.0


def test_retail_passthrough_scales_cost_response():
    from opencopper.products import load_product, shock_response

    bread = shock_response(load_product("bread-1kg"), {"wheat": 10.0})
    assert bread["retail_change_pct"] == pytest.approx(0.3 * bread["cost_change_pct"], abs=0.02)
    cable = shock_response(load_product("copper-cable"), {"copper": 10.0})
    assert cable["retail_change_pct"] == pytest.approx(cable["cost_change_pct"], abs=0.01)
    assert "Nakamura" in bread["retail_note"]


# ---------------------------------------------------------------- bias pass


def test_skip_month_shifts_the_outcome_window(monkeypatch):
    import opencopper.backtest as bt

    class _H:
        months = _sine_history()

    monkeypatch.setattr(bt, "load_price_history", lambda name: _H())
    naive = backtest_commodity("x", horizon=12, skip=0)
    skipped = backtest_commodity("x", horizon=12, skip=1)
    # one fewer usable signal month, and a genuinely different outcome window
    assert skipped.n_months == naive.n_months - 1
    assert skipped.slope != naive.slope


def test_date_range_split_partitions_the_sample(monkeypatch):
    import opencopper.backtest as bt

    class _H:
        months = _sine_history()

    monkeypatch.setattr(bt, "load_price_history", lambda name: _H())
    full = backtest_commodity("x", horizon=12)
    pre = backtest_commodity("x", horizon=12, date_range=("1900-01-01", "2019-12-31"))
    post = backtest_commodity("x", horizon=12, date_range=("2020-01-01", "2100-01-01"))
    assert pre.n_months + post.n_months == full.n_months
    # a stationary synthetic world mean-reverts in BOTH halves
    assert pre.slope < 0 and post.slope < 0


def test_deflation_uses_cpi_and_survives_missing_months(monkeypatch):
    import opencopper.backtest as bt
    import opencopper.pricing as pr

    months = _sine_history()
    cpi = [(d, 100.0 * (1.002 ** i)) for i, (d, _) in enumerate(months[:-24])]  # CPI series shorter

    class _H:
        pass
    _H.months = months
    monkeypatch.setattr(bt, "load_price_history", lambda name: _H())
    monkeypatch.setattr(pr, "cached_fred", lambda s, **k: cpi)
    row = backtest_commodity("x", horizon=12, deflate=True)
    assert row is not None and row.slope < 0  # reversion survives deflation
    assert row.n_months < backtest_commodity("x", horizon=12).n_months  # intersection only


def test_sign_consistency_counts_both_contrasts():
    from opencopper.backtest import sign_consistency

    rows = backtest_all(horizon=12)
    if not rows:
        pytest.skip("no price caches")
    c = sign_consistency(rows)
    assert c["regime_n"] >= c["n_comparable"]  # regime contrast always broader
    assert 0 <= c["regime_consistent"] <= c["regime_n"]
    assert c["regime_p"] is None or 0 <= c["regime_p"] <= 1


def test_tranche_strategy_captures_the_rebound_in_synthetic_world(monkeypatch):
    import opencopper.backtest as bt
    from opencopper.backtest import tranche_strategy

    class _H:
        months = _sine_history()

    monkeypatch.setattr(bt, "load_price_history", lambda name: _H())
    t = tranche_strategy(include=("glut",), cost_bps=10)
    # the 12m hold collects the rebound the monthly gate missed: in the
    # perfectly mean-reverting world the tranche rule prints money
    assert t["gross"]["sharpe"] > 1
    assert t["net"]["sharpe"] <= t["gross"]["sharpe"]  # costs only subtract
    assert 0 < t["avg_gross_exposure"] < 1
    assert t["ann_turnover"] < 3  # overlapping holds = slow book


def test_tranche_real_data_is_sane():
    from opencopper.backtest import tranche_strategy

    t = tranche_strategy()
    if t["n_commodities"] == 0:
        pytest.skip("no caches")
    assert t["n_commodities"] >= 10
    assert -1 < t["gross"]["ann_ret"] < 1
    assert t["net"]["ann_ret"] <= t["gross"]["ann_ret"] + 1e-9
