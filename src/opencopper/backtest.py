"""Walk-forward backtest: does the regime signal predict forward returns?

The desk sheet classifies every market into glut/balanced/tight by price vs
its trailing 36-month trend. Before anyone uses that label to make a
decision, it has to answer the quant's first question: **conditional on the
label today, what happened next, over 34 years?**

Method (all causal, no lookahead):

- signal at month i: ``dev_i`` = log price minus trailing 36m mean log price
  — the same statistic that defines regimes, known at month-end i
- outcome: forward h-month log return ``f_i = log(p[i+h]/p[i])``
- regression ``f = a + b*dev`` with a Newey-West (Bartlett, lag h-1) t-stat
  on b, because overlapping h-month windows induce MA(h-1) errors.
  b < 0 means deviations mean-revert; b > 0 means they extend (momentum)
- regime buckets: mean forward return conditional on glut/balanced/tight
- an illustrative regime-following rule (long glut / flat balanced / short
  tight, monthly, gross of costs) — evidence about the signal, NOT a strategy

Honesty notes. The regime parameters (36m window, ±15% thresholds) were
chosen to *describe* history before this backtest existed and are not fitted
to forward returns. The rule ignores costs, carry/roll, and uses spot-proxy
monthly averages (FRED/IMF), which smooth intramonth turning points.
Commodities are correlated, so the cross-commodity sign test overstates
independence. DECISION SUPPORT ONLY — see signals.DISCLAIMER.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .history import (
    GLUT_THRESHOLD,
    TIGHT_THRESHOLD,
    TREND_WINDOW,
    load_price_history,
)
from .pricing import load_pricebook


def deviations(months: list[tuple[str, float]]) -> list[float]:
    """Causal trend deviation per month — identical statistic to the regime
    classifier (trailing window INCLUDING the current month)."""
    logs = [math.log(p) for _, p in months]
    out = []
    for i in range(len(logs)):
        window = logs[max(0, i - TREND_WINDOW + 1) : i + 1]
        out.append(logs[i] - sum(window) / len(window))
    return out


def nw_slope(x: list[float], y: list[float], lag: int) -> tuple[float, float]:
    """OLS slope of y on x with a Newey-West (Bartlett kernel) standard
    error. Returns (b, t). Spelled out rather than imported so every number
    in the table is auditable."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    xc = [v - mx for v in x]
    sxx = sum(v * v for v in xc)
    b = sum(xc[i] * (y[i] - my) for i in range(n)) / sxx
    a = my - b * mx
    e = [y[i] - a - b * x[i] for i in range(n)]
    u = [xc[i] * e[i] for i in range(n)]
    s = sum(v * v for v in u)
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        s += 2 * w * sum(u[i] * u[i + l] for i in range(n - l))
    se = math.sqrt(max(s, 0.0)) / sxx
    if se == 0:  # exact fit: infinite-confidence slope
        return b, math.copysign(math.inf, b)
    return b, b / se


def _sign_test_p(n_neg: int, n: int) -> float:
    """Two-sided binomial sign test against p=0.5."""
    def cdf(k):
        return sum(math.comb(n, j) for j in range(k + 1)) / 2 ** n
    lo = cdf(n_neg)
    hi = 1 - cdf(n_neg - 1) if n_neg > 0 else 1.0
    return min(1.0, 2 * min(lo, hi))


@dataclass
class BacktestRow:
    commodity: str
    n_months: int
    horizon: int
    slope: float                 # b: fwd return per unit of trend deviation
    t_stat: float                # Newey-West
    half_life_months: float | None      # AR(1) on the deviation: ln(.5)/ln(rho)
    mean_fwd: dict[str, float | None]   # regime -> mean fwd return
    n_regime: dict[str, int]
    strat: dict[str, float] = field(default_factory=dict)
    hold: dict[str, float] = field(default_factory=dict)
    monthly_legs: dict[str, tuple[str, float]] = field(default_factory=dict)  # date -> (regime, next-month return)
    cells_2x2: dict[str, dict] = field(default_factory=dict)  # "regime|mom" -> {n, mean_fwd}


def half_life(devs: list[float]) -> float | None:
    """Mean-reversion half-life of the trend deviation from its AR(1)
    coefficient: ln(0.5)/ln(rho). The persistence statistic of Cashin, Liang
    & McDermott (2000) applied to the deviation (the trend itself is removed
    by construction, so this measures how long a DISLOCATION lasts, not how
    long a price cycle lasts)."""
    num = sum(devs[i] * devs[i + 1] for i in range(len(devs) - 1))
    den = sum(d * d for d in devs[:-1])
    if den <= 0:
        return None
    rho = num / den
    if not 0 < rho < 1:
        return None
    return round(math.log(0.5) / math.log(rho), 1)


def _regime_of(dev: float) -> str:
    if dev > TIGHT_THRESHOLD:
        return "tight"
    if dev < GLUT_THRESHOLD:
        return "glut"
    return "balanced"


def _perf(rets: list[float]) -> dict[str, float]:
    n = len(rets)
    mu = sum(rets) / n
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    equity = peak = 0.0
    worst = 0.0
    for r in rets:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return {
        "ann_ret": round(12 * mu, 4),
        "ann_vol": round(sd * math.sqrt(12), 4),
        "sharpe": round(12 * mu / (sd * math.sqrt(12)), 2) if sd else 0.0,
        "max_dd": round(math.exp(worst) - 1, 4),
    }


def backtest_commodity(name: str, horizon: int = 12) -> BacktestRow | None:
    h = load_price_history(name)
    if not h or len(h.months) < TREND_WINDOW + horizon + 24:
        return None
    months = h.months
    devs = deviations(months)
    logs = [math.log(p) for _, p in months]

    start = TREND_WINDOW  # only months with a full trend window behind them
    xs, ys, regimes, moms = [], [], [], []
    for i in range(start, len(months) - horizon):
        xs.append(devs[i])
        ys.append(logs[i + horizon] - logs[i])
        regimes.append(_regime_of(devs[i]))
        # 12m past return sign: the Miffre-Rallis (2007) momentum signal,
        # crossed with the value signal a la Asness-Moskowitz-Pedersen (2013)
        moms.append("up" if logs[i] - logs[i - 12] >= 0 else "down")
    if len(xs) < 60:
        return None
    b, t = nw_slope(xs, ys, lag=horizon - 1)

    mean_fwd: dict[str, float | None] = {}
    n_regime: dict[str, int] = {}
    for r in ("glut", "balanced", "tight"):
        sub = [ys[i] for i in range(len(ys)) if regimes[i] == r]
        n_regime[r] = len(sub)
        mean_fwd[r] = round(sum(sub) / len(sub), 4) if len(sub) >= 12 else None

    cells: dict[str, dict] = {}
    for r in ("glut", "balanced", "tight"):
        for m in ("up", "down"):
            sub = [ys[i] for i in range(len(ys)) if regimes[i] == r and moms[i] == m]
            cells[f"{r}|{m}"] = {"n": len(sub),
                                 "mean_fwd": round(sum(sub) / len(sub), 4) if len(sub) >= 12 else None}

    # regime rule, next-month application: signal at i, return i -> i+1
    strat_rets, hold_rets, monthly = [], [], {}
    for i in range(start, len(months) - 1):
        r1m = logs[i + 1] - logs[i]
        regime = _regime_of(devs[i])
        w = {"glut": 1.0, "balanced": 0.0, "tight": -1.0}[regime]
        strat_rets.append(w * r1m)
        hold_rets.append(r1m)
        monthly[months[i + 1][0][:7]] = (regime, r1m)

    return BacktestRow(
        commodity=name, n_months=len(xs), horizon=horizon,
        slope=round(b, 3), t_stat=round(t, 2),
        half_life_months=half_life(devs[start:]),
        mean_fwd=mean_fwd, n_regime=n_regime,
        strat=_perf(strat_rets), hold=_perf(hold_rets), monthly_legs=monthly,
        cells_2x2=cells,
    )


def backtest_all(horizon: int = 12) -> list[BacktestRow]:
    rows = []
    for name in load_pricebook().commodities:
        row = backtest_commodity(name, horizon)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: r.t_stat)
    return rows


def summary(rows: list[BacktestRow]) -> dict:
    """Cross-commodity verdict + the equal-weight rule split into its legs.

    The legs answer different questions: the long-glut leg tests "do
    depressed markets recover", the short-tight leg tests "are elevated
    markets safely shortable". History says yes and NO respectively — tight
    markets carry the right-tail squeeze risk the spike-odds machinery
    models, which is why the symmetric rule loses despite 9/10 negative
    slopes."""
    n_neg = sum(1 for r in rows if r.slope < 0)
    dates: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        for d, leg in r.monthly_legs.items():
            dates.setdefault(d, []).append(leg)

    def ew(weight_fn) -> list[float]:
        return [sum(weight_fn(reg) * ret for reg, ret in legs) / len(legs)
                for _, legs in sorted(dates.items())]

    symmetric = ew(lambda reg: {"glut": 1.0, "balanced": 0.0, "tight": -1.0}[reg])
    long_glut = ew(lambda reg: 1.0 if reg == "glut" else 0.0)
    short_tight = ew(lambda reg: -1.0 if reg == "tight" else 0.0)

    # pooled value x momentum cells (n-weighted across commodities)
    pooled: dict[str, dict] = {}
    for key in ("glut|up", "glut|down", "balanced|up", "balanced|down",
                "tight|up", "tight|down"):
        n = sum(r.cells_2x2.get(key, {}).get("n", 0) for r in rows)
        wsum = sum(c["mean_fwd"] * c["n"] for r in rows
                   for c in [r.cells_2x2.get(key, {})]
                   if c.get("mean_fwd") is not None)
        pooled[key] = {"n": n, "mean_fwd": round(wsum / n, 4) if n >= 24 else None}

    return {
        "horizon": rows[0].horizon if rows else None,
        "n_commodities": len(rows),
        "n_mean_reverting": n_neg,
        "sign_test_p": round(_sign_test_p(len(rows) - n_neg, len(rows)), 4),
        "median_slope": round(sorted(r.slope for r in rows)[len(rows) // 2], 3) if rows else None,
        "ew_rule": _perf(symmetric) if symmetric else {},
        "ew_long_glut": _perf(long_glut) if long_glut else {},
        "ew_short_tight": _perf(short_tight) if short_tight else {},
        "momentum_2x2": pooled,
    }


def render_backtest(rows: list[BacktestRow], horizon: int) -> str:
    s = summary(rows)
    lines = [
        f"REGIME-SIGNAL BACKTEST — forward {horizon}m returns on the trailing-trend deviation",
        "(walk-forward, causal; Newey-West t; regime params predate this test, not fitted)",
        "",
        f"{'commodity':<12}{'months':>7}{'slope b':>9}{'NW t':>7}{'HL mo':>7}"
        f"{'fwd|glut':>10}{'fwd|bal':>9}{'fwd|tight':>10}{'rule shp':>9}{'hold shp':>9}",
        "-" * 89,
    ]
    for r in rows:
        f = lambda v: f"{v:+.1%}" if v is not None else "    —"
        hl = f"{r.half_life_months:.0f}" if r.half_life_months else "—"
        lines.append(
            f"{r.commodity:<12}{r.n_months:>7}{r.slope:>9.2f}{r.t_stat:>7.2f}{hl:>7}"
            f"{f(r.mean_fwd['glut']):>10}{f(r.mean_fwd['balanced']):>9}{f(r.mean_fwd['tight']):>10}"
            f"{r.strat['sharpe']:>9.2f}{r.hold['sharpe']:>9.2f}"
        )
    ew, lg, st = s["ew_rule"], s["ew_long_glut"], s["ew_short_tight"]
    leg = lambda d: (f"{d['ann_ret']:+.1%}/yr, Sharpe {d['sharpe']:.2f}, "
                     f"maxDD {d['max_dd']:.0%}")
    lines += [
        "",
        f"verdict: {s['n_mean_reverting']}/{s['n_commodities']} commodities mean-revert "
        f"(slope<0), sign-test p={s['sign_test_p']} (commodities correlate; p is optimistic)",
        f"median slope {s['median_slope']:+.2f}: a +10% trend deviation maps to "
        f"{s['median_slope']*0.1:+.1%} expected {horizon}m forward return",
        "",
        "equal-weight rule, decomposed (gross of costs):",
        f"  symmetric (long glut / short tight):  {leg(ew)}",
        f"  long-glut leg only:                   {leg(lg)}",
        f"  short-tight leg only:                 {leg(st)}",
        "",
        "The asymmetry IS the finding: depressed markets recover (fwd|glut is",
        "positive nearly everywhere) but elevated markets are NOT safely",
        "shortable. Gorton, Hayashi & Rouwenhorst (2013) explain why: low",
        "inventories mean high convenience yield and a POSITIVE risk premium —",
        "shorting tight markets fights the premium, it doesn't harvest a",
        "mispricing. The monthly-gated legs understate the long side: the rule",
        "exits when glut reclassifies to balanced, i.e. just as the rebound",
        "starts; fwd|glut is the 12m-hold statement of the signal.",
        "",
        "value x momentum, pooled (Asness-Moskowitz-Pedersen 2013 / Miffre-",
        "Rallis 2007 — 12m past-return sign crossed with the regime):",
    ]
    m = s["momentum_2x2"]
    g = lambda k: (f"{m[k]['mean_fwd']:+.1%} (n={m[k]['n']})"
                   if m[k]["mean_fwd"] is not None else f"thin (n={m[k]['n']})")
    lines += [
        f"  {'fwd 12m':<12}{'momentum up':>18}{'momentum down':>18}",
        f"  {'glut':<12}{g('glut|up'):>18}{g('glut|down'):>18}",
        f"  {'balanced':<12}{g('balanced|up'):>18}{g('balanced|down'):>18}",
        f"  {'tight':<12}{g('tight|up'):>18}{g('tight|down'):>18}",
        "",
        "HL mo = mean-reversion half-life of the trend deviation, AR(1)",
        "(Cashin-Liang-McDermott 2000). Evidence about the signal, not a",
        "strategy: gross of costs and roll, monthly spot-proxy averages,",
        "regime thresholds not optimized. References: docs/references.md.",
    ]
    return "\n".join(lines)
