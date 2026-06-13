# References — the literature behind each mechanism

Every mechanism in this model has a home in the commodities-economics and
empirical-asset-pricing literature. This file maps paper → finding → where
it lives in the code. Where our numbers *disagree* with a published
estimate, the relevant YAML comment says so and the range records the
literature's value — disagreement is data, not embarrassment.

## Storage theory and price dynamics

- **Gustafson (1958); Williams & Wright (1991), *Storage and Commodity
  Markets*; Deaton & Laroque (1992, 1996)**, "On the Behaviour of Commodity
  Prices", *REStud/JPE*. Competitive storage makes prices spend long
  stretches near cost with occasional stockout spikes — right skew is
  structural, not anomalous. → the inventory-cover scarcity curve
  (`pricing.py`), the asymmetric clamp, and the tail-shape validation
  (`calibrate.tail_shape_check`: realized skew +0.62 vs simulated +0.55).
- **Gorton, Hayashi & Rouwenhorst (2013)**, "The Fundamentals of Commodity
  Futures Returns", *Review of Finance* 17(1) ([NBER
  w13249](https://www.nber.org/papers/w13249)). Convenience yield is a
  decreasing, NON-LINEAR function of inventories; low-inventory commodities
  carry **higher** futures risk premia. → the explanation of the backtest's
  central asymmetry: shorting tight markets fights a premium, it doesn't
  harvest a mispricing (`backtest.py`, the −95% short-tight drawdown).
- **Cashin, Liang & McDermott (2000)**, "How Persistent Are Shocks to World
  Commodity Prices?", *IMF Staff Papers*. Commodity shocks are long-lived;
  many have half-lives of years. → the per-commodity `half_life_months`
  (AR(1) on the trend deviation) in `opencopper backtest`.
- **Schwartz (1997)**, "The Stochastic Behavior of Commodity Prices",
  *J. Finance*; **Schwartz & Smith (2000)**. Mean-reverting short-term
  deviations around a long-term factor. → the trend-deviation signal is the
  reduced-form short-term factor; the half-life is its κ restated.

## Cross-sectional return predictability

- **Miffre & Rallis (2007)**, "Momentum Strategies in Commodity Futures
  Markets", *J. Banking & Finance* 31(6)
  ([SSRN 702281](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=702281)).
  13 profitable momentum strategies, ~9.4%/yr; 12-month sorts work; momentum
  buys backwardated contracts. → the 12m past-return sign in the value ×
  momentum 2×2.
- **Asness, Moskowitz & Pedersen (2013)**, "Value and Momentum Everywhere",
  *J. Finance* 68(3). Value and momentum coexist and interact in every
  asset class including commodities. → the pooled 2×2 table in
  `opencopper backtest`: glut+falling +9.0%/12m (deep value before the
  turn), balanced+rising +8.8% (momentum's home), tight+falling −7.0% (the
  only profitable short is an already-broken squeeze).
- **Gorton & Rouwenhorst (2006)**, "Facts and Fantasies about Commodity
  Futures", *FAJ*. Commodity futures earn equity-like premia with low
  equity correlation — context for treating the desk as a real asset class.

## Elasticities

- **Fally & Sayre (2018)**, "Commodity Trade Matters"
  ([NBER w24965](https://www.nber.org/papers/w24965)), **Table 1** — a
  meta-survey of published supply/demand elasticities for ~40 commodities.
  Most short-run estimates fall in 0.1–0.5 absolute; the modal estimate is
  under 0.2. → the `elasticity_*_range` entries in `data/seed/prices.yaml`
  now follow their Table 1 commodity-by-commodity (copper d 0.035–0.42,
  nickel s 0.133–2.03, coal d 0.3–0.7, manganese s >1.0, uranium s
  1.1–11.4*, …). Their Table 2: average yearly commodity vol ≈20% (minerals
  22.6%) — independently matching this model's 21.9% copper calibration
  target. *Where we disagree (cobalt supply: 1983-era estimates predate the
  ~98%-byproduct structure; uranium supply: pre-Kazatomprom era), the YAML
  comment records the tension and keeps our judgment in the point.
- **Roberts & Schlenker (2013)**, *AER*. Naive storable-commodity elasticity
  estimates bias toward zero; their IV estimates rarely exceed 0.4 absolute.
  → caution note in the prices.yaml header; ranges capped accordingly for ags.
- **Caldara, Cavallo & Iacoviello (2019)**, "Oil supply news and the global
  economy" / oil-elasticity compilation. Global short-run oil supply
  elasticity ≈0.1. → crude-oil `elasticity_supply: 0.10` cites it.

## Shock identification

- **Kilian (2009)**, "Not All Oil Price Shocks Are Alike", *AER*. Supply,
  aggregate-demand and precautionary-demand shocks move prices differently.
  → the model's structural split between country supply shocks
  (`CountrySupplyShock`) and demand-driver shocks (`DriverScenario`).
- **Hotelling (1931)**. Exhaustible-resource depletion. → the reserves
  depletion layer on cited mine reserves.

## Gold

- **Barsky & Summers (1988)**, "Gibson's Paradox and the Gold Standard",
  *JPE*; **Erb & Harvey (2013)**, "The Golden Dilemma", *FAJ*. Gold prices
  move inversely with long real yields; flow shocks are the wrong frame. →
  `gold.py`: −2.7%/100bp on 424 months of keyless data (BLS IQ12260 ×
  Cleveland Fed real rate), R² honestly ~2%, shown wherever gold appears.

## Pass-through (products layer)

- **Nakamura & Zerom (2010)**, "Accounting for Incomplete Pass-Through",
  *REStud* 77(3). Commodity-cost pass-through to retail is incomplete
  (~30% for coffee) and slow (~6 months), absorbed by markups and local
  costs. → `retail_passthrough` on bread (and the framing for all
  consumer-facing products).
- **Borenstein, Cameron & Gilbert (1997)**, "Do Gasoline Prices Respond
  Asymmetrically to Crude Oil Price Changes?", *QJE* 112(1). Near-complete
  pass-through within weeks, faster up than down ("rockets and feathers").
  → gasoline's `retail_passthrough` (share 1.0, ~2 months, asymmetry noted).

## Forecast evaluation

- **Meese & Rogoff (1983)**, "Empirical Exchange Rate Models of the
  Seventies", *J. Int. Economics*. Structural models rarely beat the random
  walk out of sample. → the design and the expected result of
  `opencopper benchmark`.
- **Diebold & Mariano (1995)**, "Comparing Predictive Accuracy", *JBES*. →
  the DM test on squared-error differentials (NW variance, lag h−1).

## Carry

- **Koijen, Moskowitz, Pedersen & Vrugt (2018)**, "Carry", *JFE* 127(2);
  **Gorton, Hayashi & Rouwenhorst (2013)** (above). Carry — the futures term
  structure (backwardation vs contango) — is the most robust commodity
  factor. -> `futuresdata.py` front-basis carry and the carry+value
  multi-factor book (`opencopper backtest --factors`).

## Forecast combination

- **Timmermann (2006)**, "Forecast Combinations", *Handbook of Economic
  Forecasting*; the "forecast combination puzzle" (simple equal weights beat
  estimated weights). → the benchmark's fixed 50/50 model+RW combination,
  weight declared a priori precisely so it cannot be fit to the test set.

- **Moskowitz, Ooi & Pedersen (2012)**, "Time Series Momentum", *JFE* (and
  Harvey et al. 2018, "The Impact of Volatility Targeting"). Scaling
  positions by trailing volatility raises futures-strategy Sharpe. → the
  vol-targeted sleeves (20%/36m/2× cap, uniform).
- **Grinold (1989)**, "The Fundamental Law of Active Management", *JPM*.
  IR ≈ IC × √breadth — why a single sleeve cannot exceed ~0.5 here and why
  breadth, not tuning, is the lever. → the Sharpe-arithmetic framing.

## Inference

- **Künsch (1989)**, "The Jackknife and the Bootstrap for General Stationary
  Observations", *Annals of Statistics*. Moving-block bootstrap preserves
  serial dependence. → the tranche strategy's Sharpe carries a 24-month-block
  bootstrap 90% CI and P(Sharpe≤0) instead of a bare point estimate.
- **Holm (1979)**, "A Simple Sequentially Rejective Multiple Test
  Procedure", *Scand. J. Statistics*. Step-down family-wise correction. →
  per-commodity slope t-stats are starred only if they survive Holm-
  Bonferroni across all 16 tests; the pooled claim rides the sign test.
- **Kendall (1954)**, "Note on Bias in the Estimation of Autocorrelation",
  *Biometrika*. E[ρ̂] ≈ ρ − (1+3ρ)/n. → resolved the model's documented
  autocorrelation "gap": a 34-observation realized estimate had been
  compared against 6-observation per-path estimates; with matched windows
  the simulation was on target all along (validation bug, not model bug).

## Risk measurement

- **Zangari (1996)**, RiskMetrics Monitor. Cornish–Fisher expansion adjusts
  Gaussian VaR quantiles for skew and kurtosis. → `book_risk`'s CF VaR
  beside the delta-normal number, with the book's realized P&L moments
  printed so the adjustment is auditable.
- **Murphy (1973)** decomposition / **Brier (1950)**. Probability scoring.
  → the thesis ledger's Brier score on probability-attached calls.

## Industry practice

- Wood Mackenzie-style **disruption allowance** (~5% of mine supply/yr) —
  the prior behind `DisruptionParams.disruption_mean=0.05`, calibrated
  against realized vol rather than taken on faith.

*Conventions: we cite the finding we USE, not everything in each paper;
working-paper versions linked where paywalls would otherwise block
verification. Corrections via PR welcome — every number above is checkable
against the cited table.*
