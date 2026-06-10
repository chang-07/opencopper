from pathlib import Path

import yaml

from opencopper.evals import evaluate, render_markdown
from opencopper.reconcile import reconcile, render_report
from opencopper.schema import Citation, ExtractedField, ExtractedMineData


def _extraction(name: str, production: float | None, year: int = 2024, quote: str = "stated") -> ExtractedMineData:
    field = None
    if production is not None:
        field = ExtractedField(
            value=production, unit="kt Cu", year=year,
            citation=Citation(quote=quote), confidence=0.9,
        )
    return ExtractedMineData(mine_name=name, commodities=["copper"], annual_production_kt=field)


def test_evaluate_scores_tolerance_and_citations():
    extractions = [
        _extraction("Escondida Mine", 1281.0),          # within 5% of 1280
        _extraction("Grasberg", 600.0),                  # way off 816
        _extraction("Collahuasi", 559.0, quote="   "),   # right value, blank citation -> not ok
    ]
    truth = [
        {"match": "Escondida", "fields": {"annual_production_kt": {"value": 1280, "tol_pct": 5}}},
        {"match": "Grasberg", "fields": {"annual_production_kt": {"value": 816, "tol_pct": 5}}},
        {"match": "Collahuasi", "fields": {"annual_production_kt": {"value": 558, "tol_pct": 5}}},
        {"match": "Nonexistent", "fields": {"annual_production_kt": {"value": 100, "tol_pct": 5}}},
    ]
    results = evaluate(extractions, truth)
    assert [r.correct for r in results] == [True, False, False, False]
    md = render_markdown(results)
    assert "1/4 fields correct" in md


def test_ground_truth_template_parses():
    truth = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "evals" / "ground_truth.yaml").read_text()
    )
    assert truth["mines"][0]["match"]


def test_reconcile_buckets_agree_discrepancy_unmatched():
    extractions = [
        _extraction("Escondida", 1281.0),   # agrees with 2024 seed 1280
        _extraction("Grasberg", 600.0),     # disagrees with 2024 seed 816
        _extraction("El Arco", 0.0),        # not in ledger (development project)
    ]
    report = reconcile(extractions)
    assert "Escondida" in report.agreements
    assert any(d.mine == "Grasberg" for d in report.matched)
    grasberg = next(d for d in report.matched if d.mine == "Grasberg")
    assert grasberg.diff_pct < -20
    assert "El Arco" in report.unmatched_extractions
    text = render_report(report)
    assert "DISCREPANCIES" in text and "NOT IN LEDGER" in text
