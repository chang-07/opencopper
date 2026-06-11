"""The desk sheet: model vs market, per commodity, machine-readable.

DECISION SUPPORT ONLY. This module reads live prices and the model's state and
emits signals a human trader (or their own systems, via --json) can weigh. It
does not place orders, size positions, or constitute investment advice — and
the output says so. The model's own honesty boxes apply doubly here: anchors
and elasticities are seed-estimates, the implied prices are illustrative
mechanics, and PREDICTIONS.md is the running scorecard of how calls fare.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Optional

DISCLAIMER = (
    "NOT INVESTMENT ADVICE. Model outputs are illustrative mechanics on "
    "seed-estimate assumptions; see docs/methodology.md and PREDICTIONS.md."
)


@dataclass
class Signal:
    commodity: str
    futures: Optional[dict]
    unit: str
    live: Optional[float]
    live_date: Optional[str]
    anchor: float
    gap_vs_anchor_pct: Optional[float]  # live richness/cheapness vs the balanced anchor
    regime: Optional[str]               # glut / balanced / tight (34yr trailing-trend)
    ambient_vol_pct: float
    model_p50_2026: Optional[float]     # country-tier MC median (copper: full engine)
    prob_double_2026: Optional[float]
    prob_halve_2026: Optional[float]
    notes: str


def build_signals(n_paths: int = 800) -> list[Signal]:
    from .balance import BASELINE
    from .history import ambient_volatility, load_price_history
    from .montecarlo import simulate_commodity, simulate_copper
    from .pricing import cached_fred, load_pricebook, summarize

    book = load_pricebook()
    copper_mc = simulate_copper(BASELINE, n_paths=n_paths, seed=42)
    i26 = copper_mc.years.index(2026)

    out: list[Signal] = []
    for name, p in book.commodities.items():
        live = live_date = None
        if p.fred_series:
            try:
                q = summarize(p.fred_series, cached_fred(p.fred_series))
                if q:
                    live, live_date = q.latest, q.latest_date
            except Exception:
                pass
        hist = load_price_history(name)
        if live is None and hist:  # Pink Sheet fallback (silver) — date shows staleness
            live_date, live = hist.months[-1]
            live = round(live, 2)
        vol, _ = ambient_volatility(name)

        p50 = pdbl = phlv = None
        if name == "copper":
            p50 = copper_mc.price.p50[i26]
            pdbl = copper_mc.prob_price_spike[2026]  # >1.5x anchor for copper engine
        else:
            mc = simulate_commodity(name, n_paths=n_paths, seed=42)
            if mc:
                j = mc.years.index(2026)
                p50, pdbl, phlv = mc.price.p50[j], mc.prob_double[2026], mc.prob_halve[2026]

        out.append(
            Signal(
                commodity=name,
                futures=p.futures,
                unit=p.unit,
                live=live,
                live_date=live_date,
                anchor=p.anchor_usd,
                gap_vs_anchor_pct=round(100 * (live / p.anchor_usd - 1), 1) if live else None,
                regime=hist.regime_now.value if hist else None,
                ambient_vol_pct=round(100 * vol, 1),
                model_p50_2026=p50,
                prob_double_2026=pdbl,
                prob_halve_2026=phlv,
                notes=p.note,
            )
        )
    out.sort(key=lambda s: -(abs(s.gap_vs_anchor_pct) if s.gap_vs_anchor_pct is not None else -1))
    return out


def render_signals(signals: list[Signal]) -> str:
    lines = [
        "DESK SHEET — model vs market (decision support, not advice)",
        f"{'commodity':<12}{'fut':>9}{'live':>11}{'anchor':>10}{'gap':>8}{'regime':>10}"
        f"{'vol':>6}{'P50 26':>10}{'P(2x)':>7}",
        "-" * 86,
    ]
    for s in signals:
        fut = s.futures.get("symbol") if s.futures and s.futures.get("symbol") else "—"
        live = f"{s.live:,.0f}" if s.live else "—"
        gap = f"{s.gap_vs_anchor_pct:+.0f}%" if s.gap_vs_anchor_pct is not None else "—"
        p50 = f"{s.model_p50_2026:,.0f}" if s.model_p50_2026 else "—"
        pd = f"{s.prob_double_2026:.0%}" if s.prob_double_2026 is not None else "—"
        lines.append(
            f"{s.commodity:<12}{fut:>9}{live:>11}{s.anchor:>10,.0f}{gap:>8}"
            f"{(s.regime or '—'):>10}{s.ambient_vol_pct:>5.0f}%{p50:>10}{pd:>7}"
        )
    lines += ["", "gap = live vs balanced-market anchor; regime = 34yr trailing-trend state;",
              "P50 26 = simulated 2026 median; P(2x) = tail-event odds (copper: >1.5x).",
              "", DISCLAIMER]
    return "\n".join(lines)


def signals_json(signals: list[Signal]) -> str:
    return json.dumps(
        {"disclaimer": DISCLAIMER, "signals": [asdict(s) for s in signals]}, indent=1
    )
