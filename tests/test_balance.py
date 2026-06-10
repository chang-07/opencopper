"""Engine invariants: the properties that make the model trustworthy."""

import pytest

from opencopper.balance import BASELINE, run
from opencopper.ledger import load_assumptions, load_ledger
from opencopper.shocks import MineOutage, MineRestart, Scenario, SmelterClosure, Tariff

YEARS = range(2024, 2031)


@pytest.fixture(scope="module")
def world():
    return load_ledger(), load_assumptions()


def test_baseline_is_deterministic(world):
    ledger, assumptions = world
    a = run(ledger, assumptions, BASELINE, YEARS)
    b = run(ledger, assumptions, BASELINE, YEARS)
    assert a.rows == b.rows


def test_inventory_conserves_balance(world):
    """Mass conservation: inventory change == cumulative refined balance
    (as long as inventory never floors at zero)."""
    ledger, assumptions = world
    result = run(ledger, assumptions, BASELINE, YEARS)
    if any(r.inventory_kt == 0 for r in result.rows):
        pytest.skip("inventory floored; conservation bounded not exact")
    start_inv = result.rows[0].inventory_kt - result.rows[0].refined_balance_kt
    cumulative = sum(r.refined_balance_kt for r in result.rows)
    assert result.rows[-1].inventory_kt == pytest.approx(start_inv + cumulative, abs=1.0)


def test_outage_never_increases_balance(world):
    """Removing supply can never produce a larger surplus in any year."""
    ledger, assumptions = world
    shocked = Scenario(
        name="outage",
        events=[MineOutage(mine="Escondida", start_year=2026, end_year=2027, severity=0.5)],
    )
    base = run(ledger, assumptions, BASELINE, YEARS)
    hit = run(ledger, assumptions, shocked, YEARS)
    for b, h in zip(base.rows, hit.rows):
        assert h.refined_balance_kt <= b.refined_balance_kt + 1e-6


def test_outage_magnitude_is_sane(world):
    """A 50% Escondida outage in 2026 should remove roughly half its
    production from that year's mine supply."""
    ledger, assumptions = world
    mine = ledger.get("Escondida")
    expected_loss = 0.5 * mine.production(2026, assumptions.world.tracked_utilization)
    shocked = Scenario(
        name="outage",
        events=[MineOutage(mine="Escondida", start_year=2026, end_year=2026, severity=0.5)],
    )
    base = run(ledger, assumptions, BASELINE, YEARS)
    hit = run(ledger, assumptions, shocked, YEARS)
    loss = base.row(2026).mine_supply_kt - hit.row(2026).mine_supply_kt
    assert loss == pytest.approx(expected_loss, rel=0.01)


def test_restart_adds_supply_only_after_start(world):
    ledger, assumptions = world
    restart = Scenario(
        name="restart",
        events=[MineRestart(mine="Cobre Panama", ramp={2026: 120, 2027: 280})],
    )
    base = run(ledger, assumptions, BASELINE, YEARS)
    up = run(ledger, assumptions, restart, YEARS)
    assert up.row(2025).mine_supply_kt == pytest.approx(base.row(2025).mine_supply_kt)
    assert up.row(2026).mine_supply_kt - base.row(2026).mine_supply_kt == pytest.approx(120, abs=0.5)
    assert up.row(2027).mine_supply_kt - base.row(2027).mine_supply_kt == pytest.approx(280, abs=0.5)
    # post-ramp: runs at capacity x utilization
    full = ledger.get("Cobre Panama").capacity_kt * assumptions.world.tracked_utilization
    assert up.row(2028).mine_supply_kt - base.row(2028).mine_supply_kt == pytest.approx(full, abs=0.5)


def test_smelter_closure_relieves_tc_and_only_binds_when_large(world):
    """Closure economics: shutting smelters REDUCES concentrate demand, so
    treatment-charge pressure eases. Refined output only falls once capacity
    (not concentrate) becomes the binding constraint."""
    ledger, assumptions = world
    base = run(ledger, assumptions, BASELINE, YEARS)

    closure = Scenario(
        name="closure", events=[SmelterClosure(capacity_kt=800, start_year=2026)]
    )
    hit = run(ledger, assumptions, closure, YEARS)
    assert hit.row(2026).tc_pressure <= base.row(2026).tc_pressure
    assert hit.row(2026).refined_supply_kt <= base.row(2026).refined_supply_kt

    massive = Scenario(
        name="massive", events=[SmelterClosure(capacity_kt=4000, start_year=2026)]
    )
    crushed = run(ledger, assumptions, massive, YEARS)
    assert crushed.row(2026).refined_supply_kt < base.row(2026).refined_supply_kt


def test_zero_tariff_is_identity(world):
    ledger, assumptions = world
    null_tariff = Scenario(name="null", events=[Tariff(rate_pct=0, start_year=2026)])
    base = run(ledger, assumptions, BASELINE, YEARS)
    same = run(ledger, assumptions, null_tariff, YEARS)
    assert [r.refined_balance_kt for r in base.rows] == [r.refined_balance_kt for r in same.rows]


def test_tariff_reduces_demand_and_creates_premium(world):
    ledger, assumptions = world
    tariff = Scenario(name="t", events=[Tariff(rate_pct=25, start_year=2026)])
    base = run(ledger, assumptions, BASELINE, YEARS)
    hit = run(ledger, assumptions, tariff, YEARS)
    assert hit.row(2026).refined_demand_kt < base.row(2026).refined_demand_kt
    assert hit.row(2026).us_premium_pct > 0
    assert hit.row(2025).us_premium_pct == 0


def test_smelter_constraint_binds(world):
    """Smelted output can never exceed capacity x max utilization."""
    ledger, assumptions = world
    result = run(ledger, assumptions, BASELINE, YEARS)
    for r in result.rows:
        cap = r.smelter_capacity_kt * assumptions.smelting.utilization_max
        assert r.smelted_kt <= cap + 1e-6
