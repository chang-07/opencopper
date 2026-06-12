"""Historical price data → empirical states and calibration targets.

This is what separates a real world model from a trend extrapolation: the
simulator is calibrated and validated against ~34 years of actual price
history (FRED / IMF monthly series, 1992-present). History does two jobs here:

1. **Defines states.** Markets aren't a smooth trend — they sit in regimes
   (glut / balanced / tight), and the regime is what a scenario actually moves
   you between. We classify every historical month by its price relative to a
   trailing trend, so "what state are we in" is an empirical fact, not a vibe.

2. **Calibrates the simulator.** Realized annual price volatility is the target
   the Monte Carlo disruption model must reproduce (see calibrate.py). If the
   simulated world is as volatile as the real one, the disruption distribution
   is honest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .pricing import cached_fred, load_pricebook

TREND_WINDOW = 36  # months for the trailing trend that defines regimes
TIGHT_THRESHOLD = 0.15  # >15% above trend = tight market
GLUT_THRESHOLD = -0.15  # >15% below trend = glut


class Regime(str, Enum):
    GLUT = "glut"
    BALANCED = "balanced"
    TIGHT = "tight"


def _log(x: float) -> float:
    return math.log(x) if x > 0 else 0.0


@dataclass
class PriceHistory:
    commodity: str
    series: str
    months: list[tuple[str, float]]  # (YYYY-MM-DD, price)
    annual_volatility: float  # std of year-over-year log change in annual avg price
    monthly_annualized_vol: float
    max_drawdown: float
    regime_now: Regime
    regime_fractions: dict[str, float]
    annual_avg: dict[int, float]

    @property
    def start(self) -> str:
        return self.months[0][0]

    @property
    def end(self) -> str:
        return self.months[-1][0]


def _annual_average(months: list[tuple[str, float]]) -> dict[int, float]:
    by_year: dict[int, list[float]] = {}
    for date, price in months:
        by_year.setdefault(int(date[:4]), []).append(price)
    return {y: sum(v) / len(v) for y, v in by_year.items()}


def _annual_volatility(annual_avg: dict[int, float]) -> float:
    years = sorted(annual_avg)
    rets = [
        _log(annual_avg[y] / annual_avg[y - 1])
        for y in years[1:]
        if annual_avg[y - 1] > 0
    ]
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))


def _monthly_annualized_vol(months: list[tuple[str, float]]) -> float:
    vals = [v for _, v in months]
    rets = [_log(vals[i] / vals[i - 1]) for i in range(1, len(vals)) if vals[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    monthly = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
    return monthly * math.sqrt(12)


def _max_drawdown(months: list[tuple[str, float]]) -> float:
    peak = -math.inf
    worst = 0.0
    for _, p in months:
        peak = max(peak, p)
        if peak > 0:
            worst = min(worst, p / peak - 1)
    return worst


def _classify_regimes(months: list[tuple[str, float]]) -> tuple[list[Regime], dict[str, float]]:
    logs = [_log(p) for _, p in months]
    regimes: list[Regime] = []
    for i in range(len(months)):
        window = logs[max(0, i - TREND_WINDOW + 1) : i + 1]
        trend = sum(window) / len(window)
        dev = logs[i] - trend
        if dev > TIGHT_THRESHOLD:
            regimes.append(Regime.TIGHT)
        elif dev < GLUT_THRESHOLD:
            regimes.append(Regime.GLUT)
        else:
            regimes.append(Regime.BALANCED)
    counts = {r.value: 0 for r in Regime}
    for r in regimes:
        counts[r.value] += 1
    fractions = {k: round(v / len(regimes), 3) for k, v in counts.items()}
    return regimes, fractions


def load_price_history(commodity: str) -> Optional[PriceHistory]:
    """Full monthly price history: FRED first, World Bank Pink Sheet second,
    None if neither carries the commodity."""
    price = load_pricebook().commodities.get(commodity)
    if not price:
        return None
    months: list[tuple[str, float]] = []
    series = ""
    if price.fred_series:
        months = cached_fred(price.fred_series)
        series = price.fred_series
    else:
        from .pinksheet import PINKSHEET_SERIES, cached_pinksheet

        if commodity in PINKSHEET_SERIES:
            try:
                months = cached_pinksheet(commodity)
                series = f"PinkSheet:{PINKSHEET_SERIES[commodity]}"
            except Exception:
                return None
    if price.series_start:
        months = [(d, v) for d, v in months if d >= price.series_start]
    if len(months) < TREND_WINDOW:
        return None
    annual_avg = _annual_average(months)
    regimes, fractions = _classify_regimes(months)
    return PriceHistory(
        commodity=commodity,
        series=series,
        months=months,
        annual_volatility=round(_annual_volatility(annual_avg), 4),
        monthly_annualized_vol=round(_monthly_annualized_vol(months), 4),
        max_drawdown=round(_max_drawdown(months), 4),
        regime_now=regimes[-1],
        regime_fractions=fractions,
        annual_avg=annual_avg,
    )


def regime_volatility(commodity: str, min_obs: int = 24) -> Optional[dict[str, float]]:
    """Annualized monthly vol conditional on the regime state — causally:
    the regime at month i-1 conditions the return over month i, so each
    bucket answers "given we are in regime R now, how volatile is next
    month". Buckets with fewer than min_obs months return no estimate.

    Vol clusters by state, which the single unconditional number hides. The
    Monte Carlo stays calibrated to UNCONDITIONAL realized vol — conditioning
    the simulator would double-count the regime, since scenarios already
    move the state."""
    h = load_price_history(commodity)
    if not h:
        return None
    regimes, _ = _classify_regimes(h.months)
    vals = [v for _, v in h.months]
    buckets: dict[str, list[float]] = {r.value: [] for r in Regime}
    for i in range(1, len(vals)):
        if vals[i - 1] > 0:
            buckets[regimes[i - 1].value].append(_log(vals[i] / vals[i - 1]))
    out: dict[str, float] = {}
    for name, rets in buckets.items():
        if len(rets) < min_obs:
            continue
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        out[name] = round(sd * math.sqrt(12), 4)
    return out or None


# Ambient annual volatility when a commodity has no price series: a documented
# round default near the middle of the observed metals range (17-28%).
DEFAULT_AMBIENT_VOL = 0.30


def ambient_volatility(commodity: str) -> tuple[float, str]:
    """Realized annual vol from history; else the commodity's SEEDED vol
    (per-name, with its basis in prices.yaml); else the documented default."""
    h = load_price_history(commodity)
    if h:
        return h.annual_volatility, f"realized {h.start[:4]}-{h.end[:4]} ({h.series})"
    seeded = load_pricebook().commodities.get(commodity)
    if seeded and seeded.ambient_vol:
        return seeded.ambient_vol, "seeded per-commodity (basis in prices.yaml)"
    return DEFAULT_AMBIENT_VOL, "no price series; documented default"


def render_history(h: PriceHistory) -> str:
    lines = [
        f"{h.commodity.upper()} price history — {h.series} ({h.start[:7]} → {h.end[:7]}, "
        f"{len(h.months)} months)",
        f"  annual volatility:     {h.annual_volatility:.1%}   "
        f"(monthly-annualized {h.monthly_annualized_vol:.1%})",
        f"  max drawdown:          {h.max_drawdown:.1%}",
        f"  current regime:        {h.regime_now.value.upper()}",
        f"  time in each regime:   glut {h.regime_fractions['glut']:.0%} | "
        f"balanced {h.regime_fractions['balanced']:.0%} | tight {h.regime_fractions['tight']:.0%}",
    ]
    recent = sorted(h.annual_avg)[-6:]
    lines.append("  recent annual avg:     " + "  ".join(f"{y}:{h.annual_avg[y]:,.0f}" for y in recent))
    return "\n".join(lines)


def conditional_volatility(commodity: str) -> tuple[float, str]:
    """Ambient vol conditioned on the CURRENT regime when estimable (vol is
    state-dependent and U-shaped — extreme states are volatile states), else
    the unconditional realized number, else the documented default. The
    source string says which one you got."""
    h = load_price_history(commodity)
    if h:
        rv = regime_volatility(commodity)
        now = h.regime_now.value
        if rv and now in rv:
            return rv[now], f"conditional on {now} regime ({h.series})"
    return ambient_volatility(commodity)
