"""Monte Carlo simulation — the engine that makes this a real world model.

The deterministic engine answers "what does this exact scenario do." The real
world is uncertain: mines fail at random, demand surprises, and the convex
scarcity curve turns small physical surprises into large price moves. The
simulator draws thousands of futures and returns DISTRIBUTIONS — P10/P50/P90
bands, probability of deficit, probability of a price spike — on top of any
deterministic scenario.

Two stochastic drivers, both calibrated against history (see calibrate.py):

- **Supply disruptions.** Each year an aggregate fraction of mine supply is
  lost, drawn from a Gamma distribution with mean ≈ the historical disruption
  rate (~5% for copper). Gamma is right-skewed: most years are calm, some are
  bad — like real mine supply.
- **Demand surprises.** A small symmetric annual shock (recessions, restocking)
  drawn Normal.

The disruption mean is an honest physical number; the dispersion is the single
knob tuned so simulated price volatility matches the ~22% copper has actually
realized since 1992. Everything is seeded for exact reproducibility.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .balance import run
from .ledger import Assumptions, Ledger, load_assumptions, load_ledger
from .pricing import CoverCurve, copper_price_from_cover, load_pricebook
from .shocks import Scenario


@dataclass(frozen=True)
class DisruptionParams:
    """Stochastic-driver parameters. Defaults are CALIBRATED for copper
    (calibrate.py bisection) so simulated annual price volatility (21.7%)
    matches the realized 21.9% in FRED history since 1992. The disruption mean
    is the physical ~5% the market already expects; the dispersion is the tuned
    knob."""

    disruption_mean: float = 0.05   # mean fraction of mine supply lost per year
    disruption_cv: float = 0.185    # calibrated to realized copper price vol (22%)
    demand_sigma: float = 0.009     # calibrated std of annual demand surprise
    # Surprises persist: a bad disruption year tends to bleed into the next
    # (recoveries take time) and demand shocks are business cycles, not coin
    # flips. AR(1) on both drivers gives the clustered deficits and fatter
    # tails real markets show. Sigmas above are the STATIONARY magnitudes;
    # innovations are scaled by sqrt(1-rho^2).
    disruption_rho: float = 0.35
    demand_rho: float = 0.5

    def gamma_params(self) -> tuple[float, float]:
        # Gamma(shape k, scale θ): mean = kθ, CV = 1/√k
        k = 1.0 / (self.disruption_cv ** 2)
        theta = self.disruption_mean / k
        return k, theta


@dataclass
class Band:
    """A percentile band for one variable across all paths, by year."""

    years: list[int]
    p10: list[float]
    p50: list[float]
    p90: list[float]
    mean: list[float]


@dataclass
class MonteCarloResult:
    commodity: str
    scenario: str
    n_paths: int
    years: list[int]
    balance: Band
    cover_days: Band
    price: Band
    prob_deficit: dict[int, float]       # P(refined balance < 0) by year
    prob_price_spike: dict[int, float]    # P(price > 1.5x anchor) by year
    simulated_annual_vol: float           # for the calibration report
    price_paths_sample: list[list[float]] = field(default_factory=list)


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _band(years: list[int], paths: list[list[float]]) -> Band:
    """paths[i] is one path's value per year; transpose to per-year columns."""
    cols = list(zip(*paths)) if paths else []
    p10, p50, p90, mean = [], [], [], []
    for col in cols:
        s = sorted(col)
        p10.append(round(_percentile(s, 0.10), 1))
        p50.append(round(_percentile(s, 0.50), 1))
        p90.append(round(_percentile(s, 0.90), 1))
        mean.append(round(sum(col) / len(col), 1))
    return Band(years=years, p10=p10, p50=p50, p90=p90, mean=mean)


def _annual_vol(price_paths: list[list[float]]) -> float:
    """Mean across paths of each path's std of year-over-year log price change."""
    import math

    vols = []
    for path in price_paths:
        rets = [
            math.log(path[i] / path[i - 1])
            for i in range(1, len(path))
            if path[i - 1] > 0 and path[i] > 0
        ]
        if len(rets) >= 2:
            mu = sum(rets) / len(rets)
            vols.append(math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)))
    return round(sum(vols) / len(vols), 4) if vols else 0.0


def simulate_copper(
    scenario: Scenario,
    *,
    n_paths: int = 2000,
    params: DisruptionParams = DisruptionParams(),
    seed: int = 12345,
    years: range = range(2024, 2031),
    ledger: Optional[Ledger] = None,
    assumptions: Optional[Assumptions] = None,
    curve: Optional[CoverCurve] = None,
) -> MonteCarloResult:
    ledger = ledger or load_ledger()
    base_assumptions = assumptions or load_assumptions()
    curve = curve or load_pricebook().copper_cover_curve
    rng = random.Random(seed)
    k, theta = params.gamma_params()
    mean_disruption = params.disruption_mean

    # The deterministic baseline already carries the EXPECTED disruption (the 5%
    # allowance on rest-of-world supply). Monte Carlo adds the mean-zero SURPRISE
    # around that expectation, so the MC median tracks the deterministic baseline
    # and only the spread is stochastic. (Zeroing the allowance and applying the
    # full draw would double-count — and the allowance hits only RoW, not the
    # tracked mines a blanket multiplier would also dock.)
    mc_assumptions = base_assumptions
    year_list = list(years)
    anchor = curve.anchor_usd_t

    balance_paths, cover_paths, price_paths = [], [], []
    deficit_hits = {y: 0 for y in year_list}
    spike_hits = {y: 0 for y in year_list}

    s_rho, d_rho = params.disruption_rho, params.demand_rho
    s_innov = (1 - s_rho**2) ** 0.5
    d_innov = (1 - d_rho**2) ** 0.5
    for _ in range(n_paths):
        supply_mult, demand_mult = {}, {}
        s_state = 0.0  # AR(1) state of the disruption SURPRISE (mean-zero)
        d_state = 0.0
        for y in year_list:
            draw = min(0.6, rng.gammavariate(k, theta))  # cap a runaway tail at 60%
            innovation = draw - mean_disruption  # >0 = worse than expected
            s_state = s_rho * s_state + s_innov * innovation
            d_state = d_rho * d_state + d_innov * rng.gauss(0.0, params.demand_sigma)
            supply_mult[y] = min(1.10, max(0.5, 1.0 - s_state))
            demand_mult[y] = 1.0 + d_state
        # Monte Carlo runs WITHOUT the lagged price feedback: this model's thin
        # inventory gives the cover->price curve such high gain that a lagged
        # demand/scrap loop cobwebs into oscillation (loop gain > 1). Volatility
        # here comes honestly from the disruption + demand-surprise draws, which
        # calibrate to realized history on their own. (Feedback remains a stable
        # deterministic opt-in for smooth scenarios — see run(feedback=True).)
        rr = run(
            ledger, mc_assumptions, scenario, years, supply_mult, demand_mult,
            curve=curve, feedback=False,
        )
        bvals, cvals, pvals = [], [], []
        for row in rr.rows:
            price = row.implied_price_usd
            bvals.append(row.refined_balance_kt)
            cvals.append(row.inventory_days)
            pvals.append(price)
            if row.refined_balance_kt < 0:
                deficit_hits[row.year] += 1
            if price > 1.5 * anchor:
                spike_hits[row.year] += 1
        balance_paths.append(bvals)
        cover_paths.append(cvals)
        price_paths.append(pvals)

    return MonteCarloResult(
        commodity="copper",
        scenario=scenario.name,
        n_paths=n_paths,
        years=year_list,
        balance=_band(year_list, balance_paths),
        cover_days=_band(year_list, cover_paths),
        price=_band(year_list, price_paths),
        prob_deficit={y: round(deficit_hits[y] / n_paths, 3) for y in year_list},
        prob_price_spike={y: round(spike_hits[y] / n_paths, 3) for y in year_list},
        simulated_annual_vol=_annual_vol(price_paths),
        price_paths_sample=price_paths[:50],
    )


# ------------------------------------------------- country-tier commodity MC


@dataclass
class CommodityMCResult:
    commodity: str
    scenario: str
    n_paths: int
    years: list[int]
    price: Band
    prob_double: dict[int, float]   # P(price >= 2x anchor)
    prob_halve: dict[int, float]    # P(price <= 0.5x anchor)
    simulated_annual_vol: float
    target_vol: float
    vol_source: str


def simulate_commodity(
    commodity: str,
    scenario=None,
    *,
    n_paths: int = 2000,
    seed: int = 12345,
    years: range = range(2025, 2031),
):
    """Path simulation for a country-tier commodity.

    Mechanics match the tier's honest scope: no inventory state, so the price
    each year is the CES-incidence response to that year's NET TIGHTENING
    x_t = (scenario supply loss) + (scenario demand change) + noise, where the
    noise sigma is derived in closed form from the commodity's realized annual
    volatility: for small x the price return is (x_t - x_{t-1})/(η_d+η_s), so
    iid noise with sigma_g = vol x (η_d+η_s) / sqrt(2) reproduces the realized
    vol — calibration by construction, verified in tests. Returns None for
    commodities excluded from shock pricing (gold).
    """
    from .commodities import (
        CountrySupplyShock,
        GlobalDemandShock,
        load_commodity,
    )
    from .history import ambient_volatility
    from .pricing import INCIDENCE_CLAMP, load_pricebook

    book = load_pricebook()
    price_cfg = book.commodities.get(commodity)
    if not price_cfg or price_cfg.excluded_from_shock_pricing:
        return None
    seed_data = load_commodity(commodity)
    vol, vol_source = ambient_volatility(commodity)
    denom = price_cfg.elasticity_demand + price_cfg.elasticity_supply
    sigma_g = vol * denom / (2 ** 0.5)
    # the closed form is the CES map's tangent at zero; for very-high-vol
    # names the ln(1-k) convexity inflates realized dispersion a few pp.
    # One deterministic Newton rescale (seeded pre-sim) enforces the
    # calibration contract exactly — a uniform rule, never per-name tuning.
    if vol * denom > 0.2:
        import math as _m

        pre = random.Random(seed ^ 0x5EED)
        rets, prev = [], None
        for _ in range(4000):
            x = min(pre.gauss(0.0, sigma_g), 0.95)
            mult = _m.exp(-_m.log(max(1e-6, 1.0 - x)) / denom) if denom else 1.0
            mult = min(max(mult, INCIDENCE_CLAMP[0]), INCIDENCE_CLAMP[1])
            if prev is not None:
                rets.append(_m.log(mult / prev))
            prev = mult
        mu = sum(rets) / len(rets)
        sim_vol = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        if sim_vol > 0 and abs(sim_vol - vol) > 0.01:
            sigma_g *= vol / sim_vol
    rng = random.Random(seed)
    year_list = list(years)
    anchor = price_cfg.anchor_usd
    lo, hi = INCIDENCE_CLAMP

    # deterministic tightening per year from the scenario
    base_tightening = {}
    for y in year_list:
        k = 0.0
        d_mult = 1.0
        if scenario:
            for e in scenario.events:
                if isinstance(e, CountrySupplyShock) and e.active(y):
                    k += seed_data.share(e.country) * e.severity
                elif isinstance(e, GlobalDemandShock):
                    d_mult *= e.multiplier(y)
        base_tightening[y] = k + (d_mult - 1.0)

    import math

    price_paths: list[list[float]] = []
    double_hits = {y: 0 for y in year_list}
    halve_hits = {y: 0 for y in year_list}
    for _ in range(n_paths):
        path = []
        for y in year_list:
            x = base_tightening[y] + rng.gauss(0.0, sigma_g)
            x = min(x, 0.95)  # cannot withdraw more than the market
            multiple = math.exp(-math.log(max(1e-6, 1.0 - x)) / denom) if denom else 1.0
            multiple = min(max(multiple, lo), hi)
            price = anchor * multiple
            path.append(round(price, 1))
            if multiple >= 2.0:
                double_hits[y] += 1
            if multiple <= 0.5:
                halve_hits[y] += 1
        price_paths.append(path)

    return CommodityMCResult(
        commodity=commodity,
        scenario=scenario.name if scenario else "baseline",
        n_paths=n_paths,
        years=year_list,
        price=_band(year_list, price_paths),
        prob_double={y: round(double_hits[y] / n_paths, 3) for y in year_list},
        prob_halve={y: round(halve_hits[y] / n_paths, 3) for y in year_list},
        simulated_annual_vol=_annual_vol(price_paths),
        target_vol=vol,
        vol_source=vol_source,
    )


def render_montecarlo(mc: MonteCarloResult) -> str:
    lines = [
        f"MONTE CARLO — {mc.commodity}, scenario '{mc.scenario}', {mc.n_paths:,} paths",
        f"simulated annual price volatility: {mc.simulated_annual_vol:.1%}",
        "",
        f"{'year':<6}{'bal P50':>9}{'bal P10':>9}{'price P10':>11}{'price P50':>11}"
        f"{'price P90':>11}{'P(def)':>8}{'P(spike)':>9}",
    ]
    for i, y in enumerate(mc.years):
        lines.append(
            f"{y:<6}{mc.balance.p50[i]:>9,.0f}{mc.balance.p10[i]:>9,.0f}"
            f"{mc.price.p10[i]:>11,.0f}{mc.price.p50[i]:>11,.0f}{mc.price.p90[i]:>11,.0f}"
            f"{mc.prob_deficit[y]:>8.0%}{mc.prob_price_spike[y]:>9.0%}"
        )
    lines.append("\nP(def) = probability of a refined deficit; P(spike) = probability price > 1.5x anchor.")
    return "\n".join(lines)
