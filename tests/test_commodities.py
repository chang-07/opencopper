"""Multi-commodity tier: seed integrity, concentration math, drift model,
and every shipped commodity scenario."""

import pytest

from opencopper.commodities import (
    COMMODITY_SCENARIO_DIR,
    CommodityScenario,
    CountrySupplyShock,
    list_commodity_names,
    load_commodity,
    load_commodity_scenario,
    run_commodity,
)

ALL = list_commodity_names()


def test_all_seeds_load_with_sources():
    assert len(ALL) == 14
    for name in ALL:
        seed = load_commodity(name)
        assert any(k in seed.source for k in ("usgs.gov", "EIA", "USDA"))
        assert seed.world.production_kt
        assert seed.top_producers
        # listed producers can never exceed the world total
        world = seed.world.production_kt[seed.world.latest_year]
        assert sum(p.production_kt for p in seed.top_producers) <= world * 1.02


def test_concentration_ordering_matches_geology():
    hhi = {name: load_commodity(name).concentration()["hhi_lower_bound"] for name in ALL}
    # the famous chokepoints rank above the diversified metals
    assert hhi["cobalt"] > hhi["nickel"] > hhi["aluminum"] > hhi["copper"] > hhi["gold"]
    cobalt = load_commodity("cobalt")
    assert cobalt.concentration()["top1"] == pytest.approx(0.742, abs=0.01)


def test_baseline_drift_is_zero_at_anchor():
    for name in ("nickel", "copper", "silver"):
        run = run_commodity(load_commodity(name))
        assert run.rows[0].drift_kt == 0.0
        assert all(r.supply_lost_kt == 0 for r in run.rows)


def test_country_shock_math():
    seed = load_commodity("nickel")
    scenario = CommodityScenario(
        name="x",
        commodity="nickel",
        events=[
            CountrySupplyShock(country="Indonesia", severity=0.5, start_year=2026, end_year=2026)
        ],
    )
    run = run_commodity(seed, scenario)
    supply_2026_baseline = seed.world.production(2026)
    expected_lost = supply_2026_baseline * seed.share("Indonesia") * 0.5
    assert run.row(2026).supply_lost_kt == pytest.approx(expected_lost, rel=0.01)
    assert run.row(2027).supply_lost_kt == 0
    # Indonesia at 50% severity removes ~33% of world supply
    assert run.row(2026).supply_lost_kt / supply_2026_baseline == pytest.approx(0.333, abs=0.01)


@pytest.mark.parametrize("path", sorted(COMMODITY_SCENARIO_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_shipped_commodity_scenarios_load_and_bite(path):
    from opencopper.commodities import DriverScenario, run_driver_scenario

    scenario = load_commodity_scenario(path)
    if isinstance(scenario, DriverScenario):
        rows = run_driver_scenario(scenario)
        assert len(rows) >= 3, "a systemic driver shock must touch several commodities"
        assert all(r["demand_change_pct"] != 0 for r in rows)
        # shipped driver scenarios are demand contractions -> prices fall, never below -100%
        priced = [r for r in rows if r["price_change_pct"] is not None]
        assert all(-100 < r["price_change_pct"] < 0 for r in priced)
        return
    seed = load_commodity(scenario.commodity)
    run = run_commodity(seed, scenario)
    shock_years = [r for r in run.rows if r.supply_lost_kt > 0]
    assert shock_years, "scenario must actually remove supply"
    assert all(r.drift_kt < 0 for r in shock_years)


def test_driver_scenario_compiles_through_exposures():
    from opencopper.commodities import DriverEvent, DriverScenario, compile_driver_scenario

    ds = DriverScenario(
        name="x", events=[DriverEvent(driver="batteries", pct=-25, start_year=2026, end_year=2026)]
    )
    lithium = compile_driver_scenario(ds, load_commodity("lithium"))
    assert len(lithium.events) == 1
    assert lithium.events[0].pct == pytest.approx(-25 * 0.88)  # exposure-weighted
    # a commodity with no battery exposure compiles to nothing
    iron = compile_driver_scenario(ds, load_commodity("iron-ore"))
    assert iron.events == []


def test_hindcast_produces_bracketing_columns():
    from opencopper.calibrate import hindcast_copper

    rows = hindcast_copper()
    if not rows:
        pytest.skip("no FRED cache")
    for r in rows:
        assert r["realized"] > 0
        assert r["scenario_implied"] >= r["baseline_implied"] - 1  # events never loosen the market
        assert isinstance(r["bracketed"], bool)
