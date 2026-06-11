"""Core data models for the opencopper ledger and extraction pipeline.

Two families of models live here:

- ``MineRecord``: a row in the supply ledger. Seeded by hand today
  (``basis="seed-estimate"``), progressively replaced by values extracted
  from technical report summaries (``basis="extracted"``) and verified
  against company guidance (``basis="verified"``).
- ``ExtractedMineData``: the structured output of LLM extraction over an
  S-K 1300 Technical Report Summary, where every populated field must
  carry a citation back to the source document.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MineStatus(str, Enum):
    OPERATING = "operating"
    RAMPING = "ramping"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class Basis(str, Enum):
    SEED_ESTIMATE = "seed-estimate"
    EXTRACTED = "extracted"
    VERIFIED = "verified"


class MineRecord(BaseModel):
    """One mine (or tightly-coupled complex) in the supply ledger.

    Quantities are kt of contained copper per year unless noted.
    """

    name: str
    country: str
    owner: str
    status: MineStatus = MineStatus.OPERATING
    capacity_kt: float = Field(gt=0, description="Nameplate annual mine production, kt Cu")
    production_kt: dict[int, float] = Field(
        default_factory=dict, description="Actual/estimated production by year, kt Cu"
    )
    sxew_share: float = Field(
        default=0.0, ge=0, le=1,
        description="Share of output produced as SX-EW cathode (bypasses smelters)",
    )
    basis: Basis = Basis.SEED_ESTIMATE
    sources: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    lat: Optional[float] = Field(default=None, description="approximate, for map display only")
    lon: Optional[float] = Field(default=None, description="approximate, for map display only")
    # Depletion: when reserves are known, the engine retires the mine once
    # cumulative production exhausts what remained as of `reserves_as_of`.
    reserves_kt: Optional[float] = Field(default=None, description="contained Cu reserve")
    reserves_as_of: Optional[int] = Field(default=None, description="effective year of the reserve statement")

    def remaining_reserves(self, at_year: int) -> Optional[float]:
        """Reserve remaining at the START of `at_year`, approximating the gap
        years since the statement at capacity x a 0.93 utilization."""
        if self.reserves_kt is None or self.reserves_as_of is None:
            return None
        elapsed = max(0, at_year - self.reserves_as_of)
        consumed = elapsed * self.capacity_kt * 0.93
        return max(0.0, self.reserves_kt - consumed)

    def production(self, year: int, utilization: float = 1.0) -> float:
        """Baseline production for a year before shocks are applied."""
        if year in self.production_kt:
            return self.production_kt[year]
        if self.status in (MineStatus.SUSPENDED, MineStatus.CLOSED):
            return 0.0
        return self.capacity_kt * utilization


class Citation(BaseModel):
    """A verbatim anchor back into the source document."""

    quote: str = Field(description="Short verbatim quote supporting the value")
    section: Optional[str] = Field(default=None, description="Section heading or page marker")


class ExtractedField(BaseModel):
    value: float
    unit: str
    year: Optional[int] = None
    citation: Citation
    confidence: float = Field(ge=0, le=1)


class ExtractedMineData(BaseModel):
    """Structured extraction from one S-K 1300 Technical Report Summary."""

    mine_name: str
    country: Optional[str] = None
    operator: Optional[str] = None
    commodities: list[str] = Field(default_factory=list)
    annual_production_kt: Optional[ExtractedField] = None
    reserves_kt: Optional[ExtractedField] = None
    mine_life_years: Optional[ExtractedField] = None
    cash_cost_usd_lb: Optional[ExtractedField] = None
    status: Optional[MineStatus] = None
    source_accession: Optional[str] = None
    source_filename: Optional[str] = None

    def is_copper_primary(self) -> bool:
        return bool(self.commodities) and self.commodities[0].lower() == "copper"
