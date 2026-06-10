# Sample extractions

Real, citation-backed extractions produced from EX-96 Technical Report Summaries
downloaded via `opencopper ingest`. They demonstrate the extraction schema and
the `reconcile` / `eval` loop on actual SEC filings — no API key needed to
reproduce the repo's headline result, because the JSON is committed.

Five mines, all US-listed filers (Freeport-McMoRan + Southern Copper):

| file | mine | filing |
|---|---|---|
| [`cuajone.json`](cuajone.json) | Cuajone Operations, Peru | SCCO FY2022 EX-96 |
| [`buenavista.json`](buenavista.json) | Buenavista, Mexico | SCCO FY2022 EX-96 |
| [`la-caridad.json`](la-caridad.json) | La Caridad, Mexico | SCCO FY2021 EX-96 |
| [`toquepala.json`](toquepala.json) | Toquepala, Peru | SCCO FY2021 EX-96 |
| [`cerro-verde.json`](cerro-verde.json) | Cerro Verde, Peru | FCX FY2021 EX-96 |

Every numeric value carries a verbatim citation and a confidence. Honest edges,
left in rather than papered over:

- **Toquepala `reserves_kt` is null** — the Mineral Reserve Statement table was
  dropped by the section pre-filter (caption-only in the retained text). It is
  recorded as null, not guessed; it lives in the source PDF (pages 1-10 / 12-9).
- **Cerro Verde `reserves_kt`** is on a *recoverable* basis (LOM-plan 27.9 Blb),
  flagged in its citation, because the reserve-table cells were caption-only in
  the source HTML. Confidence 0.4.
- **Annual production** is mostly a low-confidence LOM-average (reserve ÷ life)
  because a TRS states reserves and economics, not a single production year —
  which is exactly why `reconcile` flags those rows against the ledger's
  current-year seed estimates instead of overwriting them.

Reproduce:

```bash
opencopper eval --extractions evals/sample_extractions --truth evals/ground_truth.yaml
opencopper reconcile --extractions evals/sample_extractions
```
