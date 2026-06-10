"""Precompute scenario runs and parameter sweeps for the static web demo.

The demo is a zero-backend artifact: this module runs the engine over every
shipped scenario plus dense parameter grids (tariff rate, Grasberg severity,
restart yes/no) and writes `web/data.js`. The page's "sliders" snap to grid
points — all interactivity, no server, deployable on GitHub Pages for $0.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .balance import BASELINE, run
from .commodities import (
    COMMODITY_SCENARIO_DIR,
    list_commodity_names,
    load_commodity,
    load_commodity_scenario,
    run_commodity,
)
from .ledger import load_assumptions, load_ledger
from .minmod import DEFAULT_CACHE as MINMOD_CACHE
from .minmod import cache_path, load_sites, partition_plausible
from .pricing import cached_fred, load_pricebook, summarize
from .scenario import SCENARIO_DIR, load_scenario
from .shocks import MineOutage, MineRestart, Scenario, SmelterClosure, Tariff

DEPOSIT_MIN_KT = 1_000.0  # copper default; per-commodity floors below
DEPOSIT_MAX_POINTS = 300
# Map-layer floors scale with each commodity's market size.
DEPOSIT_MIN_BY_COMMODITY = {
    "copper": 1_000.0,
    "nickel": 300.0,
    "zinc": 500.0,
    "cobalt": 100.0,
    "lithium": 100.0,
    "rare-earths": 100.0,
}

YEARS = range(2024, 2031)

TARIFF_RATES = [0, 5, 10, 15, 20, 25, 35, 50]
GRASBERG_SEVERITIES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

CATALYSTS = [
    {
        "name": "US Commerce report: refined-copper tariffs",
        "date": "2026-06-30",
        "detail": "Section 232 — refined cathode currently exempt; this report decides",
    },
    {
        "name": "Panama: Cobre Panamá full-restart decision",
        "date": "2026-06-30",
        "detail": "expected June 2026; stockpile processing (~120kt) already approved",
        "approx": True,
    },
]


def _world_2026_events() -> list:
    return list(load_scenario(SCENARIO_DIR / "world-2026.yaml").events)


def _grasberg_scenario(severity_2026: float) -> Scenario:
    events = [MineOutage(mine="Grasberg", start_year=2025, end_year=2025, severity=0.2)]
    if severity_2026 > 0:
        events.append(
            MineOutage(mine="Grasberg", start_year=2026, end_year=2026, severity=severity_2026)
        )
        if severity_2026 / 2 > 0:
            events.append(
                MineOutage(
                    mine="Grasberg", start_year=2027, end_year=2027, severity=severity_2026 / 2
                )
            )
    return Scenario(name=f"grasberg-sev-{severity_2026:.1f}", events=events)


def _deposit_layer(
    minmod_cache: Path, commodity: str = "copper", min_kt: float = DEPOSIT_MIN_KT
) -> list[dict]:
    """Major plausible MinMod deposits for the map. Empty when no cache —
    the demo never requires the MinMod fetch to have run."""
    if not minmod_cache.exists():
        return []
    plausible, _ = partition_plausible(load_sites(minmod_cache), commodity)
    majors = [
        s
        for s in plausible
        if s.contained_kt
        and s.contained_kt >= min_kt
        and s.lat is not None
        and s.lon is not None
        and abs(s.lat) <= 85
    ]
    majors.sort(key=lambda s: -s.contained_kt)
    return [
        {
            "name": s.name,
            "lat": s.lat,
            "lon": s.lon,
            "country": s.country or "?",
            "deposit_type": s.deposit_type or "?",
            "contained_kt": s.contained_kt,
        }
        for s in majors[:DEPOSIT_MAX_POINTS]
    ]


def _all_deposit_layers() -> dict[str, list[dict]]:
    layers = {}
    for commodity, min_kt in DEPOSIT_MIN_BY_COMMODITY.items():
        layer = _deposit_layer(cache_path(commodity), commodity, min_kt)
        if layer:
            layers[commodity] = layer
    return layers


def _pricing_payload(fetch_live: bool = True) -> dict:
    book = load_pricebook()
    curve = book.copper_cover_curve
    commodities = {}
    for name, p in book.commodities.items():
        live = None
        if fetch_live and p.fred_series:
            try:
                q = summarize(p.fred_series, cached_fred(p.fred_series))
                live = {"latest": q.latest, "avg_12m": q.avg_12m, "date": q.latest_date} if q else None
            except Exception:
                live = None
        commodities[name] = {
            "anchor_usd": p.anchor_usd,
            "unit": p.unit,
            "elasticity_supply": p.elasticity_supply,
            "elasticity_demand": p.elasticity_demand,
            "excluded": p.excluded_from_shock_pricing,
            "fred_series": p.fred_series,
            "live": live,
        }
    return {
        "copper_curve": {
            "anchor_usd_t": curve.anchor_usd_t,
            "baseline_days": curve.baseline_days,
            "gamma": curve.gamma,
            "clamp": list(curve.clamp),
        },
        "commodities": commodities,
    }


def _commodity_payloads() -> list[dict]:
    """The multi-commodity tier for the web: concentration + drift runs."""
    scenario_by_commodity = {}
    for path in sorted(COMMODITY_SCENARIO_DIR.glob("*.yaml")):
        scenario = load_commodity_scenario(path)
        scenario_by_commodity[scenario.commodity] = scenario

    out = []
    for name in list_commodity_names():
        seed = load_commodity(name)
        world_year = seed.world.latest_year
        world = seed.world.production_kt[world_year]
        baseline = run_commodity(seed)
        entry = {
            "name": name,
            "unit": seed.unit,
            "world_year": world_year,
            "world_production_kt": world,
            "world_reserves_kt": seed.world.reserves_kt,
            "concentration": seed.concentration(),
            "producers": [
                {
                    "country": p.country,
                    "production_kt": p.production_kt,
                    "share": round(p.production_kt / world, 4),
                }
                for p in seed.top_producers[:8]
            ],
            "baseline": [asdict(r) for r in baseline.rows],
            "scenario": None,
            "notes": seed.notes,
            "source": seed.source,
        }
        if name in scenario_by_commodity:
            scenario = scenario_by_commodity[name]
            entry["scenario"] = {
                "name": scenario.name,
                "description": scenario.description,
                "rows": [asdict(r) for r in run_commodity(seed, scenario).rows],
            }
        out.append(entry)
    return out


def build_payload(minmod_cache: Path = MINMOD_CACHE, fetch_live_prices: bool = True) -> dict:
    ledger = load_ledger()
    assumptions = load_assumptions()

    def rows(scenario: Scenario) -> list[dict]:
        return [asdict(r) for r in run(ledger, assumptions, scenario, YEARS).rows]

    baseline_rows = rows(BASELINE)

    scenarios = {}
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        scenario = load_scenario(path)
        scenarios[scenario.name] = {
            "description": scenario.description,
            "rows": rows(scenario),
        }

    tariff_runs = {
        str(rate): rows(
            Scenario(
                name=f"tariff-{rate}",
                events=[Tariff(rate_pct=rate, start_year=2026)] if rate else [],
            )
        )
        for rate in TARIFF_RATES
    }

    grasberg_runs = {
        f"{sev:.1f}": rows(_grasberg_scenario(sev)) for sev in GRASBERG_SEVERITIES
    }

    world = _world_2026_events()
    restart = MineRestart(mine="Cobre Panama", ramp={2026: 120, 2027: 280})
    decision_runs = {
        "no": rows(Scenario(name="world-2026", events=world)),
        "yes": rows(Scenario(name="world-2026+restart", events=world + [restart])),
    }

    year0 = YEARS[0]
    tracked = sum(m.production(year0) for m in ledger.mines)
    coverage = tracked / assumptions.world.mine_supply(year0)

    return {
        "meta": {
            "years": list(YEARS),
            "catalysts": CATALYSTS,
            "tracked_mines": len(ledger.mines),
            "coverage_pct": round(100 * coverage),
        },
        "baseline": baseline_rows,
        "scenarios": scenarios,
        "labs": {
            "tariff": {"values": TARIFF_RATES, "runs": tariff_runs},
            "grasberg": {"values": GRASBERG_SEVERITIES, "runs": grasberg_runs},
            "decision": {"runs": decision_runs},
        },
        "deposits": _all_deposit_layers() if minmod_cache == MINMOD_CACHE else (
            {"copper": _deposit_layer(minmod_cache)} if _deposit_layer(minmod_cache) else {}
        ),
        "commodities": _commodity_payloads(),
        "prices": _pricing_payload(fetch_live=fetch_live_prices),
        "mines": [
            {
                "name": m.name,
                "country": m.country,
                "owner": m.owner,
                "status": m.status.value,
                "capacity_kt": m.capacity_kt,
                "production_2026_kt": round(
                    m.production(2026, assumptions.world.tracked_utilization)
                ),
                "basis": m.basis.value,
                "lat": m.lat,
                "lon": m.lon,
                "notes": m.notes or "",
            }
            for m in sorted(ledger.mines, key=lambda m: -m.capacity_kt)
        ],
    }


def export_web(out_path: Path) -> Path:
    payload = build_payload()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "// generated by `opencopper export-web` — do not edit\n"
        f"window.OPENCOPPER_DATA = {json.dumps(payload)};\n"
    )
    return out_path
