"""The policy layer: laws as structured data, wired to what they touch.

A policy entry is a scenario generator with a citation — jurisdiction,
mechanism, status (enacted / pending-decision), magnitude in the model's
terms, and the commodities AND products it lands on. The report surfaces
the registry next to each commodity/product; pending decisions carry their
dates (the June-30 Section 232 report is the live one). Curation rule: only
policies that change supply, demand, or trade geometry of modeled names —
not general industrial policy.
"""

from __future__ import annotations

from pathlib import Path

import yaml

POLICIES_PATH = Path(__file__).resolve().parents[2] / "data" / "seed" / "policies.yaml"


def load_policies() -> list[dict]:
    return yaml.safe_load(POLICIES_PATH.read_text())["policies"]


def policies_for(commodity: str | None = None, product: str | None = None) -> list[dict]:
    out = []
    for p in load_policies():
        if commodity and commodity in p.get("commodities", []):
            out.append(p)
        elif product and product in p.get("products", []):
            out.append(p)
    return out


def render_policies(policies: list[dict], heading: str = "POLICY REGISTRY") -> str:
    lines = [heading, ""]
    for p in sorted(policies, key=lambda x: (x["status"] != "pending-decision",
                                             x.get("decision_due", x.get("effective", "")))):
        when = (f"DECIDES {p['decision_due']}" if p.get("decision_due")
                else f"since {p.get('effective', '?')}")
        lines.append(f"  [{p['status'].upper():<16}] {p['name']}")
        lines.append(f"    {p['jurisdiction']} · {p['mechanism']} · {when}")
        lines.append(f"    touches: {', '.join(p.get('commodities', []) + p.get('products', []))}")
        lines.append(f"    {p['magnitude']}")
        if p.get("scenario"):
            lines.append(f"    scenario: {p['scenario']}")
        lines.append("")
    lines.append("Statuses are facts with sources; magnitudes are the model's framing.")
    return "\n".join(lines)
