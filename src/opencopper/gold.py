"""Gold gets its own mechanism: the real-rate model.

Flow-shock incidence is the WRONG model for gold (price set by monetary
demand against a 200,000+ tonne above-ground stock), so gold is excluded
from shock pricing. But "excluded" is not a model. The literature one is:
gold is a zero-coupon real asset, so its price moves inversely with long
real yields (Barsky & Summers 1988; Erb & Harvey 2013's "golden dilemma").

Keyless implementation: monthly gold returns from the BLS nonmonetary-gold
export price index (FRED IQ12260) regressed on monthly CHANGES in the 10y
real rate (Cleveland Fed REAINTRATREARAT10Y), Newey-West errors. Output is
a sensitivity — "x% per +100bp real yield" — plus where real rates are now
and what the last year of rate moves alone would have implied. A beta and
an R², not a price target; gold remains outside the scenario engine."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .pricing import cached_fred

GOLD_SERIES = "IQ12260"
REAL_RATE_SERIES = "REAINTRATREARAT10Y"


@dataclass
class GoldModel:
    n_months: int
    beta_per_100bp: float   # % gold move per +100bp real-rate change
    t_stat: float
    r2: float
    rate_now: float
    rate_date: str
    rate_change_12m: float  # pp
    implied_12m_pct: float  # beta * rate change — rates' contribution alone


def gold_rate_model() -> GoldModel | None:
    try:
        gold = dict(cached_fred(GOLD_SERIES))
        rate = dict(cached_fred(REAL_RATE_SERIES))
    except Exception:
        return None
    common = sorted(set(gold) & set(rate))
    if len(common) < 120:
        return None
    dg, dr = [], []
    for i in range(1, len(common)):
        a, b = common[i - 1], common[i]
        if gold[a] > 0 and gold[b] > 0:
            dg.append(100 * math.log(gold[b] / gold[a]))  # % move
            dr.append(rate[b] - rate[a])                  # pp change
    n = len(dg)
    mr, mg = sum(dr) / n, sum(dg) / n
    sxx = sum((r - mr) ** 2 for r in dr)
    if sxx == 0:
        return None
    beta = sum((dr[i] - mr) * (dg[i] - mg) for i in range(n)) / sxx
    alpha = mg - beta * mr
    resid = [dg[i] - alpha - beta * dr[i] for i in range(n)]
    ss_res = sum(e * e for e in resid)
    ss_tot = sum((g - mg) ** 2 for g in dg)
    # NW(3) se on the slope
    u = [(dr[i] - mr) * resid[i] for i in range(n)]
    s = sum(v * v for v in u)
    for l in range(1, 4):
        w = 1 - l / 4
        s += 2 * w * sum(u[i] * u[i + l] for i in range(n - l))
    se = math.sqrt(max(s, 1e-18)) / sxx

    last = common[-1]
    yr_ago = common[-13] if len(common) >= 13 else common[0]
    d12 = rate[last] - rate[yr_ago]
    return GoldModel(
        n_months=n,
        beta_per_100bp=round(beta, 1),
        t_stat=round(beta / se, 2),
        r2=round(1 - ss_res / ss_tot, 3),
        rate_now=round(rate[last], 2),
        rate_date=last,
        rate_change_12m=round(d12, 2),
        implied_12m_pct=round(beta * d12, 1),
    )


def render_gold_model(m: GoldModel) -> str:
    direction = "tailwind" if m.implied_12m_pct > 0 else "headwind"
    return "\n".join([
        "GOLD — the real-rate model (flow-shock incidence is the wrong tool here)",
        f"  beta: {m.beta_per_100bp:+.1f}% per +100bp 10y real yield "
        f"(NW t {m.t_stat:.1f}, R² {m.r2:.0%}, {m.n_months} months)",
        f"  10y real rate now: {m.rate_now:.2f}% ({m.rate_date[:7]}), "
        f"12m change {m.rate_change_12m:+.2f}pp",
        f"  rates' 12m contribution alone: {m.implied_12m_pct:+.1f}% ({direction})",
        "  A sensitivity, not a target: the residual (~"
        f"{100 - 100 * m.r2:.0f}% of variance) is flows, fear, and FX — unmodeled.",
        "  Sources: Barsky-Summers (1988); Erb-Harvey (2013). Series: BLS IQ12260,",
        "  Cleveland Fed REAINTRATREARAT10Y — both keyless.",
    ])
