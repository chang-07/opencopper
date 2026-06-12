"""The exposure book: YOUR positions x the model's futures = P&L distributions.

This is what turns the model from a report into a decision environment. You
declare exposures (long/short, natural units); the engine runs PAIRED Monte
Carlo paths (same seed) under baseline and a scenario and returns the
distribution of your book's P&L delta — per position and in total. Decision
support only: it values exposures you already have or are weighing; it never
recommends or executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .montecarlo import simulate_commodity, simulate_copper
from .signals import DISCLAIMER


@dataclass
class Position:
    commodity: str
    quantity: float  # natural units (t, bbl, MMBtu, oz); + = long, - = short
    label: str = ""


@dataclass
class BookResult:
    scenario: str
    year: int
    total_p10: float
    total_p50: float
    total_p90: float
    per_position: list[dict] = field(default_factory=list)


def load_book(path: Path) -> list[Position]:
    raw = yaml.safe_load(path.read_text())
    return [Position(**p) for p in raw["positions"]]


def evaluate_book(
    positions: list[Position],
    scenario=None,
    *,
    year: int = 2026,
    n_paths: int = 1500,
    seed: int = 42,
) -> BookResult:
    """Paired-path P&L: same seed for baseline and scenario, so the delta
    isolates the scenario's effect from the shared randomness."""
    from .balance import BASELINE
    from .commodities import CommodityScenario, DriverScenario, compile_driver_scenario, load_commodity
    from .shocks import Scenario as EngineScenario

    totals: list[float] | None = None
    per_position = []
    for pos in positions:
        if pos.commodity == "copper":
            engine_sc = scenario if isinstance(scenario, EngineScenario) else None
            base = simulate_copper(BASELINE, n_paths=n_paths, seed=seed)
            scen = simulate_copper(engine_sc, n_paths=n_paths, seed=seed) if engine_sc and engine_sc.events else base
            yi = base.years.index(year)
            # paired deltas via stored sample paths are limited to 50; use the
            # band-difference approximation labeled as such for copper
            deltas = [scen.price.p10[yi] - base.price.p10[yi],
                      scen.price.p50[yi] - base.price.p50[yi],
                      scen.price.p90[yi] - base.price.p90[yi]]
            pnl = [pos.quantity * d for d in deltas]
            pnl_sorted = sorted(pnl)
            contribution = {"p10": pnl_sorted[0], "p50": pnl[1], "p90": pnl_sorted[2],
                            "approx": "band-difference"}
        else:
            sc = None
            if isinstance(scenario, DriverScenario):
                sc = compile_driver_scenario(scenario, load_commodity(pos.commodity))
            elif isinstance(scenario, CommodityScenario) and scenario.commodity == pos.commodity:
                sc = scenario
            base = simulate_commodity(pos.commodity, None, n_paths=n_paths, seed=seed)
            scen = simulate_commodity(pos.commodity, sc, n_paths=n_paths, seed=seed) if sc and getattr(sc, "events", None) else base
            if base is None:
                per_position.append({"label": pos.label or pos.commodity, "commodity": pos.commodity,
                                     "excluded": True})
                continue
            yi = base.years.index(year)
            deltas = [scen.price.p10[yi] - base.price.p10[yi],
                      scen.price.p50[yi] - base.price.p50[yi],
                      scen.price.p90[yi] - base.price.p90[yi]]
            pnl = [pos.quantity * d for d in deltas]
            pnl_sorted = sorted(pnl)
            contribution = {"p10": pnl_sorted[0], "p50": pnl[1], "p90": pnl_sorted[2],
                            "approx": "band-difference"}
        per_position.append({"label": pos.label or pos.commodity, "commodity": pos.commodity,
                             "quantity": pos.quantity, **contribution})
        c = [contribution["p10"], contribution["p50"], contribution["p90"]]
        totals = c if totals is None else [a + b for a, b in zip(totals, c)]

    totals = totals or [0.0, 0.0, 0.0]
    return BookResult(
        scenario=scenario.name if scenario else "baseline",
        year=year,
        total_p10=round(totals[0]), total_p50=round(totals[1]), total_p90=round(totals[2]),
        per_position=per_position,
    )


def render_book(result: BookResult) -> str:
    lines = [
        f"BOOK vs scenario '{result.scenario}' — {result.year} P&L delta (USD)",
        f"{'position':<34}{'qty':>12}{'P10':>14}{'P50':>14}{'P90':>14}",
        "-" * 88,
    ]
    for p in result.per_position:
        if p.get("excluded"):
            lines.append(f"{p['label']:<34}{'—':>12}{'excluded from shock pricing':>42}")
            continue
        lines.append(f"{p['label'][:33]:<34}{p['quantity']:>12,.0f}"
                     f"{p['p10']:>14,.0f}{p['p50']:>14,.0f}{p['p90']:>14,.0f}")
    lines += ["-" * 88,
              f"{'TOTAL':<46}{result.total_p10:>14,.0f}{result.total_p50:>14,.0f}{result.total_p90:>14,.0f}",
              "", "Band-difference approximation on paired-seed simulations; signs follow",
              "your position (+long/-short x price delta).", "", DISCLAIMER]
    return "\n".join(lines)


# ---------------------------------------------------------------- risk layer


@dataclass
class BookRisk:
    horizon: str
    window_months: int
    sigma_usd: float          # 1-month book P&L standard deviation
    var95_usd: float
    var99_usd: float
    es95_usd: float
    gross_usd: float          # sum of |notional|
    undiversified_sigma: float  # sum of |notional_i| * sigma_i — no-correlation-benefit bound
    cf_var95_usd: float | None = None   # Cornish-Fisher (skew/kurtosis-adjusted)
    pnl_skew: float | None = None
    pnl_exkurt: float | None = None
    rows: list[dict] = field(default_factory=list)        # per-position notional/vol/contribution
    excluded: list[str] = field(default_factory=list)     # no price history -> not in VaR
    corr: dict[str, dict[str, float]] = field(default_factory=dict)


def _aligned_returns(names: list[str], window: int = 120) -> tuple[list[str], dict[str, list[float]]]:
    """Monthly log returns aligned on the date intersection of all series."""
    import math as _m

    from .history import load_price_history

    series: dict[str, dict[str, float]] = {}
    for n in names:
        h = load_price_history(n)
        if h:
            series[n] = dict(h.months[-(window + 1):])
    if not series:
        return [], {}
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    rets: dict[str, list[float]] = {n: [] for n in series}
    for i in range(1, len(common)):
        d0, d1 = common[i - 1], common[i]
        for n, s in series.items():
            rets[n].append(_m.log(s[d1] / s[d0]) if s[d0] > 0 else 0.0)
    return list(series), rets


def book_risk(positions: list[Position], window: int = 120) -> BookRisk:
    """Delta-normal 1-month VaR/ES on the book from HISTORICAL covariance.

    This is the question the scenario engine doesn't answer: not "what if
    Indonesia halves output" but "how much does this book breathe month to
    month, correlations included". Delta-normal is the honest floor — it
    understates tails (commodity returns are fatter than normal; compare the
    spike-odds columns), which is stated rather than hidden. Risk
    MEASUREMENT of a book you declared; never sizing advice.
    """
    import math as _m

    from .pricing import cached_fred, load_pricebook, summarize

    book = load_pricebook()
    names = [p.commodity for p in positions]
    have, rets = _aligned_returns(sorted(set(names)), window)
    n_obs = len(next(iter(rets.values()), []))

    # notional = qty x latest price (live where a series exists, else anchor)
    notionals: dict[int, float] = {}
    for idx, pos in enumerate(positions):
        price = book.commodities[pos.commodity].anchor_usd
        fs = book.commodities[pos.commodity].fred_series
        if fs:
            try:
                q = summarize(fs, cached_fred(fs))
                if q:
                    price = q.latest
            except Exception:
                pass
        notionals[idx] = pos.quantity * price

    means = {n: sum(r) / len(r) for n, r in rets.items()} if n_obs else {}

    def cov(a: str, b: str) -> float:
        ra, rb = rets[a], rets[b]
        return sum((ra[i] - means[a]) * (rb[i] - means[b]) for i in range(n_obs)) / (n_obs - 1)

    included = [i for i, p in enumerate(positions) if p.commodity in have]
    excluded = [positions[i].label or positions[i].commodity
                for i, p in enumerate(positions) if p.commodity not in have]

    var_p = 0.0
    marginal: dict[int, float] = {i: 0.0 for i in included}
    for i in included:
        for j in included:
            c = cov(positions[i].commodity, positions[j].commodity)
            var_p += notionals[i] * notionals[j] * c
            marginal[i] += notionals[j] * c
    sigma = _m.sqrt(max(var_p, 0.0))

    rows = []
    undiv = 0.0
    for i in included:
        own_vol = _m.sqrt(cov(positions[i].commodity, positions[i].commodity))
        undiv += abs(notionals[i]) * own_vol
        rows.append({
            "label": positions[i].label or positions[i].commodity,
            "commodity": positions[i].commodity,
            "notional_usd": round(notionals[i]),
            "monthly_vol_pct": round(100 * own_vol, 1),
            "contribution_pct": round(100 * notionals[i] * marginal[i] / var_p, 1) if var_p else 0.0,
        })

    corr = {}
    for a in have:
        corr[a] = {}
        for b in have:
            sa, sb = _m.sqrt(cov(a, a)), _m.sqrt(cov(b, b))
            corr[a][b] = round(cov(a, b) / (sa * sb), 2) if sa and sb else 0.0

    # Cornish-Fisher (Zangari 1996): adjust the 95% quantile for the realized
    # skew and excess kurtosis of the BOOK's historical P&L — the standard fix
    # for delta-normal's thin tails, and exactly zero extra assumptions: the
    # moments come from the same window as the covariance.
    cf_var = skew = exk = None
    if n_obs >= 36 and sigma > 0:
        pnl = [sum(notionals[i] * rets[positions[i].commodity][t] for i in included)
               for t in range(n_obs)]
        mu = sum(pnl) / n_obs
        m2 = sum((x - mu) ** 2 for x in pnl) / n_obs
        if m2 > 0:
            m3 = sum((x - mu) ** 3 for x in pnl) / n_obs
            m4 = sum((x - mu) ** 4 for x in pnl) / n_obs
            skew = m3 / m2 ** 1.5
            exk = m4 / m2 ** 2 - 3
            sl, kl = -skew, exk  # loss-side moments
            z = 1.645
            z_cf = (z + (z * z - 1) * sl / 6 + (z ** 3 - 3 * z) * kl / 24
                    - (2 * z ** 3 - 5 * z) * sl * sl / 36)
            cf_var = round(z_cf * sigma)
            skew, exk = round(skew, 2), round(exk, 2)

    es_mult = _m.exp(-1.645 ** 2 / 2) / (_m.sqrt(2 * _m.pi) * 0.05)  # ~2.063
    return BookRisk(
        horizon="1 month", window_months=n_obs,
        sigma_usd=round(sigma), var95_usd=round(1.645 * sigma),
        var99_usd=round(2.326 * sigma), es95_usd=round(es_mult * sigma),
        cf_var95_usd=cf_var, pnl_skew=skew, pnl_exkurt=exk,
        gross_usd=round(sum(abs(v) for v in notionals.values())),
        undiversified_sigma=round(undiv),
        rows=rows, excluded=excluded, corr=corr,
    )


def render_risk(r: BookRisk) -> str:
    lines = [
        f"BOOK RISK — delta-normal, {r.horizon} horizon, {r.window_months} months of history",
        f"{'position':<30}{'notional $':>15}{'mvol':>7}{'risk contrib':>14}",
        "-" * 66,
    ]
    for row in r.rows:
        lines.append(f"{row['label'][:29]:<30}{row['notional_usd']:>15,}"
                     f"{row['monthly_vol_pct']:>6.1f}%{row['contribution_pct']:>13.1f}%")
    for label in r.excluded:
        lines.append(f"{label[:29]:<30}{'no price history — not in VaR':>36}")
    lines += [
        "-" * 66,
        f"gross notional      {r.gross_usd:>15,}",
        f"book sigma (1m)     {r.sigma_usd:>15,}   "
        f"(undiversified bound {r.undiversified_sigma:,}; "
        f"diversification saves {1 - r.sigma_usd / r.undiversified_sigma:.0%})" if r.undiversified_sigma else "",
        f"VaR 95% / 99% (1m)  {r.var95_usd:>15,} / {r.var99_usd:,}",
        f"ES 95% (1m)         {r.es95_usd:>15,}",
        (f"CF VaR 95% (1m)     {r.cf_var95_usd:>15,}   "
         f"(Cornish-Fisher; book P&L skew {r.pnl_skew:+.2f}, excess kurt {r.pnl_exkurt:+.2f})"
         if r.cf_var95_usd is not None else ""),
        "",
        "correlations (monthly, aligned window):",
    ]
    names = list(r.corr)
    lines.append("  " + " " * 12 + "".join(f"{n[:9]:>10}" for n in names))
    for a in names:
        lines.append(f"  {a[:11]:<12}" + "".join(f"{r.corr[a][b]:>10.2f}" for b in names))
    lines += [
        "",
        "Delta-normal UNDERSTATES tails (returns are fatter than normal — see the",
        "spike-odds columns on the desk sheet), and FRED/IMF monthly AVERAGES",
        "smooth intramonth swings, so vols here are a floor twice over. Treat VaR",
        "as the floor, not the story. Risk measurement of a declared book; never",
        "sizing or advice.",
        "", DISCLAIMER,
    ]
    return "\n".join(lines)
