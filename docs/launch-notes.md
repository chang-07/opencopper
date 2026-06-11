# Launch notes

Working drafts for the public launch. Two formats for two audiences: the X
thread leads with the interactive spectacle; the HN post leads with the
engineering narrative (that's what each platform rewards).

## Pre-flight checklist

- [x] pushed — repo live at github.com/chang-07/opencopper, CI green
- [x] Pages enabled — auto-deploys on every push
- [x] demo live at https://chang-07.github.io/opencopper/ (OG card + deep links verified)
- [ ] Screenshots: World Simulator with DRC selected (`#sim=country,Congo%20(Kinshasa),50`),
      the cobalt Forecast fan, the Panama Decision toggle
- [ ] Timing: before June 30 (the Commerce tariff report) — both catalyst
      countdowns must still be live when this posts

## X thread draft

**1/** I built an open-source world model for commodity markets.

Click a country. Shock its supply. Watch the predicted price ripple across
copper, cobalt, lithium, nickel — with probability bands calibrated to 34
years of price history.

[screenshot: World Simulator, DRC selected]
[link with deep link: …/#sim=country,Congo%20(Kinshasa),50]

**2/** Why: every serious commodity supply/demand model (Wood Mackenzie, CRU,
S&P) is enterprise-priced and black-box. You get outputs, never assumptions.

opencopper takes the opposite bet: every assumption is a line in a YAML file
you can read, dispute, and PR.

**3/** It's three models in one:
- copper: mine-level, two-stage balance (concentrate vs smelters — where the
  treatment-charge crisis lives)
- 11 majors: country-level concentration + shocks (USGS data)
- ~8,300 deposits from DARPA's MinMod knowledge graph

**4/** The fun part: it simulates. Monte Carlo over mine disruptions + demand
surprises, with the dispersion tuned so the simulated world is exactly as
volatile as the real one (22% ≈ 22% realized since 1992).

The Grasberg mud rush → 80% probability of a 2026 copper deficit.

**5/** It also models the live policy events of 2025-26 as scenarios, with
parameters straight from USGS-stated facts:
- DRC cobalt export quotas (Oct 2025 decree): removes ~46% of world supply →
  the model pins its honesty cap: "≥ +300%"
- China REE export controls (Apr 2025)

**6/** And demand shocks are systemic: every commodity carries exposure shares
to global drivers (batteries, construction, grid…). One EV-slowdown event hits
lithium −36%, cobalt −45%, then nickel, rare earths, aluminum, zinc — through
shared demand, not hand-wired correlations.

**7/** Two findings from building it that I didn't expect:

a) Ingesting DARPA's deposit knowledge graph: 1.7% of records carried 90% of
the reported tonnage — unit-conversion errors, including one "deposit" holding
5× all world copper reserves. Machine-extracted data needs quarantine layers.

b) My own model refuted my talking point: a doubled AI-datacenter copper
demand slice out-moves the Grasberg disaster. I shipped the correction.

**8/** Everything is free and reproducible: USGS, SEC EDGAR filings (LLM
extraction with citations, scored 7/7 vs hand-verified ground truth), FRED
prices, MinMod. ~$0 to run.

Code, methodology, the lot: github.com/chang-07/opencopper

## Show HN draft

**Title:** Show HN: An open, auditable world model for commodity markets
(mine-level copper + 11 majors)

Commercial commodity supply/demand models (Wood Mackenzie, CRU, S&P) are
excellent and completely closed: enterprise pricing, black-box methodology,
outputs only. I built the opposite: every assumption is a YAML line you can
dispute, every extracted number carries a citation, and the validation suite
ships with the model.

What it does:

- Copper gets a mine-level engine with two coupled balances — concentrate vs
  smelter capacity (this is where 2025's negative treatment charges came
  from), then refined supply vs sector demand, with inventory cover driving an
  implied price. Backtested against the events of 2025-26; the engine's two
  structural variants bracket the realized 2026 price.
- Ten more majors run on a country-level tier built from USGS data:
  concentration (the DRC is 74% of cobalt), dominant-producer shock scenarios
  parameterized from real 2025 policy events, and exact constant-elasticity
  price incidence, clamped where the assumption breaks.
- A Monte Carlo layer draws thousands of futures with disruption dispersion
  calibrated so simulated volatility matches 34 years of realized FRED history
  (22% ≈ 22%). The country tier's noise sigma derives in closed form from each
  commodity's realized vol — calibration by construction.
- An interactive world map: click a producer country, shock its supply (or
  shock a demand driver like batteries), and see the predicted cross-commodity
  price ripple with spike odds. State lives in the URL, so any dialed-in shock
  is a sendable link.

Things I learned the hard way (all documented in docs/methodology.md):

- DARPA's CriticalMAAS deposit knowledge graph is wonderful and 1.7% of its
  copper records carried 90% of the reported tonnage — upstream unit errors.
  The ingester quarantines on per-commodity physics (ceilings, grade bounds,
  grade×tonnage consistency).
- A lagged price-feedback loop on a thin-inventory model is cobweb-unstable
  (loop gain > 1). Feedback ships as a stable deterministic opt-in; the Monte
  Carlo gets its volatility honestly from calibrated surprise draws.
- The linear elasticity-incidence rule everyone quotes produces impossible
  numbers on big shocks (a price "falling 113%"). The exact CES form fixes it,
  and a 4× clamp marks where constant elasticity itself stops being credible.

Stack: Python (pydantic, httpx, plotly), zero-backend static demo, data from
USGS / SEC EDGAR / FRED / MinMod — all free. LLM extraction is used for
reading 300-page technical filings, with citations mandatory and an eval that
scores uncited answers as wrong.

## Distribution after launch

- Ramp Developer Community: post the verification/reconcile architecture
- Stripe ACP repo: issues informed by the API design work
- Robinhood Agentic Trading team: the simulator as a sandbox complement
- Monthly cadence: model-vs-ICSG balance updates; scenario post-mortems when
  catalysts resolve (June 30 tariff report first)
