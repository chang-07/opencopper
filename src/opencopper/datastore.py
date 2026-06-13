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

    from .futuresdata import YAHOO_FUTURES
    for name, cfg in YAHOO_FUTURES.items():
        out.append(_csv_status(f"{name} ({cfg.symbol})", "futures",
                               PRICE_CACHE_DIR / f"yahoo-{cfg.symbol.replace('=', '_')}.csv"))

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
    if kind in ("all", "futures"):
        from .futuresdata import YAHOO_FUTURES, cached_front

        for name in YAHOO_FUTURES:
            try:
                rows = cached_front(name, ttl_days=-1)
                log.append(f"futures {name:<11} {len(rows or [])} mo, latest {rows[-1][0] if rows else '-'}")
            except Exception as exc:
                log.append(f"futures {name:<11} FAILED: {exc}")
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


# ------------------------------------------------------- quality checks


def check() -> list[dict]:
    """Data-quality audit: every check is (level, source, message) with
    level PASS/WARN/FAIL. FAIL means a number the model would silently
    mis-use (non-positive price, broken date order, unparseable receipt);
    WARN means a human should look (gap, wild jump, stale series, anchor far
    from market). The CI suite asserts zero FAILs on whatever caches exist."""
    import json as _json
    import math

    from .pinksheet import PINKSHEET_SERIES
    from .pricing import load_pricebook

    out: list[dict] = []

    def add(level, source, msg):
        out.append({"level": level, "source": source, "msg": msg})

    book = load_pricebook()

    def check_series(label: str, rows: list[tuple[str, float]]):
        if not rows:
            add("FAIL", label, "empty series")
            return
        dates = [d for d, _ in rows]
        if dates != sorted(dates):
            add("FAIL", label, "dates out of order")
        if len(set(dates)) != len(dates):
            add("FAIL", label, "duplicate dates")
        bad = [d for d, v in rows if v <= 0]
        if bad:
            add("FAIL", label, f"{len(bad)} non-positive price(s), first {bad[0]}")
        gaps = 0
        for i in range(1, len(dates)):
            y0, m0 = int(dates[i - 1][:4]), int(dates[i - 1][5:7])
            y1, m1 = int(dates[i][:4]), int(dates[i][5:7])
            if (y1 - y0) * 12 + (m1 - m0) != 1:
                gaps += 1
        if gaps:
            add("WARN", label, f"{gaps} gap(s) in the monthly sequence")
        jumps = []
        for i in range(1, len(rows)):
            if rows[i - 1][1] > 0 and rows[i][1] > 0:
                r = abs(math.log(rows[i][1] / rows[i - 1][1]))
                if r > 0.75:
                    jumps.append((dates[i], r))
        if jumps:
            add("WARN", label,
                f"{len(jumps)} |move|>75% month(s), e.g. {jumps[-1][0]} "
                f"({jumps[-1][1]:.0%} log) — verify against source")
        if not jumps and not gaps and dates == sorted(dates) and not bad:
            add("PASS", label, f"{len(rows)} rows clean, latest {dates[-1]}")

    from .pricing import cached_fred
    for name, p in sorted(book.commodities.items()):
        if not p.fred_series:
            continue
        try:
            check_series(f"{name} ({p.fred_series})", cached_fred(p.fred_series))
        except Exception as exc:
            add("FAIL", f"{name} ({p.fred_series})", f"unreadable: {exc}")
    from .pinksheet import cached_pinksheet
    for name in PINKSHEET_SERIES:
        try:
            check_series(f"{name} (PinkSheet)", cached_pinksheet(name))
        except Exception as exc:
            add("WARN", f"{name} (PinkSheet)", f"unavailable: {exc}")

    # anchors vs market: beyond 3x either way the balanced-market anchor is
    # doing no work and should be re-seeded
    from .history import load_price_history
    for name, p in sorted(book.commodities.items()):
        if p.series_is_index:
            continue  # an index level isn't comparable to a USD anchor (gold)
        h = load_price_history(name)
        if not h:
            continue
        live = h.months[-1][1]
        ratio = live / p.anchor_usd
        if ratio > 3 or ratio < 1 / 3:
            add("WARN", name, f"live {live:,.0f} is {ratio:.1f}x anchor "
                              f"{p.anchor_usd:,.0f} — anchor likely stale")

    # receipts must parse
    for f in sorted(Path("data/news").glob("hits-*.json")):
        try:
            _json.loads(f.read_text())
        except Exception:
            add("FAIL", str(f), "unparseable hits receipt")
    auto = Path("data/theses-auto.json")
    if auto.exists():
        try:
            for t in _json.loads(auto.read_text()):
                if t.get("entry_price", 0) <= 0 or t["commodity"] not in book.commodities:
                    add("FAIL", auto.name, f"bad auto thesis {t.get('id')}")
        except Exception:
            add("FAIL", auto.name, "unparseable auto-theses file")

    # CPI (the real-terms leg depends on it)
    try:
        cpi = cached_fred("CPIAUCSL")
        add("PASS" if len(cpi) > 600 else "WARN", "CPI (CPIAUCSL)",
            f"{len(cpi)} rows, latest {cpi[-1][0]}")
    except Exception as exc:
        add("WARN", "CPI (CPIAUCSL)", f"unavailable: {exc}")

    # futures caches (auxiliary — WARN only, never fail the build on a Yahoo hiccup)
    from .futuresdata import YAHOO_FUTURES, cached_front

    missing = []
    for name in YAHOO_FUTURES:
        rows = cached_front(name)
        if not rows:
            missing.append(name)
    if missing:
        add("WARN", "futures (Yahoo)", f"{len(missing)} unavailable: {', '.join(missing[:5])}")
    else:
        add("PASS", "futures (Yahoo)", f"{len(YAHOO_FUTURES)} front-continuous series cached")
    return out


def render_check(results: list[dict]) -> str:
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    lines = ["DATA QUALITY — gaps, order, positivity, jumps, anchors, receipts", ""]
    for r in results:
        counts[r["level"]] += 1
        if r["level"] != "PASS":
            lines.append(f"  {r['level']:<5} {r['source']:<26} {r['msg']}")
    passes = [r for r in results if r["level"] == "PASS"]
    lines += ["", f"{counts['PASS']} pass / {counts['WARN']} warn / {counts['FAIL']} FAIL "
              f"({len(passes)} clean series suppressed; FAIL = the model would silently "
              "mis-use a number)"]
    return "\n".join(lines)
