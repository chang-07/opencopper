"""The mine ledger: tracked mines + world-level assumptions.

Tracked mines are modeled individually; the rest of the world is an aggregate
(world mine supply minus the tracked baseline). The disruption allowance is
applied to the aggregate only — tracked mines get explicit events instead.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

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
    base_kt_2024: float
    regions: dict[str, dict[str, float]]  # name -> {share, growth_pct}

    def demand(self, year: int) -> float:
        total = 0.0
        for cfg in self.regions.values():
            regional_2024 = self.base_kt_2024 * cfg["share"]
            total += regional_2024 * (1 + cfg["growth_pct"] / 100) ** (year - 2024)
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
