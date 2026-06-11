"""The multi-commodity tier: country-level supply models from USGS MCS data.

Two honest scope limits, by design:

1. This tier is COUNTRY-level (USGS world production + reserves by country),
   not mine-level. Copper alone has the full mine ledger + two-stage engine.
2. Mine supply and consumption sit on different bases for several commodities
   (scrap fills copper's gap; recycling fills silver's), so the generic model
   reports BALANCE DRIFT relative to the anchor year — how shocks and trend
   growth move the market vs where it started — never an absolute surplus or
   deficit. Absolute balances need secondary-supply structure this tier
   doesn't have.

What this tier is genuinely good at: CONCENTRATION. Country shares, HHI, and
what happens when a dominant producer restricts supply — Indonesia in nickel,
the DRC in cobalt, China in rare earths. Those are the live policy questions
of 2025-26, and they are country-level questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

import yaml
from pydantic import BaseModel, Field

COMMODITY_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed" / "commodities"
COMMODITY_SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios" / "commodities"


class Producer(BaseModel):
    country: str
    production_kt: float
    reserves_kt: float | None = None


class WorldSupply(BaseModel):
    production_kt: dict[int, float]
    reserves_kt: float | None = None
    production_growth_pct: float

    @property
    def latest_year(self) -> int:
        return max(self.production_kt)

    def production(self, year: int) -> float:
        if year in self.production_kt:
            return self.production_kt[year]
        base_year = self.latest_year
        return self.production_kt[base_year] * (
            1 + self.production_growth_pct / 100
        ) ** (year - base_year)


class CommodityDemand(BaseModel):
    base_kt: float
    base_year: int
    growth_pct: float
    basis: str

    def demand(self, year: int) -> float:
        return self.base_kt * (1 + self.growth_pct / 100) ** (year - self.base_year)


class CommoditySeed(BaseModel):
    name: str
    unit: str
    source: str
    basis: str
    world: WorldSupply
    demand: CommodityDemand
    top_producers: list[Producer]
    drivers: dict[str, float] = Field(default_factory=dict)  # demand share per global driver
    notes: str = ""
    price_note: str = ""
    caveats: str = ""

    @property
    def driver_exposure(self) -> dict[str, float]:
        return self.drivers

    def model_post_init(self, __context) -> None:
        if self.drivers:
            total = sum(self.drivers.values())
            if abs(total - 1.0) > 0.02:
                raise ValueError(f"{self.name}: driver shares sum to {total:.3f}")

    def share(self, country: str) -> float:
        world = self.world.production_kt[self.world.latest_year]
        for p in self.top_producers:
            if p.country.lower() == country.lower():
                return p.production_kt / world
        raise KeyError(f"{country} not in {self.name} top_producers")

    def concentration(self) -> dict:
        world = self.world.production_kt[self.world.latest_year]
        shares = sorted((p.production_kt / world for p in self.top_producers), reverse=True)
        other = max(0.0, 1 - sum(shares))
        # HHI treating the unlisted remainder as atomized (lower bound)
        hhi = round(10_000 * sum(s * s for s in shares))
        return {
            "top1": round(shares[0], 3) if shares else 0,
            "top3": round(sum(shares[:3]), 3),
            "hhi_lower_bound": hhi,
            "listed_coverage": round(sum(shares), 3),
            "other_share": round(other, 3),
        }


def load_commodity(name: str) -> CommoditySeed:
    path = COMMODITY_SEED_DIR / f"{name}.yaml"
    return CommoditySeed(**yaml.safe_load(path.read_text()))


def list_commodity_names() -> list[str]:
    return sorted(p.stem for p in COMMODITY_SEED_DIR.glob("*.yaml"))


# ---------------------------------------------------------------- shocks


class CountrySupplyShock(BaseModel):
    """A dominant producer restricts supply reaching the world market —
    export ban, quota, war, disaster. severity is the fraction of that
    country's output withheld; the world impact is severity x country share."""

    type: Literal["country_supply_shock"] = "country_supply_shock"
    country: str
    severity: float = Field(gt=0, le=1)
    start_year: int
    end_year: int
    note: str = ""

    def active(self, year: int) -> bool:
        return self.start_year <= year <= self.end_year


class GlobalDemandShock(BaseModel):
    type: Literal["global_demand_shock"] = "global_demand_shock"
    pct: float
    start_year: int
    end_year: int
    note: str = ""

    def multiplier(self, year: int) -> float:
        if self.start_year <= year <= self.end_year:
            return 1 + self.pct / 100
        return 1.0


CommodityEvent = Union[CountrySupplyShock, GlobalDemandShock]


class CommodityScenario(BaseModel):
    name: str
    commodity: str
    description: str = ""
    events: list[CommodityEvent] = Field(default_factory=list)


# ------------------------------------------------------- demand drivers

# A driver shock is SYSTEMIC: it hits every commodity through its exposure
# share, which is how an EV slowdown reaches lithium, cobalt, nickel, copper
# and rare earths at once — cross-commodity correlation through shared demand,
# not through hand-wired pairwise links.


class DriverEvent(BaseModel):
    driver: str
    pct: float  # change in THAT driver's demand (e.g. -25 = batteries fall 25%)
    start_year: int
    end_year: int


class DriverScenario(BaseModel):
    name: str
    type: str = "driver"
    description: str = ""
    events: list[DriverEvent] = Field(default_factory=list)


def load_commodity_scenario(path: Path) -> CommodityScenario | DriverScenario:
    raw = yaml.safe_load(path.read_text())
    if raw.get("type") == "driver":
        return DriverScenario(**raw)
    return CommodityScenario(**raw)


def compile_driver_scenario(ds: DriverScenario, seed: CommoditySeed) -> CommodityScenario:
    """A driver scenario compiles to per-commodity demand shocks: each event
    moves this commodity's demand by (exposure share x driver pct)."""
    events: list[CommodityEvent] = []
    for e in ds.events:
        exposure = seed.drivers.get(e.driver, 0.0)
        if exposure <= 0:
            continue
        events.append(
            GlobalDemandShock(
                pct=exposure * e.pct,
                start_year=e.start_year,
                end_year=e.end_year,
                note=f"{e.driver} {e.pct:+.0f}% x exposure {exposure:.0%}",
            )
        )
    return CommodityScenario(name=ds.name, commodity=seed.name, events=events)


def run_driver_scenario(ds: DriverScenario, years: range = range(2025, 2031)) -> list[dict]:
    """Run a driver scenario across every commodity with exposure. Returns one
    row per affected commodity with peak demand change and the incidence-implied
    price move (negative demand -> price falls)."""
    from .pricing import load_pricebook

    book = load_pricebook()
    rows = []
    for name in list_commodity_names():
        seed = load_commodity(name)
        compiled = compile_driver_scenario(ds, seed)
        if not compiled.events:
            continue
        run = run_commodity(seed, compiled, years)
        # peak combined demand multiplier across years
        peak_mult = 1.0
        for y in years:
            mult = 1.0
            for e in compiled.events:
                mult *= e.multiplier(y)
            if abs(mult - 1) > abs(peak_mult - 1):
                peak_mult = mult
        demand_change = peak_mult - 1.0
        price = book.commodities.get(name)
        price_pct = None
        clamped = False
        if price and not price.excluded_from_shock_pricing:
            from .pricing import price_impact_from_demand

            impact = price_impact_from_demand(price, demand_change)
            price_pct, clamped = impact.price_change_pct, impact.clamped
        rows.append(
            {
                "commodity": name,
                "demand_change_pct": round(100 * demand_change, 1),
                "price_change_pct": price_pct,
                "clamped": clamped,
                "run": run,
            }
        )
    rows.sort(key=lambda r: abs(r["demand_change_pct"]), reverse=True)
    return rows


def render_driver_report(ds: DriverScenario, rows: list[dict]) -> str:
    lines = [
        f"DRIVER SCENARIO — {ds.name}: {ds.description}",
        "",
        f"{'commodity':<13}{'demand Δ':>10}{'price Δ (incidence)':>21}",
        "-" * 44,
    ]
    for r in rows:
        if r["price_change_pct"] is None:
            price = "excluded"
        else:
            bound = "≥" if r["price_change_pct"] > 0 else "≤"
            price = f"{bound}{r['price_change_pct']:+.0f}% (clamped)" if r["clamped"] else f"{r['price_change_pct']:+.0f}%"
        lines.append(f"{r['commodity']:<13}{r['demand_change_pct']:>+9.1f}%{price:>21}")
    lines.append(
        "\nOne shock, many markets: propagation runs through shared demand-driver"
        "\nexposures (data/seed/commodities/*.yaml), not hand-wired pairs."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- model


@dataclass
class CommodityYearRow:
    year: int
    supply_kt: float
    demand_kt: float
    drift_kt: float  # (supply - demand) change vs the anchor year — NOT absolute balance
    supply_lost_kt: float


@dataclass
class CommodityRun:
    commodity: str
    scenario: str
    anchor_year: int
    rows: list[CommodityYearRow] = field(default_factory=list)

    def row(self, year: int) -> CommodityYearRow:
        for r in self.rows:
            if r.year == year:
                return r
        raise KeyError(year)


def run_commodity(
    seed: CommoditySeed,
    scenario: CommodityScenario | None = None,
    years: range = range(2025, 2031),
) -> CommodityRun:
    events = scenario.events if scenario else []
    anchor = years[0]
    structural_gap = seed.world.production(anchor) - seed.demand.demand(anchor)

    run = CommodityRun(
        commodity=seed.name,
        scenario=scenario.name if scenario else "baseline",
        anchor_year=anchor,
    )
    for year in years:
        supply = seed.world.production(year)
        lost = 0.0
        for e in events:
            if isinstance(e, CountrySupplyShock) and e.active(year):
                lost += supply * seed.share(e.country) * e.severity
        supply -= lost

        demand = seed.demand.demand(year)
        for e in events:
            if isinstance(e, GlobalDemandShock):
                demand *= e.multiplier(year)

        drift = (supply - demand) - structural_gap
        run.rows.append(
            CommodityYearRow(
                year=year,
                supply_kt=round(supply, 1),
                demand_kt=round(demand, 1),
                drift_kt=round(drift, 1),
                supply_lost_kt=round(lost, 1),
            )
        )
    return run


def render_commodity_report(seed: CommoditySeed, run: CommodityRun) -> str:
    conc = seed.concentration()
    world_latest = seed.world.production_kt[seed.world.latest_year]
    lines = [
        f"{seed.name.upper()} — {seed.unit}",
        f"source: {seed.source}",
        f"world supply {seed.world.latest_year}: {world_latest:,.0f} kt"
        + (f"   reserves: {seed.world.reserves_kt:,.0f} kt" if seed.world.reserves_kt else ""),
        "",
        f"CONCENTRATION  top1 {conc['top1']:.0%}   top3 {conc['top3']:.0%}   "
        f"HHI≥{conc['hhi_lower_bound']:,}   (listed coverage {conc['listed_coverage']:.0%})",
    ]
    for p in seed.top_producers[:8]:
        share = p.production_kt / world_latest
        bar = "#" * max(1, round(40 * share))
        lines.append(f"  {p.country:<22} {p.production_kt:>9,.1f} kt  {share:>5.1%}  {bar}")
    lines += ["", f"scenario: {run.scenario} (drift vs {run.anchor_year} structural gap)"]
    lines.append(f"{'year':<6}{'supply':>10}{'demand':>10}{'lost':>8}{'drift':>9}")
    for r in run.rows:
        lines.append(
            f"{r.year:<6}{r.supply_kt:>10,.0f}{r.demand_kt:>10,.0f}"
            f"{r.supply_lost_kt:>8,.0f}{r.drift_kt:>+9,.0f}"
        )
    if seed.notes:
        lines += ["", f"notes: {seed.notes}"]
    return "\n".join(lines)
