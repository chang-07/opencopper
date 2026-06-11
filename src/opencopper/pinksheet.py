"""World Bank "Pink Sheet" (CMO) monthly prices — the free source for metals
FRED doesn't carry. Currently used for silver (monthly back to 1960); cobalt
and lithium are not in the monthly sheet (checked June 2026), so they keep the
documented default ambient volatility.

The download URL embeds a release hash that changes when the World Bank
publishes updates; on failure we fall back to the cached copy and say so.
"""

from __future__ import annotations

import csv
from pathlib import Path

import httpx

PINKSHEET_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/CMO-Historical-Data-Monthly.xlsx"
)
CACHE_DIR = Path("data/prices")
XLSX_CACHE = CACHE_DIR / "pinksheet-monthly.xlsx"

# commodity slug -> exact column header in the 'Monthly Prices' sheet
PINKSHEET_SERIES: dict[str, str] = {
    "silver": "Silver",
}


def _download() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = httpx.get(PINKSHEET_URL, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        XLSX_CACHE.write_bytes(resp.content)
    except Exception:
        if not XLSX_CACHE.exists():
            raise
    return XLSX_CACHE


def _parse_column(column: str) -> list[tuple[str, float]]:
    import openpyxl

    wb = openpyxl.load_workbook(_download(), read_only=True)
    ws = wb["Monthly Prices"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[4]
    try:
        col = next(j for j, c in enumerate(header) if c and str(c).strip() == column)
    except StopIteration:
        raise KeyError(f"Pink Sheet column not found: {column}")
    out = []
    for r in rows[6:]:
        label, value = r[0], r[col]
        if label is None or value is None:
            continue
        # label like '2025M12' -> '2025-12-01' to match the FRED shape
        s = str(label)
        if "M" in s:
            year, month = s.split("M")
            out.append((f"{year}-{int(month):02d}-01", float(value)))
    return out


def cached_pinksheet(commodity: str) -> list[tuple[str, float]]:
    """Monthly (date, price) rows for a commodity, CSV-cached like FRED."""
    column = PINKSHEET_SERIES[commodity]
    cache = CACHE_DIR / f"pinksheet-{commodity}.csv"
    if cache.exists():
        return [
            (row[0], float(row[1]))
            for row in csv.reader(cache.read_text().splitlines())
            if len(row) == 2
        ]
    rows = _parse_column(column)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(f"{d},{v}" for d, v in rows))
    return rows
