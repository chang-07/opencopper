"""Futures data — the layer that unlocks CARRY, the factor that actually works.

Spot prices (FRED/Pink Sheet) can't express the single most robust commodity
factor: carry / roll yield, the slope of the futures curve (backwardation vs
contango; Gorton-Hayashi-Rouwenhorst 2013, Koijen-Moskowitz-Pedersen-Vrugt
2018). This module adds free, keyless front-month futures (Yahoo Finance
chart API — monthly bars, ~15y, no key) and derives two things spot can't:

1. **Futures total-return momentum** — returns of the front-continuous
   contract embed the roll, so they are a better momentum input than spot.
   Unit-free (ratios), available wherever a Yahoo symbol exists.

2. **Front-basis carry** = (spot - F1) / F1, same-month-aligned. Positive =
   backwardation = positive expected roll yield = long. Computed ONLY for
   commodities whose Yahoo front shares our spot's benchmark (Brent crude,
   Henry Hub gas, COMEX/LBMA metals, CBOT grains). Copper and base metals are
   deliberately EXCLUDED: the 2025-26 COMEX-LME tariff spread sits in their
   basis and would corrupt the carry sign — an honest exclusion, not a gap.
   This is the front-basis (convenience-yield) proxy, not the F1-F2 slope; a
   true two-point curve needs a paid feed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import httpx

from .pricing import PRICE_CACHE_DIR, cache_age_days

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
FUTURES_TTL_DAYS = 1  # monthly bars, but cheap; refresh daily in the Action
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# grain bushel weights (kg) -> cents/bushel to USD/tonne multiplier
_GRAIN = lambda kg: (1 / 100) * (1000 / kg)


@dataclass
class FutCfg:
    symbol: str
    mult_to_anchor: float   # Yahoo front close x mult = price in our anchor unit
    carry: bool             # is the spot/front benchmark clean enough for carry?


# commodity -> Yahoo front-continuous config. `carry=False` means we still use
# it for futures momentum but NOT for the basis carry signal.
YAHOO_FUTURES: dict[str, FutCfg] = {
    "crude-oil": FutCfg("BZ=F", 1.0, True),       # Brent $/bbl vs our Brent spot
    "natural-gas": FutCfg("NG=F", 1.0, True),     # Henry Hub $/MMBtu both
    "silver": FutCfg("SI=F", 1.0, True),          # $/oz
    "platinum": FutCfg("PL=F", 1.0, True),        # $/oz
    "wheat": FutCfg("ZW=F", _GRAIN(27.2155), True),
    "corn": FutCfg("ZC=F", _GRAIN(25.4012), True),
    "soybeans": FutCfg("ZS=F", _GRAIN(27.2155), True),
    # momentum only (clean spot basis unavailable or cross-exchange-distorted):
    "copper": FutCfg("HG=F", 2204.62, False),     # COMEX $/lb; LME-COMEX tariff spread corrupts the basis
    "gold": FutCfg("GC=F", 1.0, False),           # no clean keyless gold spot (we hold an index)
}


def _fetch_yahoo(symbol: str, client: httpx.Client | None = None) -> list[tuple[str, float]]:
    """Monthly-AVERAGE front-continuous closes from daily Yahoo bars. Averaging
    over the month matches our FRED/Pink Sheet spot (also a monthly average),
    so the basis isn't polluted by within-month price moves (month-end close
    vs monthly-average spot was inflating carry by 10-20pp)."""
    own = client is None
    client = client or httpx.Client(timeout=40, follow_redirects=True, headers=_UA)
    try:
        r = client.get(f"{YAHOO_CHART}{symbol}", params={"range": "10y", "interval": "1d"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        import datetime as dt

        buckets: dict[str, list[float]] = {}
        for t, c in zip(ts, closes):
            if c is None:
                continue
            d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
            buckets.setdefault(f"{d.year}-{d.month:02d}-01", []).append(float(c))
        return [(m, round(sum(v) / len(v), 4)) for m, v in sorted(buckets.items())]
    finally:
        if own:
            client.close()


def cached_front(commodity: str, ttl_days: float = FUTURES_TTL_DAYS,
                 cache_dir: Path = PRICE_CACHE_DIR) -> list[tuple[str, float]] | None:
    """Monthly front-continuous closes (native unit), TTL-cached like FRED.
    None when the commodity has no Yahoo mapping."""
    cfg = YAHOO_FUTURES.get(commodity)
    if not cfg:
        return None
    cache = cache_dir / f"yahoo-{cfg.symbol.replace('=', '_')}.csv"
    age = cache_age_days(cache)
    if age is not None and age <= ttl_days:
        from .pricing import _read_price_csv

        return _read_price_csv(cache)
    try:
        rows = _fetch_yahoo(cfg.symbol)
    except Exception:
        if age is not None:
            from .pricing import _read_price_csv

            return _read_price_csv(cache)
        return None
    if rows:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(f"{d},{v}" for d, v in rows))
    return rows


def futures_returns(commodity: str) -> list[tuple[str, float]]:
    """Monthly log returns of the front-continuous contract (futures
    total-return proxy; unit-free). Empty when unmapped."""
    rows = cached_front(commodity)
    if not rows or len(rows) < 14:
        return []
    out = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1][1], rows[i][1]
        if a > 0 and b > 0:
            out.append((rows[i][0], math.log(b / a)))
    return out


def basis_carry(commodity: str) -> list[tuple[str, float]]:
    """Front-basis carry series (spot - F1)/F1, same-month-aligned, annualized
    by the ~1-month horizon. Empty unless the commodity is carry-clean."""
    cfg = YAHOO_FUTURES.get(commodity)
    if not cfg or not cfg.carry:
        return []
    front = cached_front(commodity)
    if not front:
        return []
    from .history import load_price_history

    h = load_price_history(commodity)
    if not h:
        return []
    spot = dict(h.months)
    fr = {d: v * cfg.mult_to_anchor for d, v in front}
    out = []
    for d in sorted(set(spot) & set(fr)):
        f = fr[d]
        if f > 0:
            out.append((d, (spot[d] - f) / f))  # >0 backwardation -> long
    return out


def carry_signal(commodity: str, trailing: int = 36) -> list[tuple[str, float]]:
    """Carry as a signal: the basis DEMEANED by its own trailing mean, which
    removes the structural benchmark offset (IMF "maize" sits ~20% above CBOT;
    that constant is not carry) and leaves the time-varying term-structure
    signal. Expanding mean until `trailing` months exist, then trailing — so
    it is causal (no look-ahead) and usable both in backtest and live."""
    raw = basis_carry(commodity)
    if len(raw) < 12:
        return []
    out = []
    for i in range(len(raw)):
        window = [v for _, v in raw[max(0, i - trailing + 1): i + 1]]
        out.append((raw[i][0], raw[i][1] - sum(window) / len(window)))
    return out


def latest_carry(commodity: str) -> float | None:
    s = basis_carry(commodity)
    return s[-1][1] if s else None


def latest_carry_signal(commodity: str) -> float | None:
    s = carry_signal(commodity)
    return s[-1][1] if s else None


def carry_commodities() -> list[str]:
    return [c for c, cfg in YAHOO_FUTURES.items() if cfg.carry]


def render_carry() -> str:
    """Snapshot of the current carry per carry-clean commodity. `signal` is the
    tradable number (basis demeaned of its structural benchmark offset); raw
    basis is shown for context."""
    lines = ["FUTURES CARRY — front-basis (spot - F1)/F1; signal = demeaned (tradable)",
             "", f"{'commodity':<13}{'raw basis':>11}{'signal':>9}{'shape':>15}"]
    rows = []
    for c in carry_commodities():
        sig = latest_carry_signal(c)
        raw = latest_carry(c)
        if sig is None:
            continue
        rows.append((c, raw, sig))
    for c, raw, sig in sorted(rows, key=lambda x: -x[2]):
        shape = "backwardated" if sig > 0.01 else "contango" if sig < -0.01 else "flat"
        lines.append(f"{c:<13}{raw:>+10.1%}{sig:>+9.1%}{shape:>15}")
    lines += ["",
              "Carry is the most robust commodity factor (GHR 2013; Koijen et al 2018).",
              "SIGNAL = basis minus its trailing mean — removes the structural benchmark",
              "offset (IMF 'maize' sits ~20% over CBOT; not carry) so the term-structure",
              "variation is what's traded. Front-basis proxy (not F1-F2 slope); copper /",
              "base-metals excluded — the COMEX-LME tariff spread would corrupt the basis.",
              "Source: Yahoo front-continuous (keyless, monthly-averaged) vs our spot."]
    return "\n".join(lines)
