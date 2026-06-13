"""Multi-factor book + forward paper trading. The book uses real caches (skips
if absent); the paper book's mark-forward logic is tested offline."""

import json

import pytest


def test_factor_book_runs_costed_and_sane():
    from opencopper.backtest import factor_book

    b = factor_book()
    if b.get("n_months", 0) < 36:
        pytest.skip("no futures/price caches")
    # net is below gross (costs subtract), Sharpe in a believable band
    assert b["net"]["sharpe"] <= b["gross"]["sharpe"] + 1e-9
    assert -0.5 < b["net"]["sharpe"] < 1.5
    assert b["bootstrap"]["ci90"][0] <= b["net"]["sharpe"] <= b["bootstrap"]["ci90"][1]
    # adding momentum should NOT beat carry+value here (the documented finding)
    b3 = factor_book(factors=("carry", "value", "mom"))
    assert b3["net"]["sharpe"] <= b["net"]["sharpe"] + 0.05


def test_current_weights_long_only_and_normalized():
    from opencopper.backtest import current_weights

    w = current_weights()
    if not w:
        pytest.skip("no caches")
    assert all(v > 0 for v in w.values())              # long-only
    assert abs(sum(w.values()) - 1.0) < 1e-6           # fully invested


def test_paper_marks_forward_and_is_idempotent(tmp_path, monkeypatch):
    import opencopper.paper as pp
    import opencopper.backtest as bt

    monkeypatch.setattr(bt, "current_weights", lambda *a, **k: {"crude-oil": 0.5, "wheat": 0.5})
    # realized only resolves for the FIRST month, not the second (forward-only)
    rmr = {"2026-01-01": 0.04}
    monkeypatch.setattr(bt, "realized_month_return", lambda w, m: rmr.get(m))
    path = tmp_path / "paper.json"

    pp.update_paper(path, today_month="2026-01-01")
    pp.update_paper(path, today_month="2026-01-01")              # same month: no dup
    book = json.loads(path.read_text())
    assert len(book["snapshots"]) == 1 and book["live_start"] == "2026-01-01"

    pp.update_paper(path, today_month="2026-02-01")              # new month: snapshot + mark Jan
    book = json.loads(path.read_text())
    assert len(book["snapshots"]) == 2
    jan = next(s for s in book["snapshots"] if s["month"] == "2026-01-01")
    assert jan["realized_ret"] == pytest.approx(0.04)            # Jan resolved
    feb = next(s for s in book["snapshots"] if s["month"] == "2026-02-01")
    assert feb["realized_ret"] is None                          # Feb still open

    s = pp.paper_summary(path)
    assert s["n_resolved"] == 1 and s["equity_curve"][-1]["equity"] == pytest.approx(1.04)
    assert "forward" in pp.render_paper(s).lower()


def test_paper_empty_book_renders_not_started(tmp_path):
    from opencopper.paper import paper_summary, render_paper

    s = paper_summary(tmp_path / "none.json")
    assert s["n_snapshots"] == 0
    assert "not started" in render_paper(s)
