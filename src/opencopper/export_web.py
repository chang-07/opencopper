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
from .geo import centroid
from .history import load_price_history
from .montecarlo import simulate_copper
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
    from .history import ambient_volatility

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
        from .history import conditional_volatility

        vol, vol_src = conditional_volatility(name)
        # measured 12m drift of the CURRENT regime (backtest mean_fwd) — the
        # simulator's spike odds add it to the shock center (regime mixture
        # logic, current-regime branch; see spec.py)
        drift12 = None
        try:
            from .backtest import backtest_commodity
            from .history import load_price_history as _lph

            _h = _lph(name)
            _bt = backtest_commodity(name, horizon=12) if _h else None
            if _h and _bt:
                drift12 = _bt.mean_fwd.get(_h.regime_now.value)
        except Exception:
            drift12 = None
        commodities[name] = {
            "anchor_usd": p.anchor_usd,
            "unit": p.unit,
            "elasticity_supply": p.elasticity_supply,
            "elasticity_demand": p.elasticity_demand,
            "excluded": p.excluded_from_shock_pricing,
            "fred_series": p.fred_series,
            "live": live,
            "vol": vol,
            "vol_source": vol_src,
            "drift12": drift12,
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
    from .commodities import DriverScenario

    scenario_by_commodity = {}
    for path in sorted(COMMODITY_SCENARIO_DIR.glob("*.yaml")):
        scenario = load_commodity_scenario(path)
        if isinstance(scenario, DriverScenario):
            continue  # driver scenarios are systemic; they live in the simulator's driver mode
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
            "drivers": seed.drivers,
            "byproduct_of": seed.byproduct_of,
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


def _history_payload() -> dict:
    """Per-commodity market regime + realized volatility, where a series exists."""
    out = {}
    for name in list_commodity_names():
        h = load_price_history(name)
        if h:
            out[name] = {
                "regime_now": h.regime_now.value,
                "annual_vol": h.annual_volatility,
                "max_drawdown": h.max_drawdown,
                "fractions": h.regime_fractions,
                "start": h.start[:7],
                "end": h.end[:7],
            }
    return out


def _country_payload() -> list[dict]:
    """Producer countries with centroid, per-commodity production+share, and an
    aggregate criticality score (sum of share^2 across commodities — a country
    is critical when it dominates something, anywhere)."""
    by_country: dict[str, dict] = {}
    for name in list_commodity_names():
        seed = load_commodity(name)
        world = seed.world.production_kt[seed.world.latest_year]
        for p in seed.top_producers:
            loc = centroid(p.country)
            if not loc:
                continue
            entry = by_country.setdefault(
                p.country,
                {"country": p.country, "lat": loc[0], "lon": loc[1], "commodities": {}, "criticality": 0.0},
            )
            share = p.production_kt / world
            entry["commodities"][name] = {"production_kt": p.production_kt, "share": round(share, 4)}
            entry["criticality"] += share * share
    for e in by_country.values():
        e["criticality"] = round(e["criticality"], 3)
    return sorted(by_country.values(), key=lambda e: -e["criticality"])


def _mc_band_payload(scenario, n_paths: int, seed: int) -> dict:
    mc = simulate_copper(scenario, n_paths=n_paths, seed=seed)
    return {
        "years": mc.years,
        "price": {"p10": mc.price.p10, "p50": mc.price.p50, "p90": mc.price.p90},
        "balance": {"p10": mc.balance.p10, "p50": mc.balance.p50, "p90": mc.balance.p90},
        "prob_deficit": mc.prob_deficit,
        "prob_spike": mc.prob_price_spike,
        "sim_vol": mc.simulated_annual_vol,
    }


def _simulation_payload(n_paths: int = 2500) -> dict:
    """Precomputed copper Monte Carlo: baseline + each shipped scenario."""
    from .balance import BASELINE

    runs = {"baseline": _mc_band_payload(BASELINE, n_paths, seed=42)}
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        scenario = load_scenario(path)
        runs[scenario.name] = _mc_band_payload(scenario, n_paths, seed=42)
    return runs


def _commodity_sim_payload(n_paths: int = 1500) -> dict:
    """Country-tier Monte Carlo fans for every priced commodity: baseline +
    the commodity's shipped scenario where one exists."""
    from .commodities import DriverScenario
    from .montecarlo import simulate_commodity

    scenario_by_commodity = {}
    for path in sorted(COMMODITY_SCENARIO_DIR.glob("*.yaml")):
        sc = load_commodity_scenario(path)
        if not isinstance(sc, DriverScenario):
            scenario_by_commodity[sc.commodity] = sc

    def pack(mc):
        return {
            "years": mc.years,
            "price": {"p10": mc.price.p10, "p50": mc.price.p50, "p90": mc.price.p90},
            "prob_double": mc.prob_double,
            "prob_halve": mc.prob_halve,
            "sim_vol": mc.simulated_annual_vol,
            "target_vol": mc.target_vol,
            "scenario": mc.scenario,
        }

    out = {}
    for name in list_commodity_names():
        base = simulate_commodity(name, n_paths=n_paths, seed=42)
        if base is None:
            continue  # gold: excluded from shock pricing
        entry = {"baseline": pack(base)}
        if name in scenario_by_commodity:
            shocked = simulate_commodity(name, scenario_by_commodity[name], n_paths=n_paths, seed=42)
            entry["scenario"] = pack(shocked)
        out[name] = entry
    return out


def _signals_payload() -> dict:
    from dataclasses import asdict

    from .signals import DISCLAIMER, build_signals

    return {"disclaimer": DISCLAIMER, "rows": [asdict(s) for s in build_signals(n_paths=800)]}


def _regional_payload() -> dict:
    """Quarterly US premium paths per tariff-grid rate (the COMEX-LME arb)."""
    from .regional import run_regional

    runs = {}
    for rate in TARIFF_RATES:
        scenario = Scenario(
            name=f"tariff-{rate}",
            events=[Tariff(rate_pct=rate, start_year=2026)] if rate else [],
        )
        rr = run_regional(scenario)
        runs[str(rate)] = {
            "labels": [r.label for r in rr.rows],
            "us_premium": [r.premium_pct["us"] for r in rr.rows],
            "us_minus_row": [r.us_minus_row for r in rr.rows],
        }
    return {"rates": TARIFF_RATES, "runs": runs}


def _products_payload() -> list[dict]:
    """Per-product BOM stack repriced live, plus +10%-input sensitivities."""
    from .products import list_product_names, live_pressure, load_product

    out = []
    for name in list_product_names():
        prod = load_product(name)
        bd = live_pressure(prod)
        out.append({
            "name": name, "display": prod.display, "unit": prod.unit,
            "anchor": prod.anchor_cost_usd, "source": prod.source,
            "caveats": prod.caveats, "rows": bd["rows"],
            "input_share_pct": bd["input_share_pct"],
            "non_input_usd": bd["non_input_usd"],
            "cost_now_usd": bd["cost_now_usd"], "pressure_pct": bd["pressure_pct"],
            "retail_passthrough": (prod.retail_passthrough.model_dump()
                                   if prod.retail_passthrough else None),
            "sensitivities": [
                {"commodity": r["commodity"],
                 "product_pct_per_10": round(r["share_pct"] / 10, 2)}
                for r in sorted(bd["rows"], key=lambda r: -r["share_pct"])
            ],
        })
    return out


def _benchmark_payload() -> dict:
    from dataclasses import asdict as _asdict

    from .benchmark import benchmark_all

    rows = benchmark_all(12)
    return {"rows": [_asdict(r) for r in rows],
            "n_beat_rw": sum(1 for r in rows if r.skill_vs_rw > 0)}


def _theses_payload() -> dict:
    from .theses import analytics, mark_all

    from dataclasses import asdict as _asdict

    marked = mark_all()
    return {"analytics": analytics(marked), "rows": [_asdict(m) for m in marked]}


def _data_freshness() -> list[dict]:
    from dataclasses import asdict as _asdict

    from .datastore import status

    return [_asdict(s) for s in status()]


def _backtest_payload() -> dict:
    """34-year walk-forward evidence on the regime signal — the desk shows
    its homework next to its opinions."""
    from dataclasses import asdict as _asdict

    from .backtest import backtest_all, summary

    from .backtest import tranche_strategy

    rows = backtest_all(12)
    slim = []
    for r in rows:
        d = _asdict(r)
        d.pop("monthly_legs")
        slim.append(d)
    from .backtest import sleeve_report

    sl = sleeve_report()
    return {"rows": slim, "summary": summary(rows),
            "tranche": tranche_strategy(include=("glut", "balanced|up"), cost_bps=25.0),
            "sleeves": {"corr": sl["corr"], "vt_combo": sl["vt_combo"],
                        "vt_boot": sl["vt_boot"], "vt_halves": sl["vt_halves"]}}


def _news_payload(news_dir: Path = Path("data/news")) -> dict:
    """Latest rule-matched headlines + their simulated cross-commodity
    impacts. Empty when the news pipeline has not run (the daily Action
    runs `opencopper news` before exporting)."""
    from dataclasses import asdict as _asdict

    from .linkages import ripple

    files = sorted(news_dir.glob("hits-*.json"))
    if not files:
        return {"date": None, "events": []}
    latest = files[-1]
    hits = json.loads(latest.read_text())
    groups: dict[tuple, list[dict]] = {}
    for h in hits:
        groups.setdefault((h["commodity"], h["country"], h["severity"]), []).append(h)
    events = []
    for (commodity, country, severity), hs in groups.items():
        impacts = []
        if severity > 0:
            try:
                impacts = [_asdict(r) for r in ripple(commodity, country, severity)]
            except Exception:
                impacts = []
        events.append({
            "commodity": commodity, "country": country, "severity": severity,
            "note": hs[0].get("rule_note", ""), "headline": hs[0]["headline"],
            "link": hs[0]["link"], "published": hs[0]["published"],
            "corroborating": len(hs) - 1, "impacts": impacts,
        })
    return {"date": latest.stem.replace("hits-", ""), "events": events}


def build_payload(
    minmod_cache: Path = MINMOD_CACHE,
    fetch_live_prices: bool = True,
    mc_paths: int = 2500,
) -> dict:
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
        "history": _history_payload(),
        "countries": _country_payload(),
        "simulation": _simulation_payload(mc_paths),
        "commoditySim": _commodity_sim_payload(),
        "regional": _regional_payload(),
        "signals": _signals_payload(),
        "news": _news_payload(),
        "backtest": _backtest_payload(),
        "theses": _theses_payload(),
        "freshness": _data_freshness(),
        "benchmark": _benchmark_payload(),
        "products": _products_payload(),
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
