# opencopper

**An open world model of copper supply and demand.** An LLM-built, mine-level
ledger of global copper production, plus a transparent simulator for the shocks
that move the market: mine disasters, smelter closures, export blocks, tariffs,
restarts.

Commercial copper models (Wood Mackenzie, CRU, S&P, Benchmark) are excellent —
and enterprise-priced, black-box, output-only. opencopper takes the opposite
position: **every assumption is a line in a YAML file you can read, dispute,
and PR.** The model is wrong, like all models — but it shows its work.

```
$ opencopper simulate --scenario scenarios/world-2026.yaml

scenario: world-2026 — Composed actuals: Grasberg mud rush, Kamoa-Kakula seismic
recovery, El Teniente carryover, TC/RC smelter closures

year   supply   demand  balance   Δ base  inv days  TC prs  US prem
-----------------------------------------------------------------
2024    26737    26800      -63       +0      11.2    3.4%     0.0%
2026    26350    26790     -440     -441       5.1    3.1%     0.0%
...
```

## Why this exists (June 2026)

Copper is the most interesting market in the world right now and there is no
open artifact to reason about it with:

- LME hit a record **~$14,500/t**; COMEX a record **$6.716/lb** (May 13).
- Treatment charges settled at **$0/t** (spot went *negative*) — smelters are
  paying for the privilege of smelting, and shutting down (Pasar, Tsumeb).
- **Grasberg**, the world's #2 mine, is recovering from the Sept 2025 mud rush
  with full recovery pushed to **2028**.
- Panama decides on the **Cobre Panamá** restart **this month**; the US
  Commerce report on refined-copper tariffs is due **June 30, 2026**.
- ICSG flipped its 2026 forecast from a +209kt surplus to a **-150kt deficit**.

Both June catalysts ship as scenario files. Run them before the decisions land;
compare against reality after.

## How it works

```
EDGAR EX-96 exhibits ──> LLM extraction (cited, schema-validated) ──┐
USGS / ICSG / company reporting (seed estimates) ───────────────────┼──> mine ledger
                                                                    │
                              data/seed/assumptions.yaml ───────────┼──> balance engine
                              scenarios/*.yaml (shock events) ──────┘        │
                                                                   CLI table + HTML report
```

Two coupled annual balances, because the market clears in two stages and the
2025-26 squeeze lives in the first one:

1. **Concentrate**: mine supply (ex SX-EW) vs smelter intake capacity. Scarcity
   here is what drove TCs negative — reported as `tc_pressure`.
2. **Refined**: smelted output + SX-EW + scrap vs regional demand. The balance
   flows into inventory cover, reported as `price_pressure` — an index,
   deliberately **not** a price forecast.

Tracked mines (27 today, ~42% of world supply) are modeled individually; the
rest of the world is an explicit aggregate with a disruption allowance. Shocks
are typed, parameterized events (`MineOutage`, `MineRestart`, `SmelterClosure`,
`DemandShock`, `Tariff`, `ExportBlock`) composed in YAML scenario files.

## Quickstart

```bash
uv sync
uv run opencopper ledger                                            # the mine ledger + world coverage
uv run opencopper simulate --scenario scenarios/grasberg-2025.yaml  # backtest the mud rush
uv run opencopper simulate --scenario scenarios/us-refined-tariff-2026.yaml
uv run opencopper export-web && python3 -m http.server -d web      # the interactive demo
```

### The extraction loop (needs `ANTHROPIC_API_KEY`)

```bash
uv run opencopper ingest --max 20                  # download EX-96 exhibits (HTML + PDF)
uv run opencopper extract data/raw/<exhibit>.pdf   # one document, cited structured output
uv run opencopper batch submit data/raw            # bulk via the Batches API (50% cheaper)
uv run opencopper batch status <batch_id>
uv run opencopper batch collect                    # validate -> data/extracted/*.json
uv run opencopper reconcile                        # diff extractions against the seed ledger
uv run opencopper eval                             # score against hand-verified ground truth
```

Extractions never overwrite the ledger silently: `reconcile` surfaces every
discrepancy for review (two sources, diffed — the fintech way), and `eval`
treats an uncited value as wrong even when the number is right.

## Data sources (all free)

| Source | Used for |
|---|---|
| SEC EDGAR full-text search (EX-96 / S-K 1300 exhibits) | LLM extraction of mine-level data, with citations |
| USGS Mineral Commodity Summaries, ICSG monthly releases | World totals, calibration |
| Company production reports | Seed estimates for tracked mines |
| [MinMod (DARPA CriticalMAAS)](https://minmod.isi.edu) — planned | NI 43-101 universe without scraping SEDAR+ |
| FRED `PCOPPUSDM`, delayed COMEX | Price context (display only) |

## Honesty box

- **Seed numbers are estimates.** Every mine row is tagged `basis: seed-estimate`
  with a source note; the extraction pipeline exists to replace them with cited
  values. PRs correcting any number are exactly the point.
- **The simulator propagates shocks through an explicit balance. It does not
  predict prices.** `price_pressure` is inventory-cover arithmetic, not alpha.
- **v1 simplifications are documented in the code**: annual resolution, no
  rerouting lags or regional inventory splits for tariffs, stranded exports
  treated as lost supply.

## Verification

- `uv run pytest` — engine invariants: mass conservation, outage monotonicity
  ("removing supply never increases the surplus"), zero-rate tariff identity,
  smelter-constraint binding, determinism, plus direction-and-magnitude
  backtests for every shipped scenario.
- Extraction accuracy is benchmarked against company-stated guidance
  (roadmap: published eval table).

## Web demo

`web/` is a zero-backend static site (GitHub Pages-ready — `pages.yml` deploys
it on every push): scenario sliders for the tariff rate and Grasberg severity,
a yes/no toggle on the Cobre Panamá decision, countdowns to both live June
catalysts, and the full ledger with per-row provenance. All runs are
precomputed by `opencopper export-web`; the "sliders" snap to a parameter grid.

## Roadmap

- [x] PDF exhibit support (most EX-96s are PDFs)
- [x] Batch extraction pipeline (Batches API) + reconcile + eval harness
- [x] Web demo: scenario sliders, live catalyst countdowns, ledger browser
- [ ] Run the batch over all ~2,400 copper EX-96 exhibits; publish the eval table
- [ ] Fill `evals/ground_truth.yaml` from source documents (hand-verified)
- [ ] MinMod ingestion for the NI 43-101 universe (SEDAR+ without scraping)
- [ ] Quarterly resolution; regional trade flows (the COMEX-LME arb properly)
- [ ] Monthly "model vs ICSG" balance updates

## License

MIT
