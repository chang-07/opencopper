"""Shock events and how they perturb the balance model.

Every event type is a small, explicit parameterization — the point of this
project is that assumptions are inspectable, so each event documents its own
simplifications. Events compose: a scenario is a list of events applied to the
same baseline.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


class MineOutage(BaseModel):
    """Lost production at a named mine, with linear recovery.

    severity is the fraction of production lost in [start_year, end_year].
    recovery_years > 0 ramps production back linearly after end_year.
    """

    type: Literal["mine_outage"] = "mine_outage"
    mine: str
    start_year: int
    end_year: int
    severity: float = Field(gt=0, le=1)
    recovery_years: int = Field(default=0, ge=0)

    def multiplier(self, year: int) -> float:
        if self.start_year <= year <= self.end_year:
            return 1 - self.severity
        if self.recovery_years and self.end_year < year <= self.end_year + self.recovery_years:
            step = (year - self.end_year) / (self.recovery_years + 1)
            return 1 - self.severity * (1 - step)
        return 1.0


class MineRestart(BaseModel):
    """A suspended mine returns. ramp maps year -> kt produced that year."""

    type: Literal["mine_restart"] = "mine_restart"
    mine: str
    ramp: dict[int, float]

    def production(self, year: int, capacity_kt: float, utilization: float) -> float:
        if year in self.ramp:
            return self.ramp[year]
        if self.ramp and year > max(self.ramp):
            return capacity_kt * utilization
        return 0.0


class SmelterClosure(BaseModel):
    """Primary smelting capacity removed from a given year onward (kt concentrate
    intake, Cu content). Models the TC/RC-crisis closures."""

    type: Literal["smelter_closure"] = "smelter_closure"
    capacity_kt: float = Field(gt=0)
    start_year: int
    note: str = ""

    def capacity_delta(self, year: int) -> float:
        return -self.capacity_kt if year >= self.start_year else 0.0


class DemandShock(BaseModel):
    """Multiplicative shift in refined demand over a window (e.g. recession,
    substitution, an AI-datacenter buildout surprise)."""

    type: Literal["demand_shock"] = "demand_shock"
    pct: float  # +2.0 means +2% demand
    start_year: int
    end_year: int
    note: str = ""

    def multiplier(self, year: int) -> float:
        if self.start_year <= year <= self.end_year:
            return 1 + self.pct / 100
        return 1.0


class Tariff(BaseModel):
    """An import tariff on refined copper into a region (v1: US only).

    Simplifications (deliberate, documented):
    - Effect on global demand = import_share x demand_elasticity x rate x passthrough.
      The region pays a premium; some demand is destroyed/substituted.
    - No rerouting lag or regional inventory split in v1 — the regional premium
      is reported as an output, not fed back into supply.
    """

    type: Literal["tariff"] = "tariff"
    rate_pct: float = Field(ge=0)
    start_year: int
    region_demand_share: float = Field(default=0.085, gt=0, le=1)  # US ~8.5% of world
    import_dependence: float = Field(default=0.45, ge=0, le=1)  # share of region demand imported
    demand_elasticity: float = Field(default=-0.3)
    passthrough: float = Field(default=0.8, ge=0, le=1)

    def demand_multiplier(self, year: int) -> float:
        if year < self.start_year:
            return 1.0
        regional_price_rise = (self.rate_pct / 100) * self.import_dependence * self.passthrough
        regional_demand_change = self.demand_elasticity * regional_price_rise
        return 1 + self.region_demand_share * regional_demand_change

    def regional_premium_pct(self, year: int) -> float:
        if year < self.start_year:
            return 0.0
        return self.rate_pct * self.import_dependence * self.passthrough


class ExportBlock(BaseModel):
    """A country blocks a share of its exports (mines in `country` lose
    `share` of production for the window — supply is stranded, not destroyed,
    but v1 treats stranded as lost to the world balance)."""

    type: Literal["export_block"] = "export_block"
    country: str
    share: float = Field(gt=0, le=1)
    start_year: int
    end_year: int

    def multiplier(self, year: int) -> float:
        if self.start_year <= year <= self.end_year:
            return 1 - self.share
        return 1.0


Event = Union[MineOutage, MineRestart, SmelterClosure, DemandShock, Tariff, ExportBlock]


class Scenario(BaseModel):
    name: str
    description: str = ""
    events: list[Event] = Field(default_factory=list)
