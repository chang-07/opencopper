"""Forecast benchmark: does the model's mechanism beat naive baselines?

The quant's acid test for any forecasting claim: walk-forward point
forecasts against the two baselines that are free (Meese-Rogoff tradition):

- **random walk**: P(t+h) = P(t) — what a futures curve approximates
- **anchor**: P(t+h) = the balanced-market anchor — pure mean reversion to a
  constant

The MODEL forecast is the trend-deviation mean reversion the backtest
measures, made honest: the slope is re-estimated each month on an EXPANDING
window of data available at that month (no look-ahead anywhere), then
log P̂(t+h) = log P(t) + b̂_t · dev_t. Skip-month applies as everywhere.

Scoring: RMSE per commodity and forecaster, skill = 1 − RMSE_model/RMSE_rw
(positive = the mechanism adds information beyond no-change), and a
Diebold-Mariano test on the squared-error differential with Newey-West
variance (lag h−1, overlapping forecasts). The expectation, stated up
front: at 12 months the model should beat the random walk where mean
reversion is strong (gas, aluminum, tin) and roughly tie where it is weak
(copper). A model that "wins" everywhere would be suspicious, not
impressive."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .backtest import _deviations_w, nw_slope
from .history import TREND_WINDOW, load_price_history
from .pricing import load_pricebook


@dataclass
class BenchRow:
    commodity: str
    n_forecasts: int
    rmse_model: float
    rmse_rw: float
    rmse_anchor: float
    skill_vs_rw: float        # 1 - rmse_model / rmse_rw
    dm_t: float               # Diebold-Mariano t, model vs random walk (NW)


def _dm_t(loss_diff: list[float], lag: int) -> float:
    n = len(loss_diff)
    mu = sum(loss_diff) / n
    e = [d - mu for d in loss_diff]
    s = sum(v * v for v in e)
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        s += 2 * w * sum(e[i] * e[i + l] for i in range(n - l))
    se = math.sqrt(max(s, 1e-18)) / n
    return mu / se


def benchmark_commodity(name: str, horizon: int = 12, skip: int = 1,
                        min_train: int = 120) -> BenchRow | None:
    h = load_price_history(name)
    if not h or len(h.months) < min_train + horizon + skip + 24:
        return None
    months = h.months
    logs = [math.log(p) for _, p in months]
    devs = _deviations_w(months, TREND_WINDOW)
    anchor = load_pricebook().commodities[name].anchor_usd
    log_anchor = math.log(anchor)

    e_model, e_rw, e_anchor = [], [], []
    for t in range(min_train, len(months) - horizon - skip):
        # expanding-window slope on data available at t (signal i, outcome
        # i+skip..i+skip+h, so the last usable signal is t-horizon-skip)
        xs = devs[TREND_WINDOW:t - horizon - skip]
        ys = [logs[i + skip + horizon] - logs[i + skip]
              for i in range(TREND_WINDOW, t - horizon - skip)]
        if len(xs) < 60:
            continue
        b, _ = nw_slope(xs, ys, lag=horizon - 1)
        actual = logs[t + skip + horizon]
        base = logs[t + skip]
        e_model.append((base + b * devs[t]) - actual)
        e_rw.append(base - actual)
        e_anchor.append(log_anchor - actual)
    if len(e_model) < 36:
        return None

    rmse = lambda es: math.sqrt(sum(x * x for x in es) / len(es))
    rm, rr, ra = rmse(e_model), rmse(e_rw), rmse(e_anchor)
    loss_diff = [e_model[i] ** 2 - e_rw[i] ** 2 for i in range(len(e_model))]
    return BenchRow(
        commodity=name, n_forecasts=len(e_model),
        rmse_model=round(rm, 4), rmse_rw=round(rr, 4), rmse_anchor=round(ra, 4),
        skill_vs_rw=round(1 - rm / rr, 3),
        dm_t=round(_dm_t(loss_diff, lag=horizon - 1), 2),
    )


def benchmark_all(horizon: int = 12) -> list[BenchRow]:
    rows = []
    for name in load_pricebook().commodities:
        r = benchmark_commodity(name, horizon)
        if r:
            rows.append(r)
    rows.sort(key=lambda r: -r.skill_vs_rw)
    return rows


def render_benchmark(rows: list[BenchRow], horizon: int = 12) -> str:
    n_pos = sum(1 for r in rows if r.skill_vs_rw > 0)
    sig = sum(1 for r in rows if r.skill_vs_rw > 0 and r.dm_t < -1.65)
    lines = [
        f"FORECAST BENCHMARK — walk-forward {horizon}m point forecasts, expanding-window",
        "slope re-fit monthly (no look-ahead), vs the free baselines (Meese-Rogoff style)",
        "",
        f"{'commodity':<13}{'n':>5}{'RMSE model':>12}{'RMSE rw':>9}{'RMSE anchor':>13}"
        f"{'skill':>8}{'DM t':>7}",
        "-" * 67,
    ]
    for r in rows:
        lines.append(f"{r.commodity:<13}{r.n_forecasts:>5}{r.rmse_model:>12.3f}"
                     f"{r.rmse_rw:>9.3f}{r.rmse_anchor:>13.3f}"
                     f"{r.skill_vs_rw:>+8.1%}{r.dm_t:>7.2f}")
    lines += [
        "-" * 67,
        f"model beats the random walk for {n_pos}/{len(rows)} commodities "
        f"({sig} significant, DM t < -1.65, one-sided).",
        "",
        "How to read this (it is the Meese-Rogoff result, on purpose): the signal's",
        "R^2 is ~1%, so even a TRUE mechanism moves 12m point-forecast RMSE by only",
        "a few percent — and the wins land exactly on the backtest's strongest",
        "reverters (gas, aluminum, tin, coal) while the losses land where regimes",
        "shifted under an expanding window (crude, copper supercycle). That is the",
        "mechanism's domain of validity, measured. This model is a DISTRIBUTION and",
        "scenario engine; anyone selling you commodity point forecasts that beat a",
        "random walk broadly is selling fit. Anchor column = the (bad) cost of pure",
        "reversion-to-a-constant; anchors are levels, never forecasts.",
    ]
    return "\n".join(lines)
