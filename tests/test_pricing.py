"""Pricing layer — cover curve, elasticity-incidence, FRED parsing. Offline."""

import pytest

from opencopper.pricing import (
    copper_price_from_cover,
    fetch_fred,
    load_pricebook,
    price_impact_from_shock,
    summarize,
)


def test_pricebook_loads_all_commodities_with_elasticities():
    book = load_pricebook()
    assert len(book.commodities) == 14
    for name, p in book.commodities.items():
        assert p.anchor_usd > 0
        assert 0 < p.elasticity_supply < 1
        assert 0 < p.elasticity_demand <= 2.0


def test_cover_curve_anchors_and_inverts():
    curve = load_pricebook().copper_cover_curve
    # at baseline cover, price == anchor
    assert copper_price_from_cover(curve.baseline_days, curve) == round(curve.anchor_usd_t)
    # tighter cover -> higher price; looser -> lower
    tight = copper_price_from_cover(curve.baseline_days / 2, curve)
    loose = copper_price_from_cover(curve.baseline_days * 2, curve)
    assert tight > curve.anchor_usd_t > loose
    # clamp holds at extremes
    assert copper_price_from_cover(0.01, curve) <= curve.anchor_usd_t * curve.clamp[1]
    assert copper_price_from_cover(9999, curve) >= curve.anchor_usd_t * curve.clamp[0]


def test_incidence_is_inverse_to_elasticity():
    book = load_pricebook()
    # cobalt (very inelastic) must move far more than aluminum (elastic) for the same cut
    cobalt = price_impact_from_shock(book.commodities["cobalt"], 0.2)
    aluminum = price_impact_from_shock(book.commodities["aluminum"], 0.2)
    assert cobalt.price_change_pct > aluminum.price_change_pct
    # exact CES formula: (1-k)^(-1/(eD+eS)), within clamp
    co = book.commodities["cobalt"]
    expected = 100 * ((1 - 0.2) ** (-1 / (co.elasticity_demand + co.elasticity_supply)) - 1)
    assert cobalt.price_change_pct == pytest.approx(expected, abs=0.2)


def test_incidence_level_applies_to_anchor():
    book = load_pricebook()
    impact = price_impact_from_shock(book.commodities["nickel"], 0.1)
    expected = book.commodities["nickel"].anchor_usd * (1 + impact.price_change_pct / 100)
    assert impact.implied_usd == pytest.approx(expected, rel=0.001)


def test_ces_small_shock_matches_linear_and_stays_physical():
    from opencopper.pricing import INCIDENCE_CLAMP, price_impact_from_demand

    book = load_pricebook()
    nickel = book.commodities["nickel"]
    denom = nickel.elasticity_demand + nickel.elasticity_supply
    # small shock: CES ~= linear tangent
    small = price_impact_from_shock(nickel, 0.02)
    assert small.price_change_pct == pytest.approx(100 * 0.02 / denom, rel=0.06)
    # huge demand collapse: price can never fall below -100% (clamps at -75%)
    crash = price_impact_from_demand(book.commodities["cobalt"], -0.50)
    assert -100 < crash.price_change_pct <= 100 * (INCIDENCE_CLAMP[0] - 1) + 0.1
    assert crash.clamped
    # huge supply cut clamps at the top
    squeeze = price_impact_from_shock(book.commodities["cobalt"], 0.46)
    assert squeeze.clamped and squeeze.price_change_pct == pytest.approx(300, abs=0.5)


def test_ambient_volatility_realized_or_default():
    from opencopper.history import DEFAULT_AMBIENT_VOL, ambient_volatility

    vol, src = ambient_volatility("copper")
    assert 0.15 < vol < 0.35 and "realized" in src
    vol2, src2 = ambient_volatility("cobalt")
    assert vol2 == DEFAULT_AMBIENT_VOL and "default" in src2


def test_prob_price_multiple_sanity():
    from opencopper.pricing import prob_price_multiple

    # no shock, 22% vol: doubling within a year is a tail event
    assert prob_price_multiple(0.0, 0.22, 2.0) < 0.01
    # +80% shock center makes doubling likely-ish; bigger shock -> bigger P
    assert prob_price_multiple(0.8, 0.22, 2.0) > prob_price_multiple(0.3, 0.22, 2.0)
    # crash center: P(halving) is high
    assert prob_price_multiple(-0.5, 0.22, 0.5) > 0.4


def test_fred_parser_skips_headers_and_missing(monkeypatch):
    import httpx

    csv_body = "DATE,PCOPPUSDM\n2026-04-01,12890.6\n2026-05-01,.\n2026-06-01,13483.7\n"

    class _Resp:
        text = csv_body
        def raise_for_status(self): pass

    class _Client:
        def get(self, *a, **k): return _Resp()
        def close(self): pass

    rows = fetch_fred("PCOPPUSDM", client=_Client())
    assert rows == [("2026-04-01", 12890.6), ("2026-06-01", 13483.7)]  # "." dropped
    q = summarize("PCOPPUSDM", rows)
    assert q.latest == 13483.7 and q.latest_date == "2026-06-01"
