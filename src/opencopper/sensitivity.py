"""One-at-a-time sensitivity analysis: which assumption moves the balance most?

The standard commodities-desk tornado: perturb each world assumption up and
down by a stated step, rerun the engine, rank by swing. Because every input is
an explicit field on the Assumptions model, the sweep is just attribute paths —
no hidden constants can escape the tornado.
"""

from __future__ import annotations

from dataclasses import dataclass

from .balance import BASELINE, run
from .ledger import Assumptions, Ledger, load_assumptions, load_ledger
from .shocks import Scenario

# (dotted path, +/- step, human label). Steps are deliberately "a plausible
# forecasting miss", not symmetric percentages of the value.
SWEEPS: list[tuple[str, float, str]] = [
    ("demand.base_kt_2024", 268.0, "demand level (±1%)"),
    ("demand.sectors.electrical_grid.growth_pct", 0.5, "grid demand growth (±0.5pp)"),
    ("demand.sectors.transport.growth_pct", 1.0, "EV/transport demand growth (±1pp)"),
    ("demand.sectors.construction.growth_pct", 0.5, "construction demand growth (±0.5pp)"),
    ("demand.sectors.datacenters.growth_pct", 5.0, "datacenter demand growth (±5pp)"),
    ("world.mine_supply_kt_2024", 229.0, "mine supply level (±1%)"),
    ("world.mine_supply_growth_pct", 0.5, "mine supply growth (±0.5pp)"),
    ("world.disruption_allowance_pct", 1.0, "disruption allowance (±1pp)"),
    ("refined.secondary_supply_kt_2024", 225.0, "scrap supply level (±5%)"),
    ("refined.secondary_growth_pct", 1.5, "scrap growth (±1.5pp)"),
    ("smelting.utilization_max", 0.02, "smelter utilization (±2pp)"),
    ("world.sxew_share_world", 0.02, "SX-EW share of mine supply (±2pp)"),
]


@dataclass
class SensitivityRow:
    param: str
    label: str
    low: float    # balance with param - step
    base: float
    high: float   # balance with param + step
    swing: float  # |high - low|


def _navigate(root, dotted: str):
    """Return (parent, final_key) for a dotted path across pydantic models and dicts."""
    parts = dotted.split(".")
    node = root
    for part in parts[:-1]:
        node = node[part] if isinstance(node, dict) else getattr(node, part)
    return node, parts[-1]


def _get(root, dotted: str) -> float:
    parent, key = _navigate(root, dotted)
    return parent[key] if isinstance(parent, dict) else getattr(parent, key)


def _set(root, dotted: str, value: float) -> None:
    parent, key = _navigate(root, dotted)
    if isinstance(parent, dict):
        parent[key] = value
    else:
        setattr(parent, key, value)


def run_sensitivity(
    year: int = 2026,
    scenario: Scenario | None = None,
    ledger: Ledger | None = None,
    assumptions: Assumptions | None = None,
    sweeps: list[tuple[str, float, str]] | None = None,
) -> list[SensitivityRow]:
    scenario = scenario or BASELINE
    ledger = ledger or load_ledger()
    base_assumptions = assumptions or load_assumptions()
    years = range(2024, year + 1)

    def balance(a: Assumptions) -> float:
        return run(ledger, a, scenario, years).row(year).refined_balance_kt

    base = balance(base_assumptions)
    rows: list[SensitivityRow] = []
    for path, step, label in sweeps or SWEEPS:
        perturbed = []
        for direction in (-1, +1):
            a = base_assumptions.model_copy(deep=True)
            _set(a, path, _get(a, path) + direction * step)
            perturbed.append(balance(a))
        low, high = perturbed
        rows.append(SensitivityRow(path, label, low, base, high, abs(high - low)))
    rows.sort(key=lambda r: -r.swing)
    return rows


PRICE_SWEEPS: list[tuple[str, float, str]] = [
    ("gamma", 0.15, "cover-curve gamma (±0.15)"),
    ("anchor_usd_t", 920.0, "anchor price (±10%)"),
    ("baseline_days", 2.0, "baseline inventory cover (±2 days)"),
]
FEEDBACK_SWEEPS: list[tuple[str, float, str]] = [
    ("feedback_demand_elasticity", 0.15, "feedback demand elasticity (±0.15)"),
    ("feedback_scrap_elasticity", 0.25, "feedback scrap elasticity (±0.25)"),
    ("feedback_adjustment", 0.15, "feedback adjustment speed (±0.15)"),
]


def run_price_sensitivity(year: int = 2026) -> list[SensitivityRow]:
    """Tornado on the PRICING layer: how much do the judgment-calibrated price
    parameters move the implied price under the world-2026 composite? This is
    the quantified answer to 'gamma is hand-set' — the uncertainty is shown,
    not hidden."""
    from .pricing import load_pricebook
    from .scenario import SCENARIO_DIR, load_scenario

    ledger = load_ledger()
    assumptions = load_assumptions()
    scenario = load_scenario(SCENARIO_DIR / "world-2026.yaml")
    book = load_pricebook()
    years = range(2024, year + 1)

    def implied(curve, **feedback_kwargs) -> float:
        feedback = bool(feedback_kwargs)
        rr = run(ledger, assumptions, scenario, years, curve=curve,
                 feedback=feedback, **feedback_kwargs)
        return rr.row(year).implied_price_usd

    rows: list[SensitivityRow] = []
    base = implied(book.copper_cover_curve)
    for attr, step, label in PRICE_SWEEPS:
        vals = []
        for direction in (-1, +1):
            curve = book.copper_cover_curve.model_copy(deep=True)
            setattr(curve, attr, getattr(curve, attr) + direction * step)
            vals.append(implied(curve))
        rows.append(SensitivityRow(attr, label, vals[0], base, vals[1], abs(vals[1] - vals[0])))

    fb_base = implied(book.copper_cover_curve, feedback_demand_elasticity=0.30,
                      feedback_scrap_elasticity=0.50, feedback_adjustment=0.30)
    defaults = {"feedback_demand_elasticity": 0.30, "feedback_scrap_elasticity": 0.50,
                "feedback_adjustment": 0.30}
    for attr, step, label in FEEDBACK_SWEEPS:
        vals = []
        for direction in (-1, +1):
            kwargs = dict(defaults)
            kwargs[attr] = max(0.01, kwargs[attr] + direction * step)
            vals.append(implied(book.copper_cover_curve, **kwargs))
        rows.append(SensitivityRow(attr, label + " [fb run]", vals[0], fb_base, vals[1],
                                   abs(vals[1] - vals[0])))
    rows.sort(key=lambda r: -r.swing)
    return rows


def render_tornado(
    rows: list[SensitivityRow], year: int, scenario_name: str,
    quantity: str = "refined balance (kt)",
) -> str:
    width = 26
    max_swing = max(r.swing for r in rows) or 1.0
    lines = [
        f"sensitivity of {year} {quantity} — scenario: {scenario_name}",
        f"{'assumption':<38}{'-step':>9}{'base':>9}{'+step':>9}{'swing':>8}",
        "-" * (38 + 9 + 9 + 9 + 8 + 2 + width),
    ]
    for r in rows:
        bar = "#" * max(1, round(width * r.swing / max_swing))
        lines.append(
            f"{r.label:<38}{r.low:>9.0f}{r.base:>9.0f}{r.high:>9.0f}{r.swing:>8.0f}  {bar}"
        )
    return "\n".join(lines)
