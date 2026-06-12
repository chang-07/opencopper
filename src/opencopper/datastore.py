"""One view over every cache the model reads — and one lever to refresh them.

The model consumes five external surfaces (FRED CSV, World Bank Pink Sheet,
USGS seed YAML, MinMod JSON, Google News RSS). Each caches differently, which
is fine — but freshness has to be inspectable in ONE place, or "live price"
quietly means "whenever this file first appeared". `opencopper data status`
is that place; `opencopper data refresh` forces the fetchable ones.

Freshness contract: FRED/Pink Sheet caches carry a TTL (serve fresh, refetch
stale, stale-on-failure); seeds are versioned in git, not fetched; MinMod and
news refresh through their own commands (`minmod fetch`, `news`), recorded
here by age only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .pricing import PRICE_CACHE_DIR, cache_age_days, load_pricebook


@dataclass
class SourceStatus:
    source: str
    kind: str         # fred | pinksheet | seed | minmod | news | theses
    path: str
    age_days: float | None   # None = absent
    rows: int | None
    latest: str | None        # latest data date inside the file, if applicable
    note: str = ""


def _csv_status(source: str, kind: str, path: Path) -> SourceStatus:
    age = cache_age_days(path)
    rows = latest = None
    if age is not None:
        lines = [l for l in path.read_text().splitlines() if "," in l]
        rows = len(lines)
        latest = lines[-1].split(",")[0] if lines else None
    return SourceStatus(source, kind, str(path), None if age is None else round(age, 2), rows, latest)


def status() -> list[SourceStatus]:
    from .pinksheet import CACHE_DIR as PS_DIR
    from .pinksheet import PINKSHEET_SERIES, XLSX_CACHE

    out: list[SourceStatus] = []
    book = load_pricebook()
    for name, p in sorted(book.commodities.items()):
        if p.fred_series:
            out.append(_csv_status(f"{name} ({p.fred_series})", "fred",
                                   PRICE_CACHE_DIR / f"{p.fred_series}.csv"))
    for name in PINKSHEET_SERIES:
        out.append(_csv_status(f"{name} (PinkSheet)", "pinksheet",
                               PS_DIR / f"pinksheet-{name}.csv"))
    age = cache_age_days(XLSX_CACHE)
    out.append(SourceStatus("pink sheet workbook", "pinksheet", str(XLSX_CACHE),
                            None if age is None else round(age, 2), None, None))

    minmod = Path("data/minmod")
    files = sorted(minmod.glob("*.json")) if minmod.exists() else []
    for f in files:
        age = cache_age_days(f)
        out.append(SourceStatus(f.stem, "minmod", str(f), round(age, 2), None, None,
                                note="refresh via `opencopper minmod fetch`"))

    news = sorted(Path("data/news").glob("hits-*.json"))
    if news:
        f = news[-1]
        out.append(SourceStatus("news hits (latest)", "news", str(f),
                                round(cache_age_days(f), 2), None,
                                f.stem.replace("hits-", ""),
                                note="refresh via `opencopper news`"))
    else:
        out.append(SourceStatus("news hits", "news", "data/news/", None, None, None,
                                note="never run — `opencopper news`"))

    for p, kind in ((Path("data/theses.yaml"), "theses"), (Path("data/theses-auto.json"), "theses")):
        age = cache_age_days(p)
        out.append(SourceStatus(p.name, kind, str(p),
                                None if age is None else round(age, 2), None, None))
    return out


def refresh(kind: str = "all") -> list[str]:
    """Force-refetch the fetchable caches (TTL bypassed). Returns log lines."""
    from .pinksheet import PINKSHEET_SERIES, cached_pinksheet
    from .pricing import cached_fred

    log = []
    book = load_pricebook()
    if kind in ("all", "fred"):
        for name, p in sorted(book.commodities.items()):
            if not p.fred_series:
                continue
            try:
                rows = cached_fred(p.fred_series, ttl_days=-1)
                log.append(f"fred {p.fred_series:<12} {len(rows)} rows, latest {rows[-1][0]}")
            except Exception as exc:
                log.append(f"fred {p.fred_series:<12} FAILED: {exc}")
    if kind in ("all", "pinksheet"):
        for name in PINKSHEET_SERIES:
            try:
                rows = cached_pinksheet(name, ttl_days=-1)
                log.append(f"pinksheet {name:<10} {len(rows)} rows, latest {rows[-1][0]}")
            except Exception as exc:
                log.append(f"pinksheet {name:<10} FAILED: {exc}")
    return log


def render_status(rows: list[SourceStatus]) -> str:
    from .pinksheet import PINKSHEET_TTL_DAYS
    from .pricing import FRED_TTL_DAYS

    ttl = {"fred": FRED_TTL_DAYS, "pinksheet": PINKSHEET_TTL_DAYS}
    lines = ["DATA STATUS — every cache the model reads",
             f"{'source':<28}{'kind':<11}{'age':>8}{'rows':>7}{'latest':>12}  note",
             "-" * 84]
    for r in rows:
        if r.age_days is None:
            age, flag = "—", "absent"
        else:
            age = f"{r.age_days:.1f}d"
            flag = "STALE" if r.kind in ttl and r.age_days > ttl[r.kind] else ""
        lines.append(f"{r.source[:27]:<28}{r.kind:<11}{age:>8}"
                     f"{(str(r.rows) if r.rows is not None else '—'):>7}"
                     f"{(r.latest or '—'):>12}  {r.note or flag}")
    lines += ["", f"TTLs: fred {FRED_TTL_DAYS}d, pinksheet {PINKSHEET_TTL_DAYS}d "
              "(serve fresh / refetch stale / stale-on-failure). Seeds are git-versioned."]
    return "\n".join(lines)
