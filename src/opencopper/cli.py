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

    out = Path(args.out) if args.out else Path("out") / f"{scenario.name}.html"
    render_report(scenario, result, baseline, out)
    print(f"\nreport: {out}")
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
    data = extract_mine_data(text, model=args.model, source_filename=path.name)
    print(data.model_dump_json(indent=2))
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
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("ledger", help="print the tracked mine ledger and world coverage")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=_cmd_ledger)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
