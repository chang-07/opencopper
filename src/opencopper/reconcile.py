"""Reconcile extracted filings data against the seed ledger.

The fintech move: two independent sources for the same quantity, diffed, with
every discrepancy surfaced for review instead of silently overwritten. Seed
estimates only get replaced by extracted values after a human accepts the diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .evals import load_extractions
from .ledger import Ledger, load_ledger
from .schema import ExtractedMineData, MineRecord


@dataclass
class Discrepancy:
    mine: str
    field: str
    ledger_value: float
    extracted_value: float
    extracted_year: int | None
    diff_pct: float
    confidence: float
    quote: str


@dataclass
class ReconcileReport:
    matched: list[Discrepancy]
    agreements: list[str]  # mine names where values agree within band
    unmatched_extractions: list[str]  # candidates to add to the ledger


def _find_mine(ledger: Ledger, name: str) -> MineRecord | None:
    name_l = name.lower()
    for mine in ledger.mines:
        if mine.name.lower() in name_l or name_l in mine.name.lower():
            return mine
    return None


def reconcile(
    extractions: list[ExtractedMineData],
    ledger: Ledger | None = None,
    agree_band_pct: float = 5.0,
) -> ReconcileReport:
    ledger = ledger or load_ledger()
    matched: list[Discrepancy] = []
    agreements: list[str] = []
    unmatched: list[str] = []

    for e in extractions:
        mine = _find_mine(ledger, e.mine_name)
        if mine is None:
            unmatched.append(e.mine_name)
            continue
        if e.annual_production_kt is None:
            continue
        extracted = e.annual_production_kt.value
        year = e.annual_production_kt.year
        ledger_value = (
            mine.production_kt.get(year)
            if year and year in mine.production_kt
            else mine.capacity_kt
        )
        diff_pct = 100 * (extracted - ledger_value) / ledger_value if ledger_value else 0.0
        if abs(diff_pct) <= agree_band_pct:
            agreements.append(mine.name)
        else:
            matched.append(
                Discrepancy(
                    mine=mine.name,
                    field="annual_production_kt",
                    ledger_value=ledger_value,
                    extracted_value=extracted,
                    extracted_year=year,
                    diff_pct=diff_pct,
                    confidence=e.annual_production_kt.confidence,
                    quote=e.annual_production_kt.citation.quote[:80],
                )
            )
    return ReconcileReport(matched=matched, agreements=agreements, unmatched_extractions=unmatched)


def render_report(report: ReconcileReport) -> str:
    lines: list[str] = []
    if report.matched:
        lines.append("DISCREPANCIES (review before updating the ledger):")
        for d in report.matched:
            year = d.extracted_year or "?"
            lines.append(
                f"  {d.mine:<20} ledger {d.ledger_value:>8,.0f} kt  vs extracted "
                f"{d.extracted_value:>8,.0f} kt ({year})  {d.diff_pct:+.1f}%  "
                f"[conf {d.confidence:.2f}] \"{d.quote}\""
            )
    if report.agreements:
        lines.append(f"AGREE (within band): {', '.join(sorted(set(report.agreements)))}")
    if report.unmatched_extractions:
        lines.append(
            "NOT IN LEDGER (candidates to add): "
            + ", ".join(sorted(set(report.unmatched_extractions)))
        )
    return "\n".join(lines) or "nothing to reconcile (no extractions found)"


def run_reconcile(extractions_dir: Path) -> str:
    return render_report(reconcile(load_extractions(extractions_dir)))
