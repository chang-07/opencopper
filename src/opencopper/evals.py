"""Extraction-accuracy evaluation: model output vs company-stated ground truth.

The honest loop this enables:
  1. `opencopper batch submit` / `collect` -> data/extracted/*.json
  2. Fill evals/ground_truth.yaml with values from the SOURCE documents
     (production tables, reserve statements) — by hand, that's the point.
  3. `opencopper eval` -> markdown accuracy table for the README.

A field scores correct when within tolerance AND carries a citation —
an uncited correct value is treated as a miss, because uncited numbers
are exactly what this project exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import ExtractedMineData

NUMERIC_FIELDS = ("annual_production_kt", "reserves_kt", "mine_life_years", "cash_cost_usd_lb")


@dataclass
class FieldResult:
    mine: str
    field: str
    expected: float
    extracted: float | None
    within_tol: bool
    cited: bool

    @property
    def correct(self) -> bool:
        return self.within_tol and self.cited


def load_extractions(dir_path: Path) -> list[ExtractedMineData]:
    return [
        ExtractedMineData.model_validate_json(p.read_text())
        for p in sorted(dir_path.glob("*.json"))
    ]


def load_ground_truth(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())["mines"]


def _match(extractions: list[ExtractedMineData], needle: str) -> ExtractedMineData | None:
    needle_l = needle.lower()
    for e in extractions:
        if needle_l in e.mine_name.lower() or e.mine_name.lower() in needle_l:
            return e
    return None


def evaluate(extractions: list[ExtractedMineData], truth: list[dict]) -> list[FieldResult]:
    results: list[FieldResult] = []
    for entry in truth:
        extraction = _match(extractions, entry["match"])
        for field, spec in entry.get("fields", {}).items():
            if field not in NUMERIC_FIELDS:
                raise ValueError(f"unknown eval field: {field}")
            expected = float(spec["value"])
            tol = float(spec.get("tol_pct", 5)) / 100
            extracted_field = getattr(extraction, field, None) if extraction else None
            if extracted_field is None:
                results.append(FieldResult(entry["match"], field, expected, None, False, False))
                continue
            value = extracted_field.value
            within = abs(value - expected) <= tol * abs(expected)
            cited = bool(extracted_field.citation.quote.strip())
            results.append(FieldResult(entry["match"], field, expected, value, within, cited))
    return results


def render_markdown(results: list[FieldResult]) -> str:
    lines = [
        "| mine | field | expected | extracted | within tol | cited | ok |",
        "|---|---|---:|---:|:-:|:-:|:-:|",
    ]
    for r in results:
        extracted = f"{r.extracted:,.1f}" if r.extracted is not None else "—"
        lines.append(
            f"| {r.mine} | {r.field} | {r.expected:,.1f} | {extracted} "
            f"| {'✓' if r.within_tol else '✗'} | {'✓' if r.cited else '✗'} "
            f"| {'✓' if r.correct else '✗'} |"
        )
    total = len(results)
    correct = sum(r.correct for r in results)
    pct = f" ({correct / total:.0%})" if total else ""
    lines.append(f"\n**{correct}/{total} fields correct{pct}** — correct = within tolerance AND cited.")
    return "\n".join(lines)


def run_eval(extractions_dir: Path, truth_path: Path) -> str:
    return render_markdown(evaluate(load_extractions(extractions_dir), load_ground_truth(truth_path)))
