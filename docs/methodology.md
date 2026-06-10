# Methodology

opencopper is an open supply/demand world model for mined commodities. The
stance that drives every design choice: **a model you can't audit is an
opinion with a paywall.** Every constant lives in a YAML file, every extracted
number carries a citation, every simplification is written down, and the
validation suite is part of the artifact.

This document is the formal description. The honesty box in the README is the
short version.

## Architecture: three tiers, three resolutions

| Tier | Resolution | Commodities | What it can answer |
|---|---|---|---|
| **Mine-level engine** | 31 mines + RoW aggregate, annual | copper | Shock propagation: outages, restarts, smelter closures, tariffs, sector demand shocks |
| **Country tier** | USGS country production/reserves | 11 majors | Concentration (HHI), dominant-producer restrictions (export bans/quotas), balance *drift* |
| **Deposit layer** | ~8,300 deposits w/ grade-tonnage | 6 (Cu, Ni, Zn, Co, Li, REE) | Where the endowment is; the development pipeline. Deposits, never production |

Each tier models only what its data can support. The copper engine gets
absolute balances because it has secondary-supply structure; the country tier
reports **drift relative to its anchor year** because mine supply and
consumption sit on different bases (scrap fills copper's gap, recycling fills
silver's); the deposit layer never enters a balance at all.

## The copper engine

Two coupled annual balances, because the market clears in two stages:

**1. Concentrate.** Mine supply splits into concentrate (smelter feed) and
SX-EW cathode (skips smelters):

```
mine_supply(y)   = Σ_tracked production_i(y) + RoW(y) · (1 − disruption_allowance)
RoW(y)           = world_trend(y) − Σ_tracked baseline_i(y)
concentrate(y)   = Σ_tracked production_i(y)·(1−sxew_i) + RoW(y)·(1−sxew_world)
smelted(y)       = min(concentrate(y), smelter_capacity(y) · utilization_max)
tc_pressure(y)   = −(concentrate(y) − capacity·util) / concentrate(y)
```

`tc_pressure > 0` means concentrate is scarce relative to smelting appetite —
the regime that took treatment charges to $0/negative in 2025-26. The
disruption allowance applies to the RoW aggregate only; tracked mines get
explicit events instead (no double-counting).

**2. Refined.**

```
refined_supply(y) = smelted(y) + sxew(y) + secondary(y)
demand(y)         = Σ_sectors base·share_s·(1+g_s)^(y−2024) · shock_multipliers_s
balance(y)        = refined_supply(y) − demand(y)
inventory(y)      = max(0, inventory(y−1) + balance(y))
price_pressure(y) = (baseline_days − inventory_days(y)) / baseline_days
```

Demand growth is **composed from end-use sectors** (construction, grid,
transport, machinery, datacenters, other) rather than a blended rate, so mix
shift is explicit and sector shocks are first-class. Trade regions carry
consumption shares only and exist for tariff geometry.

`price_pressure` is inventory-cover arithmetic. It is deliberately **not** a
price forecast; the model refuses to know things it cannot know.

**Shocks** are typed, parameterized events composed in YAML scenarios:
`MineOutage` (severity window + linear recovery), `MineRestart` (ramp
schedule), `SmelterClosure`, `Tariff` (regional premium proxy + elasticity
demand effect; rerouting lags documented as not modeled), `DemandShock`
(global or sector-targeted), `ExportBlock`. Each event's simplifications are
documented on the event class itself.

## The country tier (11 commodities)

Sources: USGS Mineral Commodity Summaries (public domain), one seed file per
commodity with world production (two stated years), reserves, top producers,
and a stated-or-proxy demand anchor. Growth rates are flagged seed estimates.

```
supply(y) = latest_stated · (1 + g_supply)^(y − latest_year) − Σ shocks
shock     = supply(y) · country_share · severity        (while active)
drift(y)  = (supply(y) − demand(y)) − (supply(anchor) − demand(anchor))
```

Concentration is the tier's real product: country shares, top-1/top-3, and a
**lower-bound HHI** (unlisted remainder treated as atomized). The 2025-26
policy events this parameterizes are not hypothetical: the DRC's cobalt export
quotas (Oct 2025 decree) and China's REE export controls (Apr 2025) ship as
scenarios with their parameters derived from the USGS-stated facts.

## Pricing: scarcity → price, two mechanisms, never a forecast

The price layer maps the model's scarcity signal to an implied price. It is
explicitly **illustrative, not predictive** — the goal is to show what the
mechanics *imply*, with every elasticity and curve parameter a disputable
constant in `data/seed/prices.yaml`. Each tier uses the mechanism its data can
support.

**Copper — inventory-cover scarcity curve.** Copper has stock dynamics, so the
right state variable is inventory cover (days of consumption), which integrates
the balance over time: a persistent deficit drains inventory, and *that* moves
price, not a single year's flow.

```
implied_price = anchor · clamp( (baseline_days / cover_days)^γ , lo, hi )
```

`anchor` is the balanced-market price (≈ LME average at ~12-day cover), not the
latest spot. γ = 0.7 means a halving of cover implies ~+62%; the clamp keeps
the implied price within 0.4–3.0× anchor. The curve mean-reverts as inventory
rebuilds — the world-2026 scenario implies a 2026 spike that fades by 2030.

**Country tier — elasticity-incidence.** No inventory state, only flows, so
short-run partial equilibrium: a supply withdrawal of fraction `k` raises price
by

```
%ΔP = k / ( |elasticity_demand| + elasticity_supply )
```

Metals are inelastic in the short run, so this is where concentration becomes
price: the DRC cobalt quota withdraws ~46% of world supply against a combined
elasticity of 0.20, implying a ~+230% move. That is not a model artifact — it
is why an inelastic byproduct with one dominant producer is dangerous, and it
matches cobalt's actual 2025 behavior in direction. The mechanism's
simplifications (no substitution dynamics, no destocking, no processing
bottleneck, symmetric up/down) are stated wherever it is reported.

**Live anchoring.** Where a keyless FRED series exists (copper, nickel,
aluminum, zinc, tin, iron ore — the IMF global price series), the demo shows
the live market price beside the anchor. Gold is deliberately excluded from
shock pricing: its price is set by monetary demand against a 200,000+ tonne
above-ground stock, so a flow-shock model is the wrong tool, and the code says
so rather than producing a confident wrong number.

## Data layers and the provenance ladder

Every quantity carries a `basis` tag and can only move up the ladder with
evidence:

1. **seed-estimate** — hand-seeded from public reporting, rounded, sourced
2. **extracted** — machine-read from a primary document, with a verbatim
   citation and a confidence; produced by LLM extraction (EX-96 filings) or
   ingested from MinMod
3. **verified** — a human accepted the diff against an independent source

Two rules keep the ladder honest:

- **`reconcile` never overwrites.** Extracted values are diffed against the
  ledger and discrepancies surface for review with confidence attached.
- **`eval` scores uncited answers as wrong**, even when numerically right.
  (Current extraction eval: 7/7 fields across five SEC filings.)

## Quarantine: machine-extracted data is guilty until proven consistent

Ingesting MinMod (DARPA CriticalMAAS, ~8,300 deposits across six commodities)
produced the project's clearest lesson: **1.7% of copper records carried 90%
of the reported tonnage** — upstream unit-conversion errors, including single
"deposits" larger than USGS's estimate of all world reserves. Three checks,
each with per-commodity physics (a fine zinc grade is an impossible lithium
grade):

1. **Unit ceiling** — contained metal above the largest credible single
   deposit for that commodity (e.g. 150 Mt Cu, 30 Mt Ni)
2. **Grade plausibility** — outside realistic ore-grade bounds for the
   commodity's deposit classes
3. **Internal consistency** — `contained ≈ tonnage × grade` within 15%;
   when the triple disagrees, at least one number is wrong and we cannot
   know which

Quarantined records are excluded from aggregates, ledger matching, and maps,
and the report states exactly what was excluded and why. The same artifact
class appears in PDF text extraction itself: USGS table footnote markers fuse
into numbers ("⁹25,000,000" reads as 925,000,000). The extraction agents
resolve these by cross-summing country rows against stated world totals —
a check that is itself part of the methodology.

## Calibration anchors

ICSG (world refined production/usage, monthly), USGS MCS (mine production,
reserves, by country), company disclosures (tracked-mine production), SEC
EX-96 filings (reserves, mine life — cited), MinMod (deposit endowment).
Copper baseline is tuned so the counterfactual trend stays near balance over
2024-2030; scenario *deltas* are the model's primary output and are insensitive
to the level calibration.

## Validation

- **Invariants (property tests):** mass conservation (inventory change equals
  cumulative balance), outage monotonicity (removing supply never increases
  the surplus), zero-rate tariff identity, smelter constraint binding,
  determinism, share-sum validation on demand composition.
- **Backtests with direction-and-magnitude bounds:** Grasberg 2025 mud rush,
  Cobre Panamá restart arithmetic, the 2026 composite (model: −354 kt vs
  ICSG's revised −150 kt forecast — right direction and order).
- **Extraction evals** against values transcribed from source documents.
- **Self-falsification is kept, not hidden:** the model's own arithmetic
  refuted this project's draft claim that the AI-datacenter boom is smaller
  than a mine accident — a *doubled* datacenter slice (+~500 kt) slightly
  out-moves the Grasberg outage (−372 kt). The corrected claim ships in the
  scenario file. A model whose author edits his priors when the model
  disagrees is the product working as intended.
- **Sensitivity tornado** over every world assumption; zero-swing rows expose
  which constraints bind (smelter utilization doesn't matter while the market
  is concentrate-bound).

## Known simplifications (consolidated)

Annual resolution; no rerouting lags or regional inventory splits for tariffs;
stranded exports treated as lost supply; no price feedback into supply or
demand (elasticities appear only inside the tariff event); country tier has no
secondary supply (hence drift, not balance); REE modeled as REO-equivalent
(the processing bottleneck — the actual chokepoint — is not modeled); gold
included with the explicit caveat that flow models are the wrong tool for a
stock-driven monetary asset; deposit data is research-grade machine extraction
surviving quarantine, not audited reserves.

Each of these is a PR-able boundary, which is the point.
