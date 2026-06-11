"""Cross-commodity propagation: shocks don't stop at one market.

Three typed links (data/seed/linkages.yaml):
- BYPRODUCT: a supply shock to the host commodity in a host country drags the
  dependent's supply (cobalt rides DRC copper; silver rides zinc).
- SUBSTITUTION: a sustained price rise in one metal shifts demand to another.
- INPUT_COST: an input's price passes through to an output's price.

Propagation is ONE first-order round, deliberately: second-round effects are
smaller than the couplings' uncertainty, and a fixed point would imply
precision the seed couplings don't have.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .pricing import load_pricebook, price_impact_from_demand, price_impact_from_shock

LINKAGES_PATH = Path(__file__).resolve().parents[2] / "data" / "seed" / "linkages.yaml"


@dataclass
class RippleRow:
    commodity: str
    channel: str            # direct | byproduct | substitution | input_cost
    via: str
    supply_shock: float     # fraction of world supply withdrawn (if supply-side)
    demand_shift: float     # fraction demand change (if demand-side)
    price_change_pct: float
    clamped: bool


def load_linkages() -> list[dict]:
    return yaml.safe_load(LINKAGES_PATH.read_text())["linkages"]


def ripple(commodity: str, country: str | None, severity: float) -> list[RippleRow]:
    """First-order cross-commodity impacts of a country supply shock."""
    from .commodities import load_commodity

    book = load_pricebook()
    links = load_linkages()
    rows: list[RippleRow] = []

    seed = load_commodity(commodity)
    k_direct = (seed.share(country) if country else 1.0) * severity
    direct = price_impact_from_shock(book.commodities[commodity], k_direct)
    rows.append(RippleRow(commodity, "direct", country or "world", k_direct, 0.0,
                          direct.price_change_pct, direct.clamped))

    # byproduct: dependent loses supply where it co-occurs with the host
    for ln in links:
        if ln["type"] == "byproduct" and ln["host"] == commodity:
            if ln.get("host_country") and country and ln["host_country"] != country:
                continue
            dep = load_commodity(ln["dependent"])
            dep_country_share = 0.0
            if country:
                try:
                    dep_country_share = dep.share(country)
                except KeyError:
                    continue
            else:
                dep_country_share = 1.0
            k_dep = ln["coupling"] * severity * dep_country_share
            if k_dep <= 0.001:
                continue
            impact = price_impact_from_shock(book.commodities[ln["dependent"]], k_dep)
            rows.append(RippleRow(ln["dependent"], "byproduct", f"{commodity}@{country or 'world'}",
                                  k_dep, 0.0, impact.price_change_pct, impact.clamped))

    # substitution + input cost: second round off the DIRECT price move
    dP = direct.price_change_pct / 100
    for ln in links:
        if ln["type"] == "substitution" and ln["from"] == commodity:
            d_shift = ln["elasticity"] * dP
            if abs(d_shift) < 0.002:
                continue
            impact = price_impact_from_demand(book.commodities[ln["to"]], d_shift)
            rows.append(RippleRow(ln["to"], "substitution", f"{commodity} price {dP:+.0%}",
                                  0.0, d_shift, impact.price_change_pct, impact.clamped))
        elif ln["type"] == "input_cost" and ln["input"] == commodity:
            p_out = ln["passthrough"] * direct.price_change_pct
            if abs(p_out) < 0.2:
                continue
            rows.append(RippleRow(ln["output"], "input_cost", f"{commodity} price {dP:+.0%}",
                                  0.0, 0.0, round(p_out, 1), False))
    rows.sort(key=lambda r: -abs(r.price_change_pct))
    return rows


def render_ripple(rows: list[RippleRow], title: str) -> str:
    lines = [f"CROSS-COMMODITY RIPPLE — {title}",
             f"{'commodity':<13}{'channel':<14}{'via':<26}{'price Δ':>10}", "-" * 64]
    for r in rows:
        bound = ("≥" if r.price_change_pct > 0 else "≤") if r.clamped else ""
        lines.append(f"{r.commodity:<13}{r.channel:<14}{r.via[:25]:<26}{bound}{r.price_change_pct:>+9.0f}%")
    lines.append("\nOne first-order round through data/seed/linkages.yaml (byproduct /")
    lines.append("substitution / input-cost); couplings are disputable seed-estimates.")
    return "\n".join(lines)
