"""The quarterly 3-region trade-flow layer: where the COMEX-LME arb lives.

The annual engine clears the WORLD; this layer disaggregates it into US /
China / RoW at quarterly resolution and lets the regions trade. The 2025-26
copper story was regional — the COMEX premium blowing out on tariff
anticipation, metal vacuumed into US warehouses, LME draining — and a global
model structurally cannot say anything about it. This one can:

- regional refined supply and demand split by explicit shares;
- regional inventories; a region's PREMIUM rises as its cover falls below
  target (premium_pi);
- metal flows toward the premium with a one-quarter shipping lag, capped by
  origin inventory;
- a tariff is a WEDGE on flows into the US: imports only move once the US
  premium exceeds the wedge, so the premium pins near the tariff rate — which
  is what an arb-with-a-tax does in the real world;
- announced tariffs trigger ANTICIPATION: US buyers pull demand forward in the
  quarters before the effective date and pay it back after (demand-conserving),
  reproducing the observed front-run-then-unwind premium shape of 2025.

World totals are conserved by construction (flows are zero-sum; tested).
Quarterly-within-year supply/demand is flat (no seasonality data — documented).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .balance import RunResult, run
from .ledger import Assumptions, Ledger, load_assumptions, load_ledger
from .shocks import Scenario, Tariff

REGIONS = ("us", "china", "row")
DAYS_PER_QUARTER = 365.25 / 4


@dataclass
class QuarterRow:
    label: str  # e.g. "2026Q3"
    year: int
    quarter: int
    cover_days: dict[str, float]
    premium_pct: dict[str, float]
    inflow_kt: dict[str, float]
    us_minus_row: float  # the COMEX-LME arb proxy


@dataclass
class RegionalRun:
    scenario: str
    rows: list[QuarterRow] = field(default_factory=list)

    def row(self, label: str) -> QuarterRow:
        for r in self.rows:
            if r.label == label:
                return r
        raise KeyError(label)


def _regional_cfg(assumptions: Assumptions) -> dict:
    raw = getattr(assumptions, "regional", None)
    if raw is None:
        raise ValueError("assumptions.yaml is missing the `regional` block")
    return raw


def _demand_shares(assumptions: Assumptions, cfg: dict) -> dict[str, float]:
    shares = {}
    for region, demand_keys in cfg["demand_region_map"].items():
        shares[region] = sum(assumptions.demand.regions[k] for k in demand_keys)
    total = sum(shares.values())
    return {r: s / total for r, s in shares.items()}


def run_regional(
    scenario: Scenario,
    *,
    years: range = range(2024, 2031),
    ledger: Ledger | None = None,
    assumptions: Assumptions | None = None,
    world_run: RunResult | None = None,
) -> RegionalRun:
    ledger = ledger or load_ledger()
    assumptions = assumptions or load_assumptions()
    cfg = _regional_cfg(assumptions)
    world = world_run or run(ledger, assumptions, scenario, years)

    supply_share = cfg["refined_supply_shares"]
    demand_share = _demand_shares(assumptions, cfg)
    pi = cfg["premium_pi"]
    flow_el = cfg["flow_elasticity"]
    lag = int(cfg["shipping_lag_quarters"])
    target = assumptions.refined.inventory_days_baseline

    tariffs = [e for e in scenario.events if isinstance(e, Tariff) and e.rate_pct > 0]
    tariff_start_q = min(((t.start_year - years[0]) * 4 for t in tariffs), default=None)
    tariff_rate = max((t.rate_pct for t in tariffs), default=0.0)
    ant_q = int(cfg["anticipation_quarters"])
    surge = float(cfg["anticipation_surge"])

    by_year = {r.year: r for r in world.rows}
    n_quarters = len(list(years)) * 4

    inventory = {}
    for region in REGIONS:
        demand_q0 = by_year[years[0]].refined_demand_kt / 4 * demand_share[region]
        inventory[region] = target * demand_q0 / DAYS_PER_QUARTER

    in_transit: list[dict[str, float]] = [
        {r: 0.0 for r in REGIONS} for _ in range(n_quarters + lag + 1)
    ]
    result = RegionalRun(scenario=scenario.name)

    for t in range(n_quarters):
        year = years[0] + t // 4
        quarter = t % 4 + 1
        wr = by_year[year]
        world_supply_q = wr.refined_supply_kt / 4
        world_demand_q = wr.refined_demand_kt / 4

        supply = {r: world_supply_q * supply_share[r] for r in REGIONS}
        demand = {r: world_demand_q * demand_share[r] for r in REGIONS}

        # tariff anticipation: pull-forward before the effective date, payback after
        if tariff_start_q is not None and ant_q > 0:
            if tariff_start_q - ant_q <= t < tariff_start_q:
                demand["us"] *= 1 + surge
            elif tariff_start_q <= t < tariff_start_q + ant_q:
                demand["us"] *= 1 - surge

        # BASELINE trade: structural deficits are met by continuous contracted
        # shipments (the US imports ~half its demand every quarter regardless
        # of spot premia). Net exporters fund net importers pro-rata. Without
        # this, a premium-chasing controller oscillates between famine and
        # flood — the first version of this model did exactly that.
        gaps = {r: demand[r] - supply[r] for r in REGIONS}  # >0 needs imports
        need = sum(g for g in gaps.values() if g > 0)
        give = sum(-g for g in gaps.values() if g < 0)
        baseline_in = {
            r: (gaps[r] if gaps[r] > 0 else 0.0) * min(1.0, give / need if need else 0.0)
            for r in REGIONS
        }
        baseline_out = {
            r: (-gaps[r] if gaps[r] < 0 else 0.0) * min(1.0, need / give if give else 0.0)
            for r in REGIONS
        }

        # marginal arrivals decided `lag` quarters ago
        inflow = dict(in_transit[t])

        cover, premium = {}, {}
        active_tariff = tariff_rate if (tariff_start_q is not None and t >= tariff_start_q) else 0.0
        for r in REGIONS:
            inventory[r] = max(
                0.0,
                inventory[r] + supply[r] - demand[r] + baseline_in[r] - baseline_out[r] + inflow[r],
            )
            cover[r] = inventory[r] / (demand[r] / DAYS_PER_QUARTER)
            # premium from cover scarcity, clamped at storage-arb bounds (a
            # glut goes into storage at full carry rather than discounting
            # forever; a squeeze is capped by physical substitution)
            premium[r] = max(-15.0, min(60.0, pi * (target - cover[r]) / target * 100))
        # an active tariff prices the marginal imported ton: the US premium
        # carries the full wedge on top of its scarcity premium (this is what
        # "the arb pins at the tariff" means)
        if active_tariff and baseline_in["us"] > 0:
            premium["us"] += active_tariff

        # MARGINAL flows re-route toward scarcity, lagged one quarter. The
        # wedge nets out of the gap for US-bound metal (the shipper pays it),
        # so marginal flows respond to cover imbalances, not to the tariff
        # transfer itself.
        for origin in REGIONS:
            for dest in REGIONS:
                if origin == dest:
                    continue
                gap = premium[dest] - premium[origin]
                if dest == "us":
                    gap -= active_tariff
                if origin == "us":
                    gap += active_tariff  # leaving the US escapes the wedge premium
                if gap <= 1.0:  # dead band: nobody re-routes a cargo for <1%
                    continue
                shortfall = max(0.0, (target - cover[dest])) * demand[dest] / DAYS_PER_QUARTER
                flow = min(flow_el * (gap / 100) * world_demand_q, shortfall * 0.5,
                           inventory[origin] * 0.25)
                if flow <= 0:
                    continue
                inventory[origin] -= flow
                in_transit[t + lag][dest] += flow

        result.rows.append(
            QuarterRow(
                label=f"{year}Q{quarter}",
                year=year,
                quarter=quarter,
                cover_days={r: round(cover[r], 2) for r in REGIONS},
                premium_pct={r: round(premium[r], 2) for r in REGIONS},
                inflow_kt={r: round(inflow[r], 1) for r in REGIONS},
                us_minus_row=round(premium["us"] - premium["row"], 2),
            )
        )
    return result


def render_regional(rr: RegionalRun, around_year: int | None = None) -> str:
    rows = rr.rows
    if around_year:
        rows = [r for r in rows if around_year - 1 <= r.year <= around_year + 1]
    lines = [
        f"REGIONAL (quarterly) — scenario '{rr.scenario}'",
        "US−RoW premium = the COMEX-LME arb proxy; a tariff pins it near the wedge.",
        "(Late-horizon RoW discounts reflect world surpluses parking in RoW —",
        " regional demand has no price elasticity here; documented simplification.)",
        "",
        f"{'qtr':<8}{'US cover':>9}{'CN cover':>9}{'RoW cover':>10}{'US prem':>9}{'US−RoW':>9}{'US inflow':>10}",
    ]
    for r in rows:
        lines.append(
            f"{r.label:<8}{r.cover_days['us']:>9.1f}{r.cover_days['china']:>9.1f}"
            f"{r.cover_days['row']:>10.1f}{r.premium_pct['us']:>8.1f}%{r.us_minus_row:>8.1f}%"
            f"{r.inflow_kt['us']:>10,.0f}"
        )
    return "\n".join(lines)
