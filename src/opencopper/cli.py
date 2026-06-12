"""opencopper command-line interface.

    opencopper simulate --scenario scenarios/world-2026.yaml
    opencopper ingest --max 10
    opencopper extract data/raw/<exhibit>.htm
    opencopper ledger
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ledger import load_assumptions, load_ledger


def _cmd_simulate(args: argparse.Namespace) -> int:
    from .report import render_report
    from .scenario import load_scenario, run_scenario

    scenario = load_scenario(Path(args.scenario))
    start, end = (int(y) for y in args.years.split("-"))
    result, baseline = run_scenario(scenario, years=range(start, end + 1))

    print(f"\nscenario: {scenario.name} — {scenario.description}\n")
    header = (
        f"{'year':<6}{'supply':>9}{'demand':>9}{'balance':>9}{'Δ base':>9}"
        f"{'inv days':>10}{'TC prs':>8}{'US prem':>9}"
    )
    print(header)
    print("-" * len(header))
    for row, base in zip(result.rows, baseline.rows):
        delta = row.refined_balance_kt - base.refined_balance_kt
        print(
            f"{row.year:<6}{row.refined_supply_kt:>9.0f}{row.refined_demand_kt:>9.0f}"
            f"{row.refined_balance_kt:>9.0f}{delta:>+9.0f}{row.inventory_days:>10.1f}"
            f"{row.tc_pressure:>8.1%}{row.us_premium_pct:>8.1f}%"
        )

    if args.json:
        print(json.dumps([r.__dict__ for r in result.rows], indent=2))

    # implied copper price from the inventory-cover scarcity curve
    from .pricing import copper_price_from_cover, load_pricebook

    curve = load_pricebook().copper_cover_curve
    print("\nimplied price (cover scarcity curve, illustrative — NOT a forecast):")
    print(f"  {'year':<6}{'cover days':>12}{'implied $/t':>14}{'vs baseline':>13}")
    for row, base in zip(result.rows, baseline.rows):
        p = copper_price_from_cover(row.inventory_days, curve)
        bp = copper_price_from_cover(base.inventory_days, curve)
        delta = (p / bp - 1) * 100 if bp else 0
        print(f"  {row.year:<6}{row.inventory_days:>12.1f}{p:>14,.0f}{delta:>+12.0f}%")

    out = Path(args.out) if args.out else Path("out") / f"{scenario.name}.html"
    render_report(scenario, result, baseline, out)
    print(f"\nreport: {out}")
    return 0


def _cmd_ripple(args):
    from .linkages import render_ripple, ripple

    rows = ripple(args.commodity, args.country, args.severity)
    print(render_ripple(rows, f"{args.country or 'world'} {args.commodity} -{args.severity:.0%}"))
    from .products import all_shock_responses

    responses = all_shock_responses({r.commodity: r.price_change_pct for r in rows})
    if responses:
        print("\nPRODUCT COST RESPONSE (input-cost passthrough, margins fixed):")
        for resp in responses:
            via = ", ".join(f"{c['commodity']} {c['product_change_pct']:+.1f}%" for c in resp["contributions"])
            print(f"  {resp['product']:<18}{resp['cost_change_pct']:>+7.1f}%   ({via})")
    return 0


def _cmd_news(args):
    from .news import run_news

    run_news()
    return 0


def _cmd_book(args):
    from .book import evaluate_book, load_book, render_book
    from .commodities import load_commodity_scenario
    from .scenario import load_scenario

    if getattr(args, "risk", False):
        from .book import book_risk, render_risk

        print(render_risk(book_risk(load_book(Path(args.file)))))
        return 0

    scenario = None
    if args.scenario:
        sp = Path(args.scenario)
        scenario = load_commodity_scenario(sp) if "commodities" in str(sp) else load_scenario(sp)
    print(render_book(evaluate_book(load_book(Path(args.file)), scenario, year=args.year, n_paths=args.paths)))
    return 0


def _cmd_signals(args: argparse.Namespace) -> int:
    from .signals import build_signals, render_signals, signals_json

    signals = build_signals(n_paths=args.paths)
    print(signals_json(signals) if args.json else render_signals(signals))
    return 0


def _cmd_product(args):
    from .products import (
        all_shock_responses,
        breakdown,
        list_product_names,
        live_pressure,
        load_product,
        render_product,
        scenario_changes,
    )

    if args.action == "list":
        print(f"{'product':<18}{'anchor':>10}  {'input share':>11}  {'live pressure':>13}")
        print("-" * 58)
        for name in list_product_names():
            prod = load_product(name)
            bd = live_pressure(prod)
            print(f"{name:<18}{prod.anchor_cost_usd:>10,.2f}  {bd['input_share_pct']:>10.1f}%"
                  f"  {bd['pressure_pct']:>+12.1f}%")
        print("\nCost-base passthrough only; see `opencopper product report <name>`.")
        return 0
    prod = load_product(args.name)
    print(render_product(prod, live_pressure(prod)))
    if args.scenario:
        from .commodities import load_commodity_scenario

        scenario = load_commodity_scenario(Path(args.scenario))
        changes = scenario_changes(scenario)
        resp = next((r for r in all_shock_responses(changes, min_abs_pct=0.0)
                     if r["product"] == args.name), None)
        print(f"\nscenario '{scenario.name}' -> {args.name} cost "
              f"{resp['cost_change_pct']:+.1f}%" if resp else "\nscenario: no input overlap")
        if resp:
            for c in resp["contributions"]:
                print(f"  {c['commodity']:<13} input {c['input_change_pct']:+.0f}% "
                      f"-> product {c['product_change_pct']:+.2f}%")
    return 0


def _cmd_theses(args: argparse.Namespace) -> int:
    from .theses import mark_all, render_theses, theses_json

    marked = mark_all()
    print(theses_json(marked) if args.json else render_theses(marked))
    return 0


def _cmd_data(args: argparse.Namespace) -> int:
    from .datastore import refresh, render_status, status

    if args.action == "refresh":
        for line in refresh(args.source):
            print(line)
        return 0
    print(render_status(status()))
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import backtest_all, backtest_commodity, render_backtest

    if args.commodity:
        row = backtest_commodity(args.commodity, horizon=args.horizon)
        if row is None:
            print(f"{args.commodity}: not enough price history to backtest")
            return 1
        rows = [row]
    else:
        rows = backtest_all(horizon=args.horizon)
    print(render_backtest(rows, args.horizon))
    return 0


def _cmd_regional(args: argparse.Namespace) -> int:
    from .balance import BASELINE
    from .regional import render_regional, run_regional
    from .scenario import load_scenario

    scenario = load_scenario(Path(args.scenario)) if args.scenario else BASELINE
    rr = run_regional(scenario)
    print(render_regional(rr, around_year=args.around or None))
    return 0


def _cmd_montecarlo(args: argparse.Namespace) -> int:
    from .balance import BASELINE
    from .montecarlo import render_montecarlo, simulate_copper
    from .scenario import load_scenario

    scenario = load_scenario(Path(args.scenario)) if args.scenario else BASELINE
    mc = simulate_copper(scenario, n_paths=args.paths, seed=args.seed)
    print(render_montecarlo(mc))
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    from .history import load_price_history, render_history
    from .pricing import load_pricebook

    names = [args.commodity] if args.commodity else list(load_pricebook().commodities)
    shown = False
    for name in names:
        h = load_price_history(name)
        if h:
            print(render_history(h) + "\n")
            shown = True
        elif args.commodity:
            print(f"{name}: no FRED price series (USGS anchor only)")
    if not shown and not args.commodity:
        print("no price history available")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from .calibrate import (
        calibrate_copper,
        hindcast_copper,
        render_calibration,
        render_hindcast,
        render_tail_shape,
        tail_shape_check,
    )
    from .history import load_price_history

    print(render_calibration(calibrate_copper(n_paths=args.paths)))
    print(render_tail_shape(tail_shape_check(n_paths=args.paths)))
    hist = load_price_history("copper")
    print(render_hindcast(hindcast_copper(), hist.end[:7] if hist else "?"))
    return 0


def _cmd_price(args: argparse.Namespace) -> int:
    from .pricing import (
        cached_fred,
        load_pricebook,
        price_impact_from_shock,
        render_price_table,
        summarize,
    )

    book = load_pricebook()
    if args.commodity:
        if args.commodity not in book.commodities:
            print(f"unknown commodity {args.commodity!r}; have: {', '.join(book.commodities)}")
            return 1
        price = book.commodities[args.commodity]
        if price.excluded_from_shock_pricing:
            print(f"{args.commodity}: shock pricing disabled — {price.note}")
            return 0
        impact = price_impact_from_shock(price, args.supply_loss)
        print(f"{args.commodity}: remove {impact.supply_loss_pct:.0f}% of supply")
        print(f"  CES incidence: P/P0 = (1-k)^(-1/(η_d+η_s)) "
              f"= {1 - args.supply_loss:.2f}^(-1/{price.elasticity_demand + price.elasticity_supply:.2f})")
        clamp_note = "  [clamped at model range]" if impact.clamped else ""
        print(f"  implied price change: {'≥' if impact.clamped and impact.price_change_pct > 0 else ''}"
              f"{impact.price_change_pct:+.0f}%  "
              f"({impact.anchor_usd:,.0f} -> {impact.implied_usd:,.0f} {impact.unit}){clamp_note}")
        from .pricing import impact_range

        rng = impact_range(price, args.supply_loss)
        if rng:
            print(f"  elasticity-range band:  {rng[0]:+.0f}% .. {rng[1]:+.0f}%  "
                  f"(same shock, seeded η ranges — the parameter risk around the point)")
        print(f"  short-run partial equilibrium; ignores substitution dynamics, destocking, processing.")
        return 0

    live = {}
    for name, p in book.commodities.items():
        if p.fred_series:
            try:
                live[name] = summarize(p.fred_series, cached_fred(p.fred_series))
            except Exception:
                live[name] = None
    print(render_price_table(book, live))
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    from .edgar import download_exhibit, search_technical_reports

    hits = search_technical_reports(query=args.query, max_hits=args.max)
    dest = Path(args.dest)
    print(f"{len(hits)} exhibits found for query: {args.query!r}")
    for hit in hits:
        print(f"  {hit.file_date}  {hit.form:<6} {hit.company:<40} {hit.filename}")
        if not args.dry_run:
            path = download_exhibit(hit, dest)
            print(f"    -> {path}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from .extract import extract_mine_data, load_document_text

    path = Path(args.file)
    text = load_document_text(path)
    data = extract_mine_data(
        text, model=args.model, prefilter=not args.full, source_filename=path.name
    )
    print(data.model_dump_json(indent=2))
    return 0


def _cmd_estimate(args: argparse.Namespace) -> int:
    """Estimate extraction tokens/cost before spending. ~4 chars/token heuristic
    (no API call) unless --exact, which uses the token-counting endpoint."""
    from .extract import load_document_text, relevant_sections

    # $/M input, rough output assumption (~2k tokens of structured JSON)
    prices = {"claude-opus-4-8": 5.0, "claude-sonnet-4-6": 3.0, "claude-haiku-4-5": 1.0}
    out_prices = {"claude-opus-4-8": 25.0, "claude-sonnet-4-6": 15.0, "claude-haiku-4-5": 5.0}
    in_price = prices.get(args.model, 5.0)
    out_price = out_prices.get(args.model, 25.0)

    paths = sorted(
        p for p in Path(args.dir).iterdir() if p.suffix.lower() in (".htm", ".html", ".pdf", ".txt")
    )
    full_tok = filt_tok = 0
    for path in paths:
        text = load_document_text(path)
        filtered = relevant_sections(text)
        full_tok += len(text) // 4
        filt_tok += len(filtered) // 4

    def cost(in_tok: int) -> float:
        out_tok = 2000 * len(paths)
        return in_tok / 1e6 * in_price + out_tok / 1e6 * out_price

    print(f"{len(paths)} documents, model {args.model}")
    print(f"  full text:      ~{full_tok:>10,} in-tokens  ->  ${cost(full_tok):.2f}")
    print(f"  pre-filtered:   ~{filt_tok:>10,} in-tokens  ->  ${cost(filt_tok):.2f}")
    print(f"  + Batches API (50% off):                       ${cost(filt_tok) / 2:.2f}")
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    from .batch import batch_status, collect_results, submit_batch

    if args.batch_command == "submit":
        paths = sorted(
            p for p in Path(args.dir).iterdir() if p.suffix.lower() in (".htm", ".html", ".pdf", ".txt")
        )
        if not paths:
            print(f"no exhibits in {args.dir}")
            return 1
        manifest = Path(args.manifest)
        batch_id = submit_batch(paths, model=args.model, manifest_path=manifest)
        print(f"submitted {len(paths)} documents: batch {batch_id}")
        print(f"manifest: {manifest}")
        print(f"check:    opencopper batch status {batch_id}")
    elif args.batch_command == "status":
        print(json.dumps(batch_status(args.batch_id), indent=2))
    elif args.batch_command == "collect":
        ok, failed = collect_results(Path(args.manifest), Path(args.out))
        print(f"collected {ok} extractions ({failed} failed) -> {args.out}")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from .evals import run_eval

    print(run_eval(Path(args.extractions), Path(args.truth)))
    return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    from .reconcile import run_reconcile

    print(run_reconcile(Path(args.extractions)))
    return 0


def _cmd_export_web(args: argparse.Namespace) -> int:
    from .export_web import export_web

    out = export_web(Path(args.out))
    print(f"web data: {out}")
    print("open web/index.html (or serve the web/ directory) to view the demo")
    return 0


def _cmd_minmod(args: argparse.Namespace) -> int:
    from .minmod import (
        COMMODITIES,
        cache_path,
        fetch_sites,
        load_sites,
        render_report,
        save_sites,
    )

    if args.commodity not in COMMODITIES:
        print(f"unknown commodity {args.commodity!r}; have: {', '.join(COMMODITIES)}")
        return 1
    if args.minmod_command == "fetch":
        sites = fetch_sites(args.commodity, max_sites=args.max)
        out = save_sites(sites, Path(args.out) if args.out else cache_path(args.commodity))
        print(f"fetched {len(sites):,} {args.commodity} sites -> {out}")
    elif args.minmod_command == "report":
        sites = load_sites(Path(args.cache) if args.cache else cache_path(args.commodity))
        print(render_report(sites, load_ledger(), commodity=args.commodity))
    return 0


def _cmd_commodity(args: argparse.Namespace) -> int:
    from .commodities import (
        list_commodity_names,
        load_commodity,
        load_commodity_scenario,
        render_commodity_report,
        run_commodity,
    )

    if args.commodity_command == "list":
        for name in list_commodity_names():
            seed = load_commodity(name)
            conc = seed.concentration()
            world = seed.world.production_kt[seed.world.latest_year]
            print(
                f"{name:<12} {world:>12,.0f} kt  top1 {conc['top1']:>5.1%}"
                f"  top3 {conc['top3']:>5.1%}  HHI≥{conc['hhi_lower_bound']:>5,}"
            )
    elif args.commodity_command == "report":
        from .commodities import DriverScenario, compile_driver_scenario

        seed = load_commodity(args.name)
        scenario = None
        if args.scenario:
            scenario = load_commodity_scenario(Path(args.scenario))
            if isinstance(scenario, DriverScenario):
                scenario = compile_driver_scenario(scenario, seed)
            elif scenario.commodity != seed.name:
                print(f"scenario is for {scenario.commodity!r}, not {seed.name!r}")
                return 1
        run = run_commodity(seed, scenario)
        print(render_commodity_report(seed, run))
        if scenario:
            _print_commodity_price_impact(seed, run)
    elif args.commodity_command == "driver-shock":
        from .commodities import DriverEvent, DriverScenario, render_driver_report, run_driver_scenario

        start, end = (int(y) for y in args.years.split("-"))
        ds = DriverScenario(
            name=f"{args.driver}{args.pct:+.0f}%",
            description=f"ad-hoc: {args.driver} demand {args.pct:+.0f}% over {args.years}",
            events=[DriverEvent(driver=args.driver, pct=args.pct, start_year=start, end_year=end)],
        )
        print(render_driver_report(ds, run_driver_scenario(ds)))
    elif args.commodity_command == "driver-scenario":
        from .commodities import DriverScenario, render_driver_report, run_driver_scenario

        ds = load_commodity_scenario(Path(args.path))
        if not isinstance(ds, DriverScenario):
            print(f"{args.path} is a single-commodity scenario; use `commodity report --scenario`")
            return 1
        print(render_driver_report(ds, run_driver_scenario(ds)))
    return 0


def _print_commodity_price_impact(seed, run) -> None:
    from .history import ambient_volatility
    from .pricing import load_pricebook, price_impact_from_shock, prob_price_multiple

    book = load_pricebook()
    price = book.commodities.get(seed.name)
    if not price or price.excluded_from_shock_pricing:
        return
    shock_rows = [(r.year, r.supply_lost_kt / seed.world.production(r.year))
                  for r in run.rows if r.supply_lost_kt > 0]
    if not shock_rows:
        return
    peak_year, peak_loss = max(shock_rows, key=lambda yr: yr[1])
    impact = price_impact_from_shock(price, peak_loss)
    vol, vol_src = ambient_volatility(seed.name)
    p2x = prob_price_multiple(impact.price_change_pct / 100, vol, 2.0)
    print("\nIMPLIED PRICE (elasticity-incidence, illustrative — NOT a forecast):")
    print(f"  peak supply withdrawal {impact.supply_loss_pct:.0f}% of world ({peak_year}) "
          f"-> {impact.price_change_pct:+.0f}% price")
    print(f"  {impact.anchor_usd:,.0f} -> {impact.implied_usd:,.0f} {impact.unit} "
          f"(η_d {price.elasticity_demand}, η_s {price.elasticity_supply})")
    print(f"  ambient annual vol ±{vol:.0%} ({vol_src}); "
          f"P(price > 2x anchor within the shock year) ≈ {p2x:.0%}")


def _cmd_sensitivity(args: argparse.Namespace) -> int:
    from .sensitivity import render_tornado, run_price_sensitivity, run_sensitivity

    if args.target == "price":
        rows = run_price_sensitivity(year=args.year)
        print(render_tornado(rows, args.year, "world-2026", quantity="implied price (USD/t)"))
        print("\nThese are the judgment-calibrated pricing parameters — the tornado IS")
        print("their uncertainty statement. See github issue #3 for fitting gamma.")
        return 0
    scenario = None
    name = "baseline"
    if args.scenario:
        from .scenario import load_scenario

        scenario = load_scenario(Path(args.scenario))
        name = scenario.name
    rows = run_sensitivity(year=args.year, scenario=scenario)
    print(render_tornado(rows, args.year, name))
    return 0


def _cmd_ledger(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    assumptions = load_assumptions()
    year = args.year
    tracked = sum(m.production(year, assumptions.world.tracked_utilization) for m in ledger.mines)
    world = assumptions.world.mine_supply(year)
    print(f"tracked mines: {len(ledger.mines)}")
    print(f"tracked supply {year}: {tracked:,.0f} kt  ({tracked / world:.0%} of world {world:,.0f} kt)")
    for m in sorted(ledger.mines, key=lambda m: -m.capacity_kt):
        prod = m.production(year, assumptions.world.tracked_utilization)
        print(f"  {m.name:<18} {m.country:<14} {prod:>7,.0f} kt  [{m.status.value}] ({m.basis.value})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opencopper", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("simulate", help="run a shock scenario against the baseline")
    p.add_argument("--scenario", required=True)
    p.add_argument("--years", default="2024-2030")
    p.add_argument("--out", default=None, help="HTML report path")
    p.add_argument("--json", action="store_true", help="also print rows as JSON")
    p.set_defaults(func=_cmd_simulate)

    p = sub.add_parser("ingest", help="search/download EX-96 technical report summaries from EDGAR")
    p.add_argument("--query", default='"technical report summary" copper')
    p.add_argument("--max", type=int, default=10)
    p.add_argument("--dest", default="data/raw")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("extract", help="LLM-extract structured mine data from one exhibit")
    p.add_argument("file")
    p.add_argument("--model", default="claude-opus-4-8")
    p.add_argument("--full", action="store_true", help="send the whole document (skip section pre-filter)")
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("estimate", help="estimate extraction tokens/cost before spending (no API call)")
    p.add_argument("dir", help="directory of exhibit files")
    p.add_argument("--model", default="claude-haiku-4-5")
    p.set_defaults(func=_cmd_estimate)

    p = sub.add_parser("ledger", help="print the tracked mine ledger and world coverage")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=_cmd_ledger)

    p = sub.add_parser("batch", help="bulk extraction via the Batches API (50%% cheaper)")
    bsub = p.add_subparsers(dest="batch_command", required=True)
    b = bsub.add_parser("submit")
    b.add_argument("dir", help="directory of exhibit files")
    b.add_argument("--model", default="claude-opus-4-8")
    b.add_argument("--manifest", default="data/batch-manifest.json")
    b = bsub.add_parser("status")
    b.add_argument("batch_id")
    b = bsub.add_parser("collect")
    b.add_argument("manifest", nargs="?", default="data/batch-manifest.json")
    b.add_argument("--out", default="data/extracted")
    p.set_defaults(func=_cmd_batch)

    p = sub.add_parser("eval", help="score extractions against hand-verified ground truth")
    p.add_argument("--extractions", default="data/extracted")
    p.add_argument("--truth", default="evals/ground_truth.yaml")
    p.set_defaults(func=_cmd_eval)

    p = sub.add_parser("reconcile", help="diff extracted values against the seed ledger")
    p.add_argument("--extractions", default="data/extracted")
    p.set_defaults(func=_cmd_reconcile)

    p = sub.add_parser("export-web", help="precompute scenario data for the static web demo")
    p.add_argument("--out", default="web/data.js")
    p.set_defaults(func=_cmd_export_web)

    p = sub.add_parser("sensitivity", help="tornado: which assumption moves the balance (or price) most")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--scenario", default=None)
    p.add_argument("--target", choices=["balance", "price"], default="balance")
    p.set_defaults(func=_cmd_sensitivity)

    p = sub.add_parser("ripple", help="cross-commodity propagation of a shock (byproduct/substitution/input-cost)")
    p.add_argument("--commodity", required=True)
    p.add_argument("--country", default=None)
    p.add_argument("--severity", type=float, required=True)
    p.set_defaults(func=_cmd_ripple)

    p = sub.add_parser("news", help="fetch headlines, match rules, simulate impacts -> out/news-brief.md")
    p.set_defaults(func=_cmd_news)

    p = sub.add_parser("book", help="value YOUR exposures under a scenario: P&L distribution (not advice)")
    p.add_argument("file", help="exposure book yaml (see examples/book.yaml)")
    p.add_argument("--scenario", default=None)
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--paths", type=int, default=1500)
    p.add_argument("--risk", action="store_true",
                   help="historical-covariance VaR/ES on the book (1m delta-normal, correlations included)")
    p.set_defaults(func=_cmd_book)

    p = sub.add_parser("signals", help="desk sheet: model vs live market per commodity (decision support, not advice)")
    p.add_argument("--json", action="store_true", help="machine-readable, for your own systems")
    p.add_argument("--paths", type=int, default=800)
    p.set_defaults(func=_cmd_signals)

    p = sub.add_parser("product", help="products priced off their commodity bill of materials (cost-base passthrough)")
    p.add_argument("action", choices=["list", "report"])
    p.add_argument("name", nargs="?")
    p.add_argument("--scenario", default=None)
    p.set_defaults(func=_cmd_product)

    p = sub.add_parser("theses", help="scorecard: every registered + news-generated call, marked to market")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_theses)

    p = sub.add_parser("data", help="one view over every cache: freshness, rows, latest date; force refresh")
    p.add_argument("action", choices=["status", "refresh"], nargs="?", default="status")
    p.add_argument("--source", choices=["all", "fred", "pinksheet"], default="all")
    p.set_defaults(func=_cmd_data)

    p = sub.add_parser("backtest", help="walk-forward test: does the regime signal predict forward returns? (34yr, NW t-stats)")
    p.add_argument("--commodity", default=None)
    p.add_argument("--horizon", type=int, default=12, help="forward-return horizon in months")
    p.set_defaults(func=_cmd_backtest)

    p = sub.add_parser("regional", help="quarterly 3-region trade flows: covers, premia, the COMEX-LME arb")
    p.add_argument("--scenario", default=None, help="scenarios/*.yaml (default: baseline)")
    p.add_argument("--around", type=int, default=2026, help="print quarters around this year (0 = all)")
    p.set_defaults(func=_cmd_regional)

    p = sub.add_parser("montecarlo", help="stochastic simulation: price/balance bands, P(deficit), P(spike)")
    p.add_argument("--scenario", default=None, help="scenarios/*.yaml (default: baseline)")
    p.add_argument("--paths", type=int, default=4000)
    p.add_argument("--seed", type=int, default=12345)
    p.set_defaults(func=_cmd_montecarlo)

    p = sub.add_parser("history", help="historical price regimes + volatility (FRED, 1992-present)")
    p.add_argument("--commodity", default=None)
    p.set_defaults(func=_cmd_history)

    p = sub.add_parser("validate", help="calibrate the simulator against realized price volatility")
    p.add_argument("--paths", type=int, default=1500)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("price", help="implied prices: FRED live levels + elasticity-incidence under shock")
    p.add_argument("--commodity", default=None, help="show shock price impact for one commodity")
    p.add_argument("--supply-loss", type=float, default=0.1, help="fraction of world supply withdrawn")
    p.set_defaults(func=_cmd_price)

    p = sub.add_parser("commodity", help="multi-commodity tier: USGS country-level supply + concentration")
    csub = p.add_subparsers(dest="commodity_command", required=True)
    c = csub.add_parser("list", help="all commodities with concentration metrics")
    c = csub.add_parser("report", help="one commodity: producers, HHI, balance drift")
    c.add_argument("name")
    c.add_argument("--scenario", default=None, help="scenarios/commodities/*.yaml")
    c = csub.add_parser("driver-shock", help="systemic: shock a demand driver across ALL commodities")
    c.add_argument("--driver", required=True, help="batteries|construction|grid|transport|electronics|...")
    c.add_argument("--pct", type=float, required=True, help="driver demand change, e.g. -25")
    c.add_argument("--years", default="2026-2027")
    c = csub.add_parser("driver-scenario", help="run a type:driver scenario file across all commodities")
    c.add_argument("path")
    p.set_defaults(func=_cmd_commodity)

    p = sub.add_parser("minmod", help="DARPA MinMod deposit KG (deposits, not production)")
    msub = p.add_subparsers(dest="minmod_command", required=True)
    m = msub.add_parser("fetch", help="download all sites with grade-tonnage models")
    m.add_argument("--commodity", default="copper")
    m.add_argument("--max", type=int, default=None)
    m.add_argument("--out", default=None)
    m = msub.add_parser("report", help="summary (+ ledger matches for copper) from the cached fetch")
    m.add_argument("--commodity", default="copper")
    m.add_argument("--cache", default=None)
    p.set_defaults(func=_cmd_minmod)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
