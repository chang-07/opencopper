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
uv run opencopper sensitivity                                       # which assumption matters most
uv run opencopper export-web && python3 -m http.server -d web      # the interactive demo
```

`sensitivity` runs the one-at-a-time tornado over every world assumption. A
nice property of an explicit-constraint model: smelter utilization shows zero
swing in the baseline *because the market is concentrate-bound* — the tornado
exposes which constraints bind, not just which numbers are big.

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

### Running it for ~$0

The model and demo need **no LLM spend at all** — the seed ledger and simulator
are self-contained, and a real sample extraction is committed. Extraction is an
optional layer to upgrade seed values to cited ones, and it's cheap by design:

- **You don't need the whole corpus.** Replacing seed values for the ~30 mines
  that matter is a few dozen documents, not the ~2,400 on EDGAR.
- **Section pre-filter** keeps only the high-signal sections of each report —
  ~84% fewer input tokens (measured: 262K → 43K on two real filings).
- **Pick the tier.** `--model claude-haiku-4-5` is ~5× cheaper than Opus on
  input and fine for structured extraction; the Batches API halves it again.
- **See the bill first:** `opencopper estimate data/raw --model claude-haiku-4-5`
  prints token counts and cost for full vs pre-filtered, no API call.

```
$ opencopper estimate data/raw --model claude-haiku-4-5
2 documents, model claude-haiku-4-5
  full text:      ~   262,034 in-tokens  ->  $0.28
  pre-filtered:   ~    42,570 in-tokens  ->  $0.06
  + Batches API (50% off):                       $0.03
```

A realistic ~40-document fill lands near **$1** (Haiku, pre-filtered, batched);
the entire EDGAR corpus is **~$40**, not the ~$1,900 a naive full-text Opus run
would cost.

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
- Extraction is benchmarked against values hand-read from the source filings.
  A field counts as correct only if it is **within tolerance _and_ carries a
  citation** — an uncited right answer is scored as a miss. First real run, on
  Southern Copper's Cuajone Operations EX-96 ([sample](evals/sample_extractions/cuajone.json)):

  | mine | field | expected | extracted | within tol | cited | ok |
  |---|---|---:|---:|:-:|:-:|:-:|
  | Cuajone | reserves_kt | 6,560 | 6,560 | ✓ | ✓ | ✓ |
  | Cuajone | mine_life_years | 48 | 48 | ✓ | ✓ | ✓ |

  `reconcile` then diffs that extraction against the ledger and flags Cuajone
  at −14.4% (extracted LOM-average rate 137 kt vs the 160 kt current-year seed),
  confidence 0.35 — correctly surfacing a reserve-statement rate as *not* a
  current-production rate, for review rather than silent overwrite.

## Web demo

`web/` is a zero-backend static site (GitHub Pages-ready — `pages.yml` deploys
it on every push): scenario sliders for the tariff rate and Grasberg severity,
a yes/no toggle on the Cobre Panamá decision, countdowns to both live June
catalysts, and the full ledger with per-row provenance. All runs are
precomputed by `opencopper export-web`; the "sliders" snap to a parameter grid.

## Roadmap

- [x] PDF exhibit support (most EX-96s are PDFs)
- [x] Batch extraction pipeline (Batches API) + reconcile + eval harness
- [x] Section pre-filter + `estimate` command (~84% token cut; see "Running it for ~$0")
- [x] First real extraction + eval + reconcile on a live filing (Cuajone)
- [x] Web demo: scenario sliders, live catalyst countdowns, ledger browser
- [ ] Extend the batch + hand-verified ground truth to the ~30 ledger mines that file EX-96
- [ ] MinMod ingestion for the NI 43-101 universe (SEDAR+ without scraping) —
      API exists at `minmod.isi.edu/api/v1` (+ SPARQL at `/sparql`) but currently
      serves an incomplete cert chain; bulk CDR endpoint needs a token. Parked.
- [ ] Quarterly resolution; regional trade flows (the COMEX-LME arb properly)
- [ ] Monthly "model vs ICSG" balance updates

## License

MIT
