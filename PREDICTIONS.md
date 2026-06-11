# Prediction registry

Timestamped, falsifiable calls made by this model **before** the events
resolve, graded publicly after. A model that won't commit to numbers in
advance is an opinion generator; this file is the accountability mechanism.

Conventions: predictions are made from the model state at the stated commit;
conditional predictions resolve only if their condition occurs; each entry gets
a `RESOLVED:` line appended after the resolution source publishes — hits and
misses both stay in the file forever.

---

## #1 — 2026 world refined copper balance: deficit

**Made:** 2026-06-11 (commit `8164a50`) · **Resolves:** ICSG full-year 2026
balance, published ~April 2027.

The world-2026 composite (Grasberg recovery, Kamoa, El Teniente, smelter
closures) gives a 2026 refined balance of **−312 kt (P50)**, P10 −693 / P90
+61, with **P(deficit) = 86%**. Prediction: **ICSG reports a 2026 refined
deficit** (their own October 2025 flip was to −150 kt). Graded correct if the
final ICSG 2026 balance is negative; magnitude within the P10–P90 band is the
stretch goal.

## #2 — Copper price 2026: elevated, not collapsed

**Made:** 2026-06-11 (commit `8164a50`) · **Resolves:** IMF/FRED `PCOPPUSDM`
2026 annual average, final print ~Jan 2027.

The hindcast's structural variants bracket 2026 at **$9,225–$18,430/t**
(realized Jan–May average: $12,968). Prediction: the **2026 annual average
lands inside $11,000–$16,500/t** — above every pre-2026 annual average in the
series, and the model's central tendency sits near **$13,500**.

## #3 — June 30 tariff decision (conditional)

**Made:** 2026-06-11 (commit `8164a50`) · **Resolves:** US Commerce Section
232 refined-copper report due 2026-06-30, then COMEX–LME spreads over the
following quarter.

- **If refined cathode gets a tariff ≥15%:** the COMEX–LME premium re-blows
  past **10%** of the LME price within a quarter (the model's incidence on US
  import dependence), and US apparent demand falls vs trend.
- **If the exemption holds:** the premium compresses below 5% within a
  quarter as front-run inventories unwind.

## #4 — Cobre Panamá (conditional)

**Made:** 2026-06-11 (commit `8164a50`) · **Resolves:** Panama government
decision (expected June 2026) + First Quantum production reports.

- **If full restart approved:** ~**+120 kt** of 2026 supply (stockpile
  processing + ramp), cutting the model's P(2026 deficit) from 86% (world-2026)
  toward ~65%; under a calm baseline the same +120 kt cuts P(deficit) from 33%
  to 20%. Copper's 2026 price path flattens vs #2's central tendency.
- **If rejected:** no supply effect in 2026; watch arbitration headlines
  instead.

## #5 — Cobalt under the DRC quota regime

**Made:** 2026-06-11 (commit `8164a50`) · **Resolves:** USGS MCS 2027 price
note + trade-press annual averages, ~Feb 2027.

With the Oct 2025 quota decree binding through 2026 (~46% of world supply
withheld), the CES incidence saturates the model's honesty cap (**≥+300% vs
the $33k/t anchor**) — reality has leakage and stockpiles the model doesn't,
so the calibrated call is directional: **2026 average cobalt prices exceed
2024's by at least 50%** (2024: $16.77/lb US spot; 2025 already $21).
Falsified if 2026 averages land below ~$25/lb.

---

*Why publish these: commercial models hide their misses. This project's bet is
that an auditable model with a public scorecard compounds credibility faster
than a black box with a sales team.*
