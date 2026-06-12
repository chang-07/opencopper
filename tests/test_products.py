"""Products layer: BOM integrity, passthrough math, multi-event ripple, and
the new commodity pool's wiring into it."""

import pytest

import opencopper.products as pr
from opencopper.products import (
    all_shock_responses,
    breakdown,
    list_product_names,
    live_pressure,
    load_product,
    scenario_changes,
    shock_response,
)


def test_all_boms_load_and_reference_priced_commodities():
    from opencopper.pricing import load_pricebook

    book = load_pricebook()
    names = list_product_names()
    assert len(names) == 11
    for name in names:
        prod = load_product(name)
        bd = breakdown(prod)
        for inp in prod.inputs:
            if inp.product:
                assert inp.product in names, f"{name}: unknown sub-product {inp.product}"
            else:
                assert inp.commodity in book.commodities, f"{name}: unknown input {inp.commodity}"
        # inputs can never exceed the product's anchor cost
        assert 0 < bd["input_share_pct"] < 100
        assert prod.source and prod.caveats


def test_passthrough_spectrum_cable_vs_bread():
    cable = shock_response(load_product("copper-cable"), {"copper": 10.0})
    bread = shock_response(load_product("bread-1kg"), {"wheat": 10.0})
    assert cable["cost_change_pct"] == pytest.approx(7.9, abs=0.2)  # ~80% share
    assert bread["cost_change_pct"] < 1.0                            # ~5% share
    # linearity: double the input move, double the product move
    cable2 = shock_response(load_product("copper-cable"), {"copper": 20.0})
    assert cable2["cost_change_pct"] == pytest.approx(2 * cable["cost_change_pct"], rel=0.01)


def test_battery_pack_rides_the_cobalt_byproduct_channel():
    from opencopper.linkages import ripple

    rows = ripple("copper", "Congo (Kinshasa)", 0.5)
    changes = {r.commodity: r.price_change_pct for r in rows}
    resp = shock_response(load_product("ev-battery-pack"), changes)
    by = {c["commodity"]: c["product_change_pct"] for c in resp["contributions"]}
    # the cobalt squeeze (byproduct of DRC copper) dominates the pack impact
    assert by["cobalt"] > by["copper"] > 0
    assert resp["cost_change_pct"] > 5


def test_live_pressure_zero_when_prices_sit_at_anchor(monkeypatch):
    monkeypatch.setattr(pr, "_live_price", lambda c: (None, None))
    bd = live_pressure(load_product("steel-hrc"))
    assert bd["pressure_pct"] == 0.0
    assert bd["cost_now_usd"] == load_product("steel-hrc").anchor_cost_usd


def test_all_shock_responses_sorts_and_filters():
    out = all_shock_responses({"copper": 10.0}, min_abs_pct=0.1)
    assert out[0]["product"] == "copper-cable"  # biggest copper exposure first
    assert all(abs(r["cost_change_pct"]) >= 0.1 for r in out)


# ------------------------------------------------------- multi-event ripple


def test_ripple_events_single_event_equals_ripple():
    from opencopper.linkages import ripple, ripple_events

    a = ripple("copper", "Chile", 0.3)
    b = ripple_events("copper", [("Chile", 0.3)])
    assert [(r.commodity, r.price_change_pct) for r in a] == \
           [(r.commodity, r.price_change_pct) for r in b]


def test_ripple_events_aggregates_supply_withdrawal():
    from opencopper.commodities import load_commodity
    from opencopper.linkages import ripple_events

    seed = load_commodity("copper")
    rows = ripple_events("copper", [("Chile", 0.3), ("Peru", 0.3)])
    direct = next(r for r in rows if r.channel == "direct")
    expected_k = 0.3 * seed.share("Chile") + 0.3 * seed.share("Peru")
    assert direct.supply_shock == pytest.approx(expected_k, abs=1e-6)
    # byproduct stays country-gated: no cobalt drag from Chile+Peru
    assert "cobalt" not in {r.commodity for r in rows}


def test_scenario_changes_on_shipped_hormuz_scenario():
    from pathlib import Path

    from opencopper.commodities import load_commodity_scenario

    scenario = load_commodity_scenario(
        Path("scenarios/commodities/hormuz-disruption.yaml"))
    changes = scenario_changes(scenario)
    assert changes["crude-oil"] > 20            # multi-country Gulf withdrawal
    assert changes.get("wheat", 0) > 0          # fuel/fertilizer input-cost link
    # and gasoline feels it through the BOM
    resp = shock_response(load_product("gasoline-us"), changes)
    assert resp["cost_change_pct"] > 10


# ------------------------------------------------------- expanded pool


def test_new_commodities_are_fully_wired():
    from opencopper.commodities import load_commodity
    from opencopper.geo import centroid
    from opencopper.pricing import load_pricebook

    book = load_pricebook()
    for name in ("lead", "platinum", "uranium", "coal", "corn", "soybeans",
                 "graphite", "manganese"):
        seed = load_commodity(name)
        assert name in book.commodities
        for p in seed.top_producers:
            assert centroid(p.country) is not None, f"{name}: no centroid for {p.country}"
    # graphite now outranks cobalt as the most concentrated commodity
    g = load_commodity("graphite").concentration()["top1"]
    c = load_commodity("cobalt").concentration()["top1"]
    assert g > c > 0.7


# ------------------------------------------------------- value-chain layers


def test_recursive_bom_embeds_and_guards_depth():
    from opencopper.products import breakdown, load_product, shock_response

    ev = breakdown(load_product("ev-compact"))
    nested = [r for r in ev["rows"] if r.get("via_product")]
    assert {r["via_product"] for r in nested} == {"ev-battery-pack", "steel-hrc"}
    # embedded commodity terms scale by the sub-product's cost share
    pack = next(r for r in nested if r["via_product"] == "ev-battery-pack")
    li = next(e for e in pack["embedded"] if e["commodity"] == "lithium")
    assert 1.0 < li["share_pct"] < 4.0
    # a DRC copper shock reaches the vehicle mostly through cobalt-in-the-pack
    from opencopper.linkages import ripple

    changes = {r.commodity: r.price_change_pct
               for r in ripple("copper", "Congo (Kinshasa)", 0.5)}
    resp = shock_response(load_product("ev-compact"), changes)
    top = max(resp["contributions"], key=lambda c: c["product_change_pct"])
    assert "cobalt (via ev-battery-pack)" == top["commodity"]
    assert 1.5 < resp["cost_change_pct"] < 5


def test_value_chain_attenuation():
    from opencopper.products import breakdown, load_product

    shares = {n: breakdown(load_product(n))["input_share_pct"]
              for n in ("copper-cable", "ev-compact", "data-center-mw", "smartphone")}
    assert shares["copper-cable"] > shares["ev-compact"] > \
        shares["data-center-mw"] > shares["smartphone"]
    assert shares["smartphone"] < 1.0  # the honesty exhibit


def test_processing_layer_and_its_headline():
    from opencopper.commodities import load_commodity, render_commodity_report, run_commodity

    co = load_commodity("cobalt")
    assert co.processing["top_countries"]["China"] == 0.75
    text = render_commodity_report(co, run_commodity(co))
    assert "MIDSTREAM" in text and "REFINERY" in text
    li = load_commodity("lithium")
    assert "spodumene sails to China" in li.processing["note"]


def test_policy_registry_links_both_directions():
    from opencopper.policy import load_policies, policies_for

    pols = load_policies()
    assert len(pols) >= 10
    assert all(p.get("source") and p.get("magnitude") for p in pols)
    cu = policies_for(commodity="copper")
    assert any(p["id"] == "us-section-232-copper" for p in cu)
    ev = policies_for(product="ev-compact")
    assert any(p["id"] == "drc-cobalt-quota" for p in ev)
    pending = [p for p in pols if p["status"] == "pending-decision"]
    assert all(p.get("decision_due") for p in pending)
