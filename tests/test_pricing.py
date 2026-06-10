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
    assert len(book.commodities) == 11
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
    # exact formula: k / (|eD| + eS)
    co = book.commodities["cobalt"]
    assert cobalt.price_change_pct == pytest.approx(
        100 * 0.2 / (co.elasticity_demand + co.elasticity_supply), abs=0.1
    )


def test_incidence_level_applies_to_anchor():
    book = load_pricebook()
    impact = price_impact_from_shock(book.commodities["nickel"], 0.1)
    expected = book.commodities["nickel"].anchor_usd * (1 + impact.price_change_pct / 100)
    assert impact.implied_usd == pytest.approx(expected, rel=0.001)


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
