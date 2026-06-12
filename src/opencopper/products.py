"""Products: the commodity model pointed at things people actually buy.

A product seed is a bill of materials — input quantities in the pricebook's
own units — plus an anchor cost. Three questions, answered with the same
machinery as everything else:

1. **Cost structure** — what share of this product's bottom line is each
   commodity at balanced-market anchors? (Cable ~80% copper; bread ~5%
   wheat. The spread between those two numbers is most of commodity-market
   punditry done quantitatively.)
2. **Live input-cost pressure** — reprice the BOM at today's prices: how far
   has the input stack pushed the cost base off its anchor?
3. **Shock response** — any scenario or ripple that moves commodity prices
   maps linearly onto product cost: Δproduct% = Σ share_i × ΔP_i.

The honest frame, stated everywhere: this is INPUT-COST passthrough with
non-input costs and margins held fixed — a cost-base model, not a retail
price prediction. Pricing power, hedges, contracts and lags decide how much
of the cost move reaches the shelf, and none of those are modeled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from .pricing import load_pricebook

PRODUCT_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed" / "products"


class ProductInput(BaseModel):
    commodity: str
    qty: float          # in the pricebook unit for that commodity (t, bbl, MMBtu, ozt)
    note: str = ""


class ProductSeed(BaseModel):
    name: str
    display: str
    unit: str
    anchor_cost_usd: float
    source: str
    inputs: list[ProductInput]
    caveats: str = ""


def list_product_names() -> list[str]:
    return sorted(p.stem for p in PRODUCT_SEED_DIR.glob("*.yaml"))


def load_product(name: str) -> ProductSeed:
    return ProductSeed(**yaml.safe_load((PRODUCT_SEED_DIR / f"{name}.yaml").read_text()))


def breakdown(product: ProductSeed) -> dict:
    """Anchor-price cost stack: per-input cost and share of the product's
    anchor cost, plus the non-input remainder."""
    book = load_pricebook()
    rows = []
    input_cost = 0.0
    for inp in product.inputs:
        price = book.commodities[inp.commodity]
        cost = inp.qty * price.anchor_usd
        input_cost += cost
        rows.append({
            "commodity": inp.commodity, "qty": inp.qty, "qty_note": inp.note,
            "unit_price": price.anchor_usd, "price_unit": price.unit,
            "cost_usd": round(cost, 4),
            "share_pct": round(100 * cost / product.anchor_cost_usd, 2),
        })
    return {
        "rows": rows,
        "input_cost_usd": round(input_cost, 4),
        "input_share_pct": round(100 * input_cost / product.anchor_cost_usd, 1),
        "non_input_usd": round(product.anchor_cost_usd - input_cost, 4),
    }


def _live_price(commodity: str) -> tuple[Optional[float], Optional[str]]:
    from .history import load_price_history

    h = load_price_history(commodity)
    if not h:
        return None, None
    d, v = h.months[-1]
    return v, d


def live_pressure(product: ProductSeed) -> dict:
    """Reprice the BOM at the latest monthly prices (anchor where no series).
    Returns the repriced cost base and the pressure vs anchor."""
    bd = breakdown(product)
    delta = 0.0
    for row in bd["rows"]:
        live, live_date = _live_price(row["commodity"])
        row["live"] = live
        row["live_date"] = live_date
        if live is not None:
            row["live_cost_usd"] = round(row["qty"] * live, 4)
            delta += row["live_cost_usd"] - row["cost_usd"]
        else:
            row["live_cost_usd"] = row["cost_usd"]  # anchor stands in
    cost_now = product.anchor_cost_usd + delta
    bd["cost_now_usd"] = round(cost_now, 4)
    bd["pressure_pct"] = round(100 * delta / product.anchor_cost_usd, 1)
    return bd


def shock_response(product: ProductSeed, price_changes_pct: dict[str, float]) -> dict:
    """Linear input-cost passthrough of commodity price changes (in %) onto
    the product's cost base. Non-input costs held fixed — a cost model, not
    a retail price prediction."""
    bd = breakdown(product)
    total = 0.0
    contributions = []
    for row in bd["rows"]:
        pct = price_changes_pct.get(row["commodity"])
        if pct is None:
            continue
        contrib = row["share_pct"] / 100 * pct
        total += contrib
        contributions.append({"commodity": row["commodity"], "input_change_pct": pct,
                              "product_change_pct": round(contrib, 2)})
    return {"product": product.name, "cost_change_pct": round(total, 2),
            "contributions": contributions}


def all_shock_responses(price_changes_pct: dict[str, float],
                        min_abs_pct: float = 0.1) -> list[dict]:
    """Every product's cost response to a set of commodity moves — the stage
    a ripple runs after commodities. Sorted by |impact|, noise dropped."""
    out = []
    for name in list_product_names():
        r = shock_response(load_product(name), price_changes_pct)
        if abs(r["cost_change_pct"]) >= min_abs_pct:
            out.append(r)
    out.sort(key=lambda r: -abs(r["cost_change_pct"]))
    return out


def render_product(product: ProductSeed, bd: dict) -> str:
    lines = [
        f"{product.display} — {product.unit}, anchor {product.anchor_cost_usd:,.2f}",
        f"{'input':<13}{'qty':>10}{'unit price':>12}{'cost':>10}{'share':>8}"
        f"{'live':>10}{'now':>10}",
        "-" * 75,
    ]
    for r in bd["rows"]:
        live = f"{r['live']:,.0f}" if r.get("live") else "—"
        now = f"{r['live_cost_usd']:,.2f}" if r.get("live_cost_usd") is not None else "—"
        lines.append(f"{r['commodity']:<13}{r['qty']:>10g}{r['unit_price']:>12,.0f}"
                     f"{r['cost_usd']:>10,.2f}{r['share_pct']:>7.1f}%{live:>10}{now:>10}")
    lines += [
        "-" * 75,
        f"input cost at anchors: {bd['input_cost_usd']:,.2f} "
        f"({bd['input_share_pct']:.1f}% of product) · non-input {bd['non_input_usd']:,.2f}",
    ]
    if "pressure_pct" in bd:
        lines.append(f"repriced at latest monthlies: {bd['cost_now_usd']:,.2f} "
                     f"({bd['pressure_pct']:+.1f}% input-cost pressure vs anchor)")
    lines += ["", f"source: {product.source}"]
    if product.caveats:
        lines.append(f"caveats: {product.caveats}")
    lines.append("Cost-base passthrough only — margins, contracts, hedges and pricing power unmodeled.")
    return "\n".join(lines)


def scenario_changes(scenario) -> dict[str, float]:
    """Commodity price changes (%) implied by a scenario, for product
    response: driver scenarios via their compiled demand incidence,
    commodity scenarios via the multi-event ripple."""
    from .commodities import DriverScenario, run_driver_scenario
    from .linkages import ripple_events

    if isinstance(scenario, DriverScenario):
        return {r["commodity"]: r["price_change_pct"]
                for r in run_driver_scenario(scenario)
                if r["price_change_pct"] is not None}
    events = [(e.country, e.severity) for e in scenario.events]
    return {r.commodity: r.price_change_pct
            for r in ripple_events(scenario.commodity, events)}
