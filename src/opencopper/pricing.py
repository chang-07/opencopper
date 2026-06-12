"""The pricing layer: implied prices from the supply/demand mechanics.

Not a forecast — a transparent map from scarcity to price, with two mechanisms
matched to what each tier can support:

1. COPPER — inventory-cover scarcity curve. Copper has stock dynamics, so the
   engine's inventory cover (days of consumption) is the right state variable:
   a persistent deficit drains inventory and *that* moves price.
       implied = anchor x clamp((baseline_days / cover)^gamma, lo, hi)

2. COUNTRY TIER — elasticity-incidence (textbook short-run partial equilibrium).
   A supply withdrawal of fraction k moves price by
       %ΔP = k / (|elasticity_demand| + elasticity_supply)
   Inelastic metals (cobalt, REE) therefore spike violently from small cuts —
   which is the actual 2025-26 story, not a modelling artifact.

Live market prices come from FRED's keyless CSV endpoint (IMF global price
series) where one exists; everything else carries a USGS-anchored normal price.
All constants live in data/seed/prices.yaml and are disputable by design.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import yaml
from pydantic import BaseModel

PRICES_SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "prices.yaml"
PRICE_CACHE_DIR = Path("data/prices")
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class CommodityPrice(BaseModel):
    futures: dict | None = None  # exchange/symbol/contract — desk reference, never execution
    anchor_usd: float
    unit: str
    fred_series: Optional[str] = None
    elasticity_supply: float
    elasticity_demand: float
    # literature/judgment RANGES around the point elasticities; when present,
    # incidence outputs carry an uncertainty band instead of a bare point
    elasticity_supply_range: Optional[tuple[float, float]] = None
    elasticity_demand_range: Optional[tuple[float, float]] = None
    # per-commodity history controls: truncate a series at the date its
    # price discovery actually began (iron-ore's annual-benchmark era is a
    # step function, not a market), and mark index-basis series whose level
    # cannot be compared to the USD anchor (gold's BLS export index)
    series_start: Optional[str] = None
    series_is_index: bool = False
    # seeded ambient vol for no-series commodities — replaces the blanket
    # default with a per-commodity number and its basis in the comment
    ambient_vol: Optional[float] = None
    excluded_from_shock_pricing: bool = False
    note: str = ""


class CoverCurve(BaseModel):
    anchor_usd_t: float
    baseline_days: float
    gamma: float
    clamp: tuple[float, float]


class PriceBook(BaseModel):
    copper_cover_curve: CoverCurve
    commodities: dict[str, CommodityPrice]


def load_pricebook(path: Path = PRICES_SEED) -> PriceBook:
    return PriceBook(**yaml.safe_load(path.read_text()))


# ---------------------------------------------------------------- live (FRED)


@dataclass
class FredQuote:
    series: str
    latest_date: str
    latest: float
    avg_12m: float
    low: float
    high: float


def fetch_fred(series: str, client: Optional[httpx.Client] = None) -> list[tuple[str, float]]:
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.get(FRED_CSV, params={"id": series})
        resp.raise_for_status()
        rows = []
        for row in csv.reader(resp.text.splitlines()):
            if len(row) != 2 or row[0] in ("DATE", "observation_date"):
                continue
            try:
                rows.append((row[0], float(row[1])))
            except ValueError:
                continue  # FRED writes "." for missing months
        return rows
    finally:
        if own:
            client.close()


FRED_TTL_DAYS = 3  # monthly series; a few days of cache is plenty


def _read_price_csv(cache: Path) -> list[tuple[str, float]]:
    rows = []
    for row in csv.reader(cache.read_text().splitlines()):
        if len(row) == 2:
            rows.append((row[0], float(row[1])))
    return rows


def cache_age_days(cache: Path) -> float | None:
    if not cache.exists():
        return None
    import time

    return (time.time() - cache.stat().st_mtime) / 86400


def cached_fred(series: str, cache_dir: Path = PRICE_CACHE_DIR,
                ttl_days: float = FRED_TTL_DAYS) -> list[tuple[str, float]]:
    """TTL cache: serve fresh files, refetch stale ones, and fall back to the
    stale file when the refetch fails — a quote that is days old beats no
    quote, as long as its date travels with it (summarize() carries it)."""
    cache = cache_dir / f"{series}.csv"
    age = cache_age_days(cache)
    if age is not None and age <= ttl_days:
        return _read_price_csv(cache)
    try:
        rows = fetch_fred(series)
    except Exception:
        if age is not None:
            return _read_price_csv(cache)  # stale beats nothing
        raise
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(f"{d},{v}" for d, v in rows))
    return rows


def summarize(series: str, rows: list[tuple[str, float]]) -> Optional[FredQuote]:
    if not rows:
        return None
    last_24 = rows[-24:]
    last_12 = [v for _, v in rows[-12:]]
    window = [v for _, v in last_24]
    return FredQuote(
        series=series,
        latest_date=rows[-1][0],
        latest=round(rows[-1][1], 2),
        avg_12m=round(sum(last_12) / len(last_12), 2),
        low=round(min(window), 2),
        high=round(max(window), 2),
    )


# ---------------------------------------------------------------- mechanisms


def copper_price_from_cover(cover_days: float, curve: CoverCurve) -> float:
    """Implied copper price from inventory cover via the scarcity curve."""
    cover = max(cover_days, 0.5)  # avoid blowups at near-zero cover
    ratio = (curve.baseline_days / cover) ** curve.gamma
    lo, hi = curve.clamp
    return round(curve.anchor_usd_t * min(max(ratio, lo), hi))


def prob_price_multiple(shock_change: float, vol: float, multiple: float) -> float:
    """P(price exceeds `multiple` x anchor within a year), treating the price as
    lognormal centered on the shock-implied level with sigma = ambient annual
    vol. Closed form, no simulation:  z = (ln m - ln(1+shock)) / vol.
    For multiple < 1 this is P(price falls BELOW that multiple)."""
    import math

    center = max(1e-6, 1.0 + shock_change)
    z = (math.log(multiple) - math.log(center)) / max(vol, 1e-6)
    phi = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return round(1 - phi, 3) if multiple >= 1 else round(phi, 3)


# Implied-price clamp: beyond these multiples the constant-elasticity
# assumption has certainly broken (substitution, rationing, stockpile release),
# so the model reports the bound rather than extrapolating into air.
INCIDENCE_CLAMP = (0.25, 4.0)


@dataclass
class PriceImpact:
    supply_loss_pct: float
    price_change_pct: float
    anchor_usd: float
    implied_usd: float
    unit: str
    clamped: bool = False


def _ces_multiple(quantity_factor: float, denom: float, invert: bool) -> tuple[float, bool]:
    """Exact constant-elasticity equilibrium price multiple for a quantity
    shift by `quantity_factor` (e.g. 0.63 = 37% withdrawn). The first-order
    linear form (Δq/denom) is this multiple's tangent at zero — we use the
    exact form so large shocks stay physical (a price cannot fall 113%)."""
    import math

    if denom <= 0:
        return 1.0, False
    factor = max(1e-6, quantity_factor)
    exponent = (-1.0 if invert else 1.0) / denom
    multiple = math.exp(exponent * math.log(factor))
    lo, hi = INCIDENCE_CLAMP
    clamped = multiple < lo or multiple > hi
    return min(max(multiple, lo), hi), clamped


def price_impact_from_shock(price: CommodityPrice, supply_loss_fraction: float) -> PriceImpact:
    """Supply withdrawal of fraction k -> price multiple (1-k)^(-1/(η_d+η_s)),
    clamped. Linearizes to k/(η_d+η_s) for small k."""
    denom = abs(price.elasticity_demand) + price.elasticity_supply
    multiple, clamped = _ces_multiple(1.0 - supply_loss_fraction, denom, invert=True)
    return PriceImpact(
        supply_loss_pct=round(100 * supply_loss_fraction, 1),
        price_change_pct=round(100 * (multiple - 1), 1),
        anchor_usd=price.anchor_usd,
        implied_usd=round(price.anchor_usd * multiple, 2),
        unit=price.unit,
        clamped=clamped,
    )


def impact_range(price: CommodityPrice, supply_loss_fraction: float) -> Optional[tuple[float, float]]:
    """Price-change band (lo_pct, hi_pct) from the elasticity RANGES, when
    seeded. The CES multiple is monotone decreasing in η_d+η_s, so the band
    endpoints are exactly the denominator extremes — no sampling needed. A
    point elasticity without a range returns None: the absence of a band is
    itself information (nobody has bounded that parameter yet)."""
    if not (price.elasticity_supply_range and price.elasticity_demand_range):
        return None
    hi_denom = price.elasticity_demand_range[1] + price.elasticity_supply_range[1]
    lo_denom = price.elasticity_demand_range[0] + price.elasticity_supply_range[0]
    soft, _ = _ces_multiple(1.0 - supply_loss_fraction, hi_denom, invert=True)
    hard, _ = _ces_multiple(1.0 - supply_loss_fraction, lo_denom, invert=True)
    lo, hi = sorted((100 * (soft - 1), 100 * (hard - 1)))
    return round(lo, 1), round(hi, 1)


def price_impact_from_demand(price: CommodityPrice, demand_change_fraction: float) -> PriceImpact:
    """Demand shift of fraction g -> price multiple (1+g)^(1/(η_d+η_s)), clamped."""
    denom = abs(price.elasticity_demand) + price.elasticity_supply
    multiple, clamped = _ces_multiple(1.0 + demand_change_fraction, denom, invert=False)
    return PriceImpact(
        supply_loss_pct=round(-100 * demand_change_fraction, 1),
        price_change_pct=round(100 * (multiple - 1), 1),
        anchor_usd=price.anchor_usd,
        implied_usd=round(price.anchor_usd * multiple, 2),
        unit=price.unit,
        clamped=clamped,
    )


# ---------------------------------------------------------------- rendering


def render_price_table(book: PriceBook, live: dict[str, Optional[FredQuote]]) -> str:
    lines = [
        f"{'commodity':<12}{'anchor':>14}{'live (FRED)':>16}{'12m avg':>12}"
        f"{'η_s':>6}{'η_d':>6}",
        "-" * 66,
    ]
    for name, p in book.commodities.items():
        q = live.get(name)
        anchor = f"{p.anchor_usd:,.0f}"
        live_s = f"{q.latest:,.0f}" if q else "—"
        avg_s = f"{q.avg_12m:,.0f}" if q else "—"
        lines.append(
            f"{name:<12}{anchor:>14}{live_s:>16}{avg_s:>12}"
            f"{p.elasticity_supply:>6.2f}{p.elasticity_demand:>6.2f}"
        )
    lines.append("\nanchor = normal/balanced-market price (USGS-anchored); live = latest FRED month.")
    lines.append("η_s/η_d = short-run supply/demand elasticities (disputable; data/seed/prices.yaml).")
    return "\n".join(lines)
