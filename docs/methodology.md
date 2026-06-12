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

**Country tier — constant-elasticity incidence.** No inventory state, only
flows, so short-run partial equilibrium with constant-elasticity curves. A
supply withdrawal of fraction `k` (or a demand shift of `g`) moves price by

```
P/P₀ = (1−k)^(−1/(η_d+η_s))        supply shock
P/P₀ = (1+g)^(+1/(η_d+η_s))        demand shock
clamped to [0.25×, 4×]
```

The familiar linear rule `%ΔP = k/(η_d+η_s)` is this curve's tangent at zero —
we use the exact form because the linearization produces impossible numbers on
large shocks (a 22% demand collapse against a 0.20 denominator "implies" a
price falling 113%). The clamp is the same philosophy as the copper cover
curve: beyond 4× the constant-elasticity assumption has certainly broken
(substitution, rationing, stockpile release), so the model reports the bound —
"≥ +300%, model range" — rather than printing a fake 900%. Metals are
inelastic in the short run, so this is where concentration becomes price: the
DRC cobalt quota withdraws ~46% of world supply against a combined elasticity
of 0.20 and saturates the clamp. Each reported move also carries closed-form
spike odds — P(price exceeds 2× anchor within the year), lognormal around the
shock-implied level with sigma = the commodity's ambient realized volatility.

**Demand drivers — systemic cross-commodity shocks.** Every commodity seed
carries exposure shares to global demand drivers (batteries, construction,
grid, transport, electronics, PV…), coarse splits from the USGS uses notes. A
`DriverScenario` compiles down to per-commodity demand shocks through those
exposures, so one event propagates across markets the way real downturns do:
an EV stall (batteries −25%, transport −10%) hits lithium −22% demand / −36%
price, cobalt −11% / −45%, nickel, rare earths, aluminum, zinc, iron ore and
copper all at once — cross-commodity correlation through shared demand, not
hand-wired pairwise links.

**Live anchoring.** Where a keyless FRED series exists (copper, nickel,
aluminum, zinc, tin, iron ore — the IMF global price series), the demo shows
the live market price beside the anchor. Gold is deliberately excluded from
shock pricing: its price is set by monetary demand against a 200,000+ tonne
above-ground stock, so a flow-shock model is the wrong tool, and the code says
so rather than producing a confident wrong number.

## From deterministic to stochastic: states, simulation, calibration

A deterministic run answers "what does this exact scenario do." A world model
must also answer "what is *likely*" — so three layers sit on top of the engine:

**1. Historical states (history.py).** Thirty-four years of FRED monthly
prices (1992–present) define empirical market regimes: every month is
classified **glut / balanced / tight** by its price relative to a 36-month
trailing trend (±15% thresholds). Copper has spent 17% of that time in glut,
52% balanced, 32% tight — and is TIGHT today. Realized annual volatility
(copper: 21.9%) becomes the calibration target. An internal-consistency check
fell out for free: aluminum, the commodity with the most elastic supply (idle
smelters), shows the lowest realized volatility (17.1%) — the elasticities and
history agree without being told to.

**2. Monte Carlo (montecarlo.py).** Each simulated year draws an aggregate
mine-disruption fraction from a right-skewed Gamma (most years calm, some bad)
and a Normal demand surprise, both applied through the full balance engine, for
thousands of paths. Crucially the draws are **mean-zero surprises around the
expected disruption** the baseline already carries — so the median path tracks
the deterministic run and only the spread is stochastic. Outputs are
distributions: P10/P50/P90 bands for balance, cover, and implied price, plus
P(deficit) and P(price > 1.5× anchor) by year. Seeded, hence exactly
reproducible.

**3. Calibration (calibrate.py).** One dispersion knob (a single scale on
disruption CV + demand sigma) is bisected until **simulated annual price
volatility (22.2%) matches realized (21.9%)**. The disruption *mean* is held
at its physical ~5%; only the surprise dispersion is tuned. A model whose
generated world is as volatile as the real one — with a fat right tail, as
commodity prices actually have — has earned its uncertainty bands.

Two honest limits, found by trying: the implied price **clamps at 3× anchor**
(beyond that the cover curve is extrapolating into air, and severe composite
scenarios like world-2026 saturate it — the model says "very high" rather than
inventing a number); and the **lagged price-feedback loop is cobweb-unstable**
under Monte Carlo (thin inventory + convex cover curve give it loop gain > 1),
so feedback ships as a stable deterministic opt-in (gradual partial-adjustment
of demand destruction and scrap response) while MC volatility comes from the
calibrated surprise draws alone.

**Country-tier Monte Carlo** extends the fans to every priced commodity. With
no inventory state, each simulated year's price is the CES-incidence response
to that year's net tightening (scenario supply loss + demand change + noise),
and the noise sigma is **derived in closed form** from the commodity's realized
volatility: for small shocks the price return is `(x_t − x_{t−1})/(η_d+η_s)`,
so `σ_x = vol·(η_d+η_s)/√2` reproduces the realized vol by construction —
verified in tests (nickel: simulated 29.1% vs realized 28.3%), no fitting loop
needed. Extreme scenarios pin the 4× clamp and the fan goes flat-topped: the
model's way of saying "at least this" rather than extrapolating.

**The World Simulator** (web) composes the layers interactively: producer
countries plotted by **criticality** (Σ share² across commodities — China 1.10,
DRC 0.58, Indonesia 0.51), click-to-shock with a severity slider, and the
predicted per-commodity price ripple via elasticity-incidence with a ±25%
elasticity-uncertainty band, each card tagged with the commodity's *current
historical regime*. The Forecasts tab shows the Monte Carlo fan per scenario.

## The regional layer: quarterly trade flows and the COMEX–LME arb

The annual engine clears the world; `regional.py` disaggregates it into
US / China / RoW at **quarterly** resolution and lets the regions trade —
because the 2025-26 copper story was regional and a global model structurally
cannot see it. Mechanics: explicit refined-supply and demand shares per
region; regional inventories; a region's premium rises with its cover
shortfall (clamped at storage-arb bounds, −15/+60%); **structural deficits are
met by continuous contracted baseline flows** (without which a premium-chasing
controller oscillates famine/flood — the first version did, and the test suite
now forbids it); marginal cargoes re-route toward the premium with a
one-quarter shipping lag and a 1% dead band.

A tariff enters twice, honestly: as a **wedge priced into the US premium**
(the marginal imported ton pays it, so post-tariff the premium pins at ~the
tariff rate — the arb-with-a-tax steady state), and as **anticipation** —
announced tariffs pull US demand forward in the prior quarters and pay it back
after, demand-conserving. The simulated shape under the June-30 scenario is
the observed 2025 sequence: front-run spike → effective-date unwind → pin at
the wedge. Documented simplifications: flat intra-year supply/demand (no
seasonality data), no regional demand elasticity (world surpluses park in RoW
as late-horizon discounts), three regions only.

## The desk layer

`opencopper signals` (and the demo's Desk tab + ticker strip) put the model
next to the live market: latest FRED/Pink Sheet price vs the balanced-market
anchor, the 34-year regime, simulated 2026 medians and tail odds, and futures
contract mapping (COMEX/LME/SGX/CME symbols) for reference. It is decision
support with a hard boundary: the project never sizes, recommends, or executes
trades, every output carries the not-advice disclaimer, and PREDICTIONS.md is
the public scorecard that keeps the signals honest.

## Products: the model pointed at things people actually buy

A product seed (`data/seed/products/`) is a bill of materials — input
quantities in the pricebook's own units — plus an anchor cost from public
intensity figures (BNEF/IEA for batteries, worldsteel for steel, EIA for
gasoline, USDA farm-share for bread). Three outputs per product:

- **Cost structure at anchors** — copper cable is ~80% metal, an EV battery
  pack ~27% raw commodities, US gasoline ~52% crude, bread ~5% wheat. The
  spread between those numbers is most commodity punditry done
  quantitatively: cable buyers hedge the LME, bakers don't hedge wheat.
- **Live input-cost pressure** — the BOM repriced at the latest monthlies.
- **Shock response** — Δproduct% = Σ shareᵢ × ΔPᵢ, composed with everything
  upstream: a DRC copper outage reaches the battery pack mostly through the
  **cobalt byproduct channel** (linkage graph → incidence → BOM), which no
  single-commodity view would show.

Stated everywhere it appears: this is INPUT-COST passthrough with non-input
costs and margins held fixed — a cost-base model, never a retail price
prediction. The 14→22 commodity expansion (lead, platinum, uranium, coal,
corn, soybeans, graphite, manganese) exists partly to make these BOMs
honest: graphite and manganese have no keyless monthly series (documented
default vol, like cobalt), and the steel model prices met coal at the
Newcastle thermal benchmark with the premium named as unmodeled. The
expanded pool also moves the concentration story: **graphite (China ~76%)
now outranks cobalt's DRC share as the most concentrated commodity in the
model**, and platinum (South Africa 72%) joins the chokepoint club.

Scenario composition: `ripple_events` generalizes the linkage propagation to
multi-event scenarios (Hormuz's multi-country withdrawal aggregates into one
incidence pass per commodity), so any shipped scenario prices any product.

## The thesis ledger: the system grades its own calls

PREDICTIONS.md is prose; `data/theses.yaml` is its machine-readable twin,
and `opencopper theses` marks every entry to market:

- **Markable metrics grade themselves.** The copper-2026 band call reads its
  own YTD average off FRED every run and prints provisional standing
  ("12,968 over 5 months — inside") until the year completes, then grades
  itself permanently. Resolution dates and readings are recorded.
- **External metrics can't pretend.** Calls that resolve via ICSG/USGS
  publications stay OPEN until a `resolution:` block with a cited source is
  added — and once the deadline passes unresolved they flip to NEEDS-RES and
  glow on the scorecard until someone owns the grade.
- **The news pipeline's calls are tested automatically.** Every distinct
  rule-matched supply event becomes an auto thesis — "price prints ≥+5% vs
  entry within 6 months" — with the entry price snapshotted at creation.
  The first monthly print over the threshold resolves it; a passed deadline
  is a MISS that stays forever. Commodities without a keyless monthly series
  are skipped: untrackable claims aren't theses, they're prose.
- **Analytics:** hit rate over resolved theses, Brier score wherever the
  model attached a probability at creation, and the open auto-theses' paper
  move. The daily Action re-marks everything and ships the scorecard in the
  brief and on the demo's Scorecard tab.

This is the performance test of the *generated* theses, not just the
hand-made ones: when the news→simulation loop fires, the ledger records what
happened next, every time.

## Data freshness: one place where staleness is visible

Five external surfaces feed the model (FRED, Pink Sheet, USGS seeds, MinMod,
Google News). `opencopper data status` shows every cache's age, row count,
and latest data date in one table; `opencopper data refresh` force-refetches
the fetchable ones. FRED/Pink Sheet caches carry a TTL (serve fresh, refetch
stale, fall back to the stale file when the network fails — a days-old quote
beats none as long as its date travels with it). The status board is also
where honest awkwardness surfaces: the Pink Sheet's monthly file trails FRED
by months, which is why silver's "live" price wears its date on the desk.

## Literature grounding

Every mechanism maps to the published literature — storage theory
(Deaton-Laroque; Gorton-Hayashi-Rouwenhorst), mean reversion and its
half-life (Schwartz; Cashin-Liang-McDermott), cross-sectional value ×
momentum (Asness-Moskowitz-Pedersen; Miffre-Rallis), elasticity surveys
(Fally-Sayre Table 1, which the prices.yaml ranges now follow
commodity-by-commodity), pass-through (Nakamura-Zerom; Borenstein et al.),
and risk measurement (Zangari's Cornish-Fisher). The full mapping of paper →
finding → file, including the places where this model DISAGREES with a
published estimate and says so, lives in [references.md](references.md).
Two independent cross-checks worth noting: Fally-Sayre report average
minerals volatility of 22.6%/yr — this model's copper calibration target,
derived separately from FRED, is 21.9%; and GHR's inventory-premium result
is the storage-theory explanation for the backtest's central finding that
tight markets are not safely shortable.

## Evidence before opinions: backtest, conditional vol, parameter bands, book risk

A model usable for real decisions has to show its homework. Four pieces
(`opencopper backtest`, the desk sheet's evidence columns, `--risk`):

**The regime signal is backtested, walk-forward, against 34 years.**
At every month the trailing-trend deviation (the exact statistic that
defines regimes, causal by construction) is regressed on the forward
12-month return, with Newey-West (Bartlett, lag h−1) standard errors because
overlapping windows induce MA(h−1) errors. Result: 9/10 commodities have
negative slopes (mean reversion; sign-test p≈0.02, optimistic since
commodities correlate), gas/aluminum/tin/nickel individually significant.
Crucially the parameters (36m window, ±15%) predate the backtest — they were
chosen to describe regimes, not fitted to returns.

**The legs are asymmetric, and that is the finding.** Conditional on glut,
12m forward returns averaged +12% to +21% across nearly every commodity;
conditional on tight, they are small or negative — but a short-tight rule
loses badly (−95% max drawdown in the equal-weight version), because tight
markets carry the right-tail squeeze risk the spike-odds machinery prices.
A deterministic toy reproduces this (`test_quant.py`): in a perfectly
mean-reverting sine world the trailing trend lags the cycle, so "tight"
months sit mid-ascent and monthly-gated shorts lose even though 12m stats
mean-revert. The signal is horizon-dependent; any use of it must be too.

**The backtest hunts its own biases** (`opencopper backtest --robustness`).
Four threats, four answers, all printed:

1. *Averaging bias (Working 1960)*: FRED/Pink Sheet values are monthly
   AVERAGES, mechanically correlating the signal month with the next month.
   The skip-month convention (signal at i, outcomes start at i+1) is now the
   default; the naive variant ships beside it. The finding survives — and
   the trading rule actually IMPROVED with the bias removed (the averaging
   overlap was anchoring entries adversely).
2. *Selection bias*: the (36m, ±15%) regime parameters are swept over a
   window × threshold grid, nominal and CPI-deflated (FRED CPIAUCSL).
   14/16 commodities mean-revert in every one of the nine slope variants,
   and the glut/tight forward-return gap WIDENS monotonically with the
   threshold (+8.3%→+11.7% glut; +1.1%→−2.2% tight) — dose-response, not
   artifact.
3. *Data-mining bias*: the value × momentum cells were examined after
   seeing the data, so the split-sample re-estimates them per half. Both
   halves hold (pre-2010: glut|down +13.9%, tight|down −2.9%; post-2010:
   +6.5% vs 0.0%), with honest attenuation post-2010 — consistent with the
   alpha decay documented for published commodity signals.
4. *Pooling bias*: pooled cells over-weight long series (silver and crude
   carry 744 months). Equal-weighted per-commodity consistency: fwd|glut >
   fwd|tight for 13/16 commodities (sign p=0.02); the sharper 2×2 contrast
   is 4/4 but underpowered and says so.

Survivorship is the one bias we can only document, not fix: the pool is
today's 22 commodities. Mitigating facts: the series are continuous
(1960/1992 starts, nothing delisted), and commodities don't exit the way
stocks do.

**The strategy matches the evidence horizon** (`opencopper backtest
--tranche`). The monthly-gated rule exits when a glut reclassifies — just as
the rebound starts — so the evidence-faithful construction is
Jegadeesh-Titman (1993) overlapping tranches: each month's signal opens a
12-month hold, capital is the average of active tranches. Pre-declared
variants, all shown: long-glut (Sharpe 0.33), glut-while-falling (0.34), and
the value+momentum combination long glut + balanced-with-momentum (**Sharpe
0.54 gross, 0.52 net of 25bps**, consistent halves 0.52/0.59 — the
Asness-Moskowitz-Pedersen diversification on our data). Long-only by
construction: the diagnostics showed the short side is a risk premium.
Turnover ~0.2-0.5×/yr makes costs a rounding error; exposure averages
17-43% of capital because gluts are rare — selectivity is the strategy.
Roll yield is not captured (spot-proxy monthly series); levels are
indicative, the shape is the finding.

**The data audits itself** (`opencopper data check`, run by CI and by the
daily Action before anything publishes — clean data or no publish). Every
series is checked for date order, duplicates, gaps, non-positive prices, and
>75%-log monthly jumps (the one current warning is January 1974 Brent — the
OPEC embargo, i.e. the checker correctly flagging the most violent real move
in the dataset); anchors more than 3× from the live price are flagged
stale; news/theses receipts must parse and reference known commodities.
FAIL-level findings (numbers the model would silently mis-use) fail the
build.

**Claims carry error bars and corrections.** The tranche strategy's Sharpe
is reported with a moving-block bootstrap 90% CI (Künsch 1989; 24-month
blocks preserve regime clustering) and P(Sharpe≤0) — currently [0.25, 0.82]
and 0.2% — plus a Newey-West t on the mean (3.1). Per-commodity slope
t-stats are starred only if they survive Holm-Bonferroni across all 16
tests (4 names: gas, aluminum, tin, nickel); the pooled claim is held to
the cross-commodity sign test. And the previously documented MC
autocorrelation "gap" (+0.13 realized vs −0.03 simulated) dissolved under
matched estimators: per-path AR(1) at n=6 is biased by ~(1+3ρ)/n (Kendall
1954), and chopping the realized series into same-length windows gives
−0.08 vs the simulation's −0.03 — the validation was biased, not the model.
Fixing the test rather than re-tuning the parameter is the methodological
point.

**Volatility is conditioned on the state.** Realized vol bucketed causally
by regime (regime at month i−1, return over month i) is U-shaped: extreme
states are volatile states (crude: 41% in glut, 22% balanced, 39% tight).
The desk shows the current regime's vol, and the CLOSED-FORM spike odds
(simulator cards, commodity reports) now use the conditional number too —
the Monte Carlo alone stays calibrated to UNCONDITIONAL vol, since its
scenarios already move the state and conditioning would double-count.

**Elasticities carry ranges, not just points.** Where the literature bounds
a short-run elasticity, `prices.yaml` seeds a range, and every incidence
output shows the band (the CES multiple is monotone in η_d+η_s, so the band
endpoints are exact — no sampling). The bands are deliberately loud: a −7%
copper supply shock is "+18%" at the point but +10..+44% across the ranges.
When the band is wide, the elasticities are doing the work, not the event.
A missing band means nobody has bounded that parameter yet — also
information.

**Book risk is measured with correlations, and labeled a floor.**
`opencopper book --risk` runs delta-normal 1-month VaR/ES on the declared
book from the historical covariance of aligned monthly returns (copper–crude
correlation ≈0.34 is measured, not assumed). Stated twice over as a floor:
returns are fatter-tailed than normal, and FRED/IMF monthly *averages*
smooth intramonth swings. Positions without a price series are excluded and
say so. Risk measurement of a declared book — never sizing, never advice.

## Cross-commodity linkages: shocks don't stop at one market

`data/seed/linkages.yaml` is a small typed graph; `opencopper ripple` (and
every news-driven event) propagates a shock through it in **one first-order
round**:

- **byproduct** — a host-commodity outage drags a dependent mined alongside
  it. Cobalt rides DRC copper: coupling 0.70 means 70% of the copper cut
  hits cobalt supply, scaled by the country's share of dependent output.
  This is the channel the independent tiers structurally miss — a 50% DRC
  copper outage is a ~17% copper move but a clamp-the-model cobalt squeeze.
- **substitution** — a sustained price rise shifts demand to the substitute
  (copper→aluminum 0.12: a +20% copper move adds ~2.4% to aluminum demand,
  priced through the same CES incidence).
- **input_cost** — pass-through from input price to output price
  (natural-gas→aluminum 0.20 via smelting power; crude-oil/gas→wheat via
  fuel and fertilizer).

One round only, deliberately: second-round terms are smaller than the
couplings' uncertainty, and iterating to a fixed point would imply precision
the seed-estimates don't have. Couplings live in YAML next to their sources
and are exactly as disputable as every other seed number.

## News ingest: headlines drive the simulator, keyless and unattended

`opencopper news` is the autonomy loop (`.github/workflows/daily-brief.yml`
runs it every morning):

1. **Fetch** Google News RSS searches (no API key) for a fixed feed list.
2. **Filter** to the last 14 days — search RSS resurfaces months-old stories.
3. **Match** against transparent keyword rules (`data/seed/news_rules.yaml`):
   a rule fires when every term group matches (groups support `|`
   alternation, plain substrings, never regex). Each rule maps to a country
   supply event with a **prior severity** — Grasberg incident → Indonesia
   copper −30%, Hormuz → Gulf crude −20%.
4. **Simulate**: each distinct event is priced through incidence + linkages;
   corroborating headlines group under one event instead of repeating it.
5. **Publish**: a dated brief (`docs/briefs/`), machine-readable hits
   (`data/news/`), and the Wire strip on the demo's Desk tab.

The honesty contract: severities are **priors, not measurements** — the
pipeline cannot read "force majeure, 35 kt for six weeks" out of prose
without an LLM, and keeping the loop keyless and deterministic was the
point. So the brief always prints the headline beside the simulated number,
the rule that fired, and the prior it assumed; the human judges relevance.
A rule that references a country the seeds don't know flags the headline
rather than crashing the unattended run.

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
- **Hindcast with structural brackets:** `opencopper validate` compares the
  model's implied annual copper price to the realized FRED average for every
  overlapping year, running the world-2026 composite in both structural
  variants — no-feedback (inventory drains undamped) and full-feedback
  (demand/scrap adjust at the modeled speed). 2024 lands within 3%; in the
  crisis year the two variants bracket the realized price (9,225 ↔ 18,430
  around 12,968), and the width of that bracket is the model's honest
  structural uncertainty, stated rather than hidden.

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
