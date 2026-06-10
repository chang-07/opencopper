# Contributing

The whole point of opencopper is that the model is disputable. The highest-value
contributions are corrections, not features.

## Dispute a number

Every model input lives in two YAML files:

- [`data/seed/mines.yaml`](data/seed/mines.yaml) — per-mine capacity, production,
  SX-EW share, status
- [`data/seed/assumptions.yaml`](data/seed/assumptions.yaml) — world totals,
  smelting capacity, demand growth, inventory baseline

To correct one: PR the change with a **source** (company report, USGS, ICSG —
link it in the `sources` list or the PR description). If you can cite the exact
table, the merge is easy. Upgrading a row's `basis` from `seed-estimate` to
`verified` requires a citation to a primary document.

## Add a mine

Copy any row in `mines.yaml`. Required: `name`, `country`, `owner`,
`capacity_kt` (kt contained copper), one `production_kt` actual, a source note.
Keep `basis: seed-estimate` unless you cite a primary document. `lat`/`lon` are
display-only approximations.

## Add a scenario

Scenarios are YAML files in [`scenarios/`](scenarios/) composed of typed events
(see [`src/opencopper/shocks.py`](src/opencopper/shocks.py) for parameters and
each event's documented simplifications). If your scenario models a real event,
say what actually happened in the header comment so it can serve as a backtest.

## Challenge the engine

If you think a mechanism is wrong (e.g. how tariffs propagate), open an issue
describing the economics first — mechanism changes need agreement on the
economics before code. v1's documented simplifications are listed in the README
honesty box.

## Dev loop

```bash
uv sync
uv run pytest          # invariants + scenario backtests must stay green
uv run opencopper simulate --scenario scenarios/world-2026.yaml
uv run opencopper export-web   # regenerate web/data.js if data changed
```
