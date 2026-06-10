"""The supply/demand balance engine.

Two coupled balances per year, because the market clears in two stages and the
2025-26 squeeze lives in the first one:

1. CONCENTRATE: mine supply (ex-SX-EW) vs smelter intake capacity.
   When concentrate < smelter appetite, treatment charges collapse (the
   $0/negative TC crisis) — reported as `tc_pressure`.
2. REFINED: smelted output + SX-EW + scrap vs regional demand.
   The surplus/deficit flows into inventory; inventory cover drives
   `price_pressure` (an index, deliberately NOT a price forecast).

All inputs come from explicit assumptions (data/seed/assumptions.yaml) and the
mine ledger; shocks perturb them. No hidden constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import Assumptions, Ledger
from .schema import MineStatus
from .shocks import (
    DemandShock,
    Event,
    ExportBlock,
    MineOutage,
    MineRestart,
    Scenario,
    SmelterClosure,
    Tariff,
)


@dataclass
class YearRow:
    year: int
    mine_supply_kt: float
    concentrate_supply_kt: float
    smelter_capacity_kt: float
    smelted_kt: float
    concentrate_balance_kt: float
    tc_pressure: float  # >0 = concentrate tight, TCs falling
    refined_supply_kt: float
    refined_demand_kt: float
    refined_balance_kt: float
    inventory_kt: float
    inventory_days: float
    price_pressure: float  # >0 = bullish vs baseline cover
    us_premium_pct: float = 0.0


@dataclass
class RunResult:
    scenario: str
    rows: list[YearRow] = field(default_factory=list)

    def row(self, year: int) -> YearRow:
        for r in self.rows:
            if r.year == year:
                return r
        raise KeyError(year)


def _mine_production(
    mine_name: str,
    ledger: Ledger,
    assumptions: Assumptions,
    events: list[Event],
    year: int,
) -> float:
    mine = ledger.get(mine_name)
    restarts = [e for e in events if isinstance(e, MineRestart) and e.mine.lower() == mine_name.lower()]
    if mine.status in (MineStatus.SUSPENDED, MineStatus.CLOSED):
        if restarts:
            return restarts[0].production(
                year, mine.capacity_kt, assumptions.world.tracked_utilization
            )
        return mine.production(year)

    base = mine.production(year, assumptions.world.tracked_utilization)
    for e in events:
        if isinstance(e, MineOutage) and e.mine.lower() == mine_name.lower():
            base *= e.multiplier(year)
        elif isinstance(e, ExportBlock) and mine.country.lower() == e.country.lower():
            base *= e.multiplier(year)
    return base


def run(
    ledger: Ledger,
    assumptions: Assumptions,
    scenario: Scenario,
    years: range = range(2024, 2031),
) -> RunResult:
    result = RunResult(scenario=scenario.name)
    events = scenario.events

    start_year = years[0]
    inventory = (
        assumptions.refined.inventory_days_baseline
        * assumptions.demand.demand(start_year)
        / 365
    )

    for year in years:
        # --- supply: tracked mines + rest-of-world aggregate
        tracked_actual = 0.0
        tracked_baseline = 0.0
        concentrate_tracked = 0.0
        for mine in ledger.mines:
            actual = _mine_production(mine.name, ledger, assumptions, events, year)
            baseline = mine.production(year, assumptions.world.tracked_utilization)
            tracked_actual += actual
            tracked_baseline += baseline
            concentrate_tracked += actual * (1 - mine.sxew_share)

        row_supply = max(
            0.0, assumptions.world.mine_supply(year) - tracked_baseline
        ) * (1 - assumptions.world.disruption_allowance_pct / 100)
        for e in events:
            if isinstance(e, ExportBlock):
                # RoW share of the blocked country is not modeled; tracked mines
                # in that country were already hit in _mine_production.
                pass

        mine_supply = tracked_actual + row_supply
        concentrate_supply = concentrate_tracked + row_supply * (
            1 - assumptions.world.sxew_share_world
        )
        sxew = mine_supply - concentrate_supply

        # --- smelting constraint
        smelter_capacity = assumptions.smelting.capacity(year)
        for e in events:
            if isinstance(e, SmelterClosure):
                smelter_capacity += e.capacity_delta(year)
        smelter_intake_max = smelter_capacity * assumptions.smelting.utilization_max
        smelted = min(concentrate_supply, smelter_intake_max)
        concentrate_balance = concentrate_supply - smelter_intake_max
        tc_pressure = -concentrate_balance / concentrate_supply if concentrate_supply else 0.0

        # --- refined balance
        refined_supply = smelted + sxew + assumptions.refined.secondary(year)
        sector_multipliers: dict[str, float] = {}
        for e in events:
            if isinstance(e, DemandShock) and e.sector:
                sector_multipliers[e.sector] = (
                    sector_multipliers.get(e.sector, 1.0) * e.multiplier(year)
                )
        demand = assumptions.demand.demand(year, sector_multipliers)
        us_premium = 0.0
        for e in events:
            if isinstance(e, DemandShock) and not e.sector:
                demand *= e.multiplier(year)
            elif isinstance(e, Tariff):
                demand *= e.demand_multiplier(year)
                us_premium = max(us_premium, e.regional_premium_pct(year))

        balance = refined_supply - demand
        inventory = max(0.0, inventory + balance)
        inventory_days = inventory / (demand / 365)
        price_pressure = (
            assumptions.refined.inventory_days_baseline - inventory_days
        ) / assumptions.refined.inventory_days_baseline

        result.rows.append(
            YearRow(
                year=year,
                mine_supply_kt=round(mine_supply, 1),
                concentrate_supply_kt=round(concentrate_supply, 1),
                smelter_capacity_kt=round(smelter_capacity, 1),
                smelted_kt=round(smelted, 1),
                concentrate_balance_kt=round(concentrate_balance, 1),
                tc_pressure=round(tc_pressure, 4),
                refined_supply_kt=round(refined_supply, 1),
                refined_demand_kt=round(demand, 1),
                refined_balance_kt=round(balance, 1),
                inventory_kt=round(inventory, 1),
                inventory_days=round(inventory_days, 2),
                price_pressure=round(price_pressure, 4),
                us_premium_pct=round(us_premium, 2),
            )
        )

    return result


BASELINE = Scenario(name="baseline", description="No events: the counterfactual world.")
