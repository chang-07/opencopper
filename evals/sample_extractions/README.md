# Sample extractions

Real, citation-backed extractions produced from EX-96 Technical Report Summaries
downloaded via `opencopper ingest`. These demonstrate the extraction schema and
the `reconcile` / `eval` loop on actual filings without requiring an API key to
reproduce the repo's headline result.

- [`cuajone.json`](cuajone.json) — Southern Copper's **Cuajone Operations**
  (Peru) TRS, FY2022 (`scco-20221231xex96d1.pdf`). Reserves and mine life are
  stated directly in the document and carry high confidence; annual production
  is flagged low-confidence because the TRS states no single-year figure (it is
  a reserve/economic document), so the value is derived from
  reserve ÷ mine-life and marked as such in its citation. `reconcile` correctly
  surfaces the gap between that LOM-average rate and the ledger's current-year
  seed estimate — which is the point: a reserve-statement rate is not a
  current-production rate, and the tool flags it for review rather than
  overwriting.

Run against them:

```bash
opencopper reconcile --extractions evals/sample_extractions
opencopper eval --extractions evals/sample_extractions --truth evals/ground_truth.yaml
```
