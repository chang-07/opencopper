"""The mine ledger: tracked mines + world-level assumptions.

Tracked mines are modeled individually; the rest of the world is an aggregate
(world mine supply minus the tracked baseline). The disruption allowance is
applied to the aggregate only — tracked mines get explicit events instead.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from .schema import MineRecord

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"


class WorldAssumptions(BaseModel):
    mine_supply_kt_2024: float
    mine_supply_growth_pct: float
    disruption_allowance_pct: float
    sxew_share_world: float
    tracked_utilization: float

    def mine_supply(self, year: int) -> float:
        return self.mine_supply_kt_2024 * (1 + self.mine_supply_growth_pct / 100) ** (year - 2024)


class SmeltingAssumptions(BaseModel):
    capacity_kt: dict[int, float]  # primary smelter intake capacity, Cu content
    capacity_growth_pct: float
    utilization_max: float

    def capacity(self, year: int) -> float:
        if year in self.capacity_kt:
            return self.capacity_kt[year]
        last_year = max(self.capacity_kt)
        base = self.capacity_kt[last_year]
        return base * (1 + self.capacity_growth_pct / 100) ** (year - last_year)


class DemandAssumptions(BaseModel):
    """Demand = sector composition (what copper is used FOR) x trade regions
    (WHERE it lands). Growth comes from sectors — construction stagnates while
    grid/EV/datacenters compound — so the mix shift is explicit instead of
    hidden in a blended regional growth rate. Regions carry shares only and
    exist for trade-geometry shocks (tariffs)."""

    base_kt_2024: float
    sectors: dict[str, dict[str, float]]  # name -> {share, growth_pct}
    regions: dict[str, float]  # name -> share of consumption

    @model_validator(mode="after")
    def _shares_sum_to_one(self):
        for label, shares in (
            ("sectors", [s["share"] for s in self.sectors.values()]),
            ("regions", list(self.regions.values())),
        ):
            total = sum(shares)
            if abs(total - 1.0) > 0.01:
                raise ValueError(f"{label} shares sum to {total:.3f}, expected 1.0")
        return self

    def demand(self, year: int, sector_multipliers: dict[str, float] | None = None) -> float:
        """World refined demand for a year; optional per-sector multipliers let
        shock events hit one end-use slice (e.g. a datacenter boom)."""
        multipliers = sector_multipliers or {}
        total = 0.0
        for name, cfg in self.sectors.items():
            slice_2024 = self.base_kt_2024 * cfg["share"]
            grown = slice_2024 * (1 + cfg["growth_pct"] / 100) ** (year - 2024)
            total += grown * multipliers.get(name, 1.0)
        return total


class RefinedAssumptions(BaseModel):
    secondary_supply_kt_2024: float
    secondary_growth_pct: float
    inventory_days_baseline: float

    def secondary(self, year: int) -> float:
        return self.secondary_supply_kt_2024 * (1 + self.secondary_growth_pct / 100) ** (year - 2024)


class Assumptions(BaseModel):
    world: WorldAssumptions
    smelting: SmeltingAssumptions
    demand: DemandAssumptions
    refined: RefinedAssumptions
    regional: dict | None = None  # quarterly trade-flow layer config (regional.py)


class Ledger(BaseModel):
    mines: list[MineRecord]

    def get(self, name: str) -> MineRecord:
        for m in self.mines:
            if m.name.lower() == name.lower():
                return m
        raise KeyError(f"mine not in ledger: {name}")

    def in_country(self, country: str) -> list[MineRecord]:
        return [m for m in self.mines if m.country.lower() == country.lower()]


def load_ledger(path: Path | None = None) -> Ledger:
    path = path or DATA_DIR / "mines.yaml"
    raw = yaml.safe_load(path.read_text())
    return Ledger(mines=[MineRecord(**m) for m in raw["mines"]])


def load_assumptions(path: Path | None = None) -> Assumptions:
    path = path or DATA_DIR / "assumptions.yaml"
    return Assumptions(**yaml.safe_load(path.read_text()))
