"""Thesis ledger marking + auto-generation + the TTL data layer. Offline:
price series injected via monkeypatch; cache TTL tested with utime."""

import json
from datetime import date

import pytest

import opencopper.theses as th
from opencopper.theses import (
    Marked,
    analytics,
    generate_auto_theses,
    mark_auto,
    mark_registered,
)

COPPER_2026 = [(f"2026-{m:02d}-01", 13000.0) for m in range(1, 6)]
FULL_YEAR = [(f"2026-{m:02d}-01", 13000.0) for m in range(1, 13)]


def _band(**kw):
    base = dict(id="t", created="2026-06-11", commodity="copper",
                claim="c", metric="annual_avg_band", year=2026,
                lo=11000, hi=16500, resolves_by="2027-01-31")
    base.update(kw)
    return base


def test_band_thesis_provisional_then_hit_then_miss(monkeypatch):
    monkeypatch.setattr(th, "_months", lambda c: COPPER_2026)
    m = mark_registered(_band(), today=date(2026, 6, 12))
    assert m.status == "open" and "inside" in m.reading and "5 mo" in m.reading

    monkeypatch.setattr(th, "_months", lambda c: FULL_YEAR)
    assert mark_registered(_band()).status == "hit"

    monkeypatch.setattr(th, "_months", lambda c: [(d, 20000.0) for d, _ in FULL_YEAR])
    m = mark_registered(_band())
    assert m.status == "miss" and m.resolved_on == "2026-12-31"


def test_external_thesis_lifecycle():
    ext = dict(id="x", created="2026-06-11", commodity="copper", claim="c",
               metric="external", resolves_by="2026-09-30", prob=0.86)
    assert mark_registered(ext, today=date(2026, 7, 1)).status == "open"
    assert mark_registered(ext, today=date(2026, 10, 1)).status == "needs-res"
    ext["resolution"] = {"date": "2026-10-02", "outcome": "hit", "note": "ICSG -200kt"}
    m = mark_registered(ext, today=date(2026, 11, 1))
    assert m.status == "hit" and m.resolved_on == "2026-10-02"


AUTO = dict(id="a", created="2026-06-11", commodity="crude-oil", country="x",
            severity=0.2, claim="c", metric="price_change", min_move_pct=5.0,
            horizon_months=6, entry_date="2026-05-01", entry_price=100.0)


def test_auto_thesis_hits_on_first_print_over_threshold(monkeypatch):
    monkeypatch.setattr(th, "_months", lambda c: [
        ("2026-05-01", 100.0), ("2026-06-01", 103.0), ("2026-07-01", 106.0),
        ("2026-08-01", 99.0)])
    m = mark_auto(AUTO, today=date(2026, 8, 15))
    assert m.status == "hit" and m.resolved_on == "2026-07-01"
    assert m.move_pct == pytest.approx(6.0)


def test_auto_thesis_open_then_miss_at_deadline(monkeypatch):
    monkeypatch.setattr(th, "_months", lambda c: [
        ("2026-05-01", 100.0), ("2026-06-01", 102.0)])
    m = mark_auto(AUTO, today=date(2026, 7, 1))
    assert m.status == "open" and m.move_pct == pytest.approx(2.0)
    m = mark_auto(AUTO, today=date(2027, 1, 2))  # deadline 2026-12-01 passed
    assert m.status == "miss" and "never printed" in m.reading


def test_generate_auto_dedupes_and_skips_untrackable(monkeypatch, tmp_path):
    monkeypatch.setattr(th, "_months",
                        lambda c: [("2026-05-01", 100.0)] if c == "crude-oil" else [])
    hits = [
        {"commodity": "crude-oil", "country": "Saudi Arabia", "severity": 0.2,
         "headline": "h1"},
        {"commodity": "crude-oil", "country": "Saudi Arabia", "severity": 0.2,
         "headline": "h2 (same event, other outlet)"},
        {"commodity": "cobalt", "country": "Congo (Kinshasa)", "severity": 0.4,
         "headline": "untrackable — no monthly series"},
        {"commodity": "copper", "country": "Panama", "severity": -0.1,
         "headline": "supply ADD, not a squeeze thesis"},
    ]
    p = tmp_path / "auto.json"
    added = generate_auto_theses(hits, "2026-06-11", auto_path=p)
    assert len(added) == 1 and added[0]["entry_price"] == 100.0
    # idempotent across reruns (the daily Action runs every morning)
    assert generate_auto_theses(hits, "2026-06-11", auto_path=p) == []
    assert len(json.loads(p.read_text())) == 1


def test_analytics_hit_rate_and_brier():
    rows = [
        Marked("a", "d", "c", "x", "registered", "external", "hit", "r", "dl", prob=0.86),
        Marked("b", "d", "c", "x", "registered", "external", "miss", "r", "dl", prob=0.30),
        Marked("c", "d", "c", "x", "auto", "price_change", "open", "r", "dl", move_pct=4.0),
    ]
    a = analytics(rows)
    assert a["hit_rate"] == pytest.approx(0.5)
    expected_brier = ((0.86 - 1) ** 2 + (0.30 - 0) ** 2) / 2
    assert a["brier"] == pytest.approx(expected_brier, abs=1e-3)
    assert a["open_auto_avg_move_pct"] == pytest.approx(4.0)


# ---------------------------------------------------------------- data layer


def test_cached_fred_ttl_serves_fresh_refetches_stale(monkeypatch, tmp_path):
    import os

    import opencopper.pricing as pr

    cache = tmp_path / "X.csv"
    cache.write_text("2026-05-01,100.0")

    def boom(series, client=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(pr, "fetch_fred", boom)
    # fresh: served straight from disk, no fetch attempted
    assert pr.cached_fred("X", cache_dir=tmp_path) == [("2026-05-01", 100.0)]
    # stale + fetch failure: stale file beats nothing
    os.utime(cache, (1, 1))
    assert pr.cached_fred("X", cache_dir=tmp_path) == [("2026-05-01", 100.0)]
    # stale + fetch works: cache rewritten
    monkeypatch.setattr(pr, "fetch_fred", lambda s, client=None: [("2026-06-01", 110.0)])
    assert pr.cached_fred("X", cache_dir=tmp_path) == [("2026-06-01", 110.0)]
    assert "110.0" in cache.read_text()


def test_datastore_status_covers_every_kind():
    from opencopper.datastore import render_status, status

    rows = status()
    kinds = {r.kind for r in rows}
    assert {"fred", "pinksheet", "news", "theses"} <= kinds
    fred = [r for r in rows if r.kind == "fred"]
    assert all(r.latest and r.latest.startswith("20") for r in fred if r.age_days is not None)
    text = render_status(rows)
    assert "TTLs" in text and "fred" in text
