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
