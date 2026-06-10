"""Calibrate the simulator against real price history.

The test of a world model is whether the world it generates is as volatile as
the real one. We hold the disruption MEAN fixed at its physical value (~5% of
mine supply, which the market already expects and prices in) and search the
disruption dispersion + demand-surprise size so that simulated annual price
volatility matches the realized ~22% copper has shown since 1992.

A model that has to be told the answer isn't validated — but a model whose one
free dispersion knob, once tuned, reproduces three decades of realized
volatility is doing real work. The calibrated value is the default in
montecarlo.DisruptionParams; this module re-derives it on demand.
"""

from __future__ import annotations

from dataclasses import dataclass

from .balance import BASELINE
from .history import load_price_history
from .montecarlo import DisruptionParams, simulate_copper


@dataclass
class CalibrationResult:
    target_vol: float
    achieved_vol: float
    disruption_cv: float
    demand_sigma: float
    iterations: int


def calibrate_copper(
    target_vol: float | None = None,
    *,
    n_paths: int = 1500,
    seed: int = 7,
    tol: float = 0.01,
    max_iter: int = 18,
) -> CalibrationResult:
    """Bisection on a dispersion scale so simulated vol == realized vol.

    A single scale multiplies both the disruption CV and the demand sigma, so
    one knob controls overall surprise size. Monotone in the scale, so bisection
    converges fast.
    """
    if target_vol is None:
        hist = load_price_history("copper")
        target_vol = hist.annual_volatility if hist else 0.22

    base_cv, base_sigma = 0.30, 0.015  # shape of the surprise; scale tunes magnitude

    def vol_at(scale: float) -> float:
        params = DisruptionParams(
            disruption_mean=0.05,
            disruption_cv=base_cv * scale,
            demand_sigma=base_sigma * scale,
        )
        return simulate_copper(BASELINE, n_paths=n_paths, params=params, seed=seed).simulated_annual_vol

    lo, hi = 0.1, 4.0
    achieved = vol_at(1.0)
    iterations = 0
    for iterations in range(1, max_iter + 1):
        mid = (lo + hi) / 2
        achieved = vol_at(mid)
        if abs(achieved - target_vol) <= tol:
            break
        if achieved < target_vol:
            lo = mid
        else:
            hi = mid
    scale = (lo + hi) / 2
    return CalibrationResult(
        target_vol=round(target_vol, 4),
        achieved_vol=round(achieved, 4),
        disruption_cv=round(base_cv * scale, 4),
        demand_sigma=round(base_sigma * scale, 4),
        iterations=iterations,
    )


def render_calibration(c: CalibrationResult) -> str:
    return "\n".join([
        "CALIBRATION — copper simulator vs realized price history",
        f"  target (realized annual vol):  {c.target_vol:.1%}",
        f"  achieved (simulated):          {c.achieved_vol:.1%}   ({c.iterations} bisection steps)",
        f"  => disruption CV:              {c.disruption_cv:.3f}",
        f"  => demand surprise sigma:      {c.demand_sigma:.3f}",
        "  disruption mean held at 5% (physical; the market expects it).",
    ])
