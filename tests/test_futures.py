"""Futures data + carry. Offline: Yahoo fetch monkeypatched; carry math and
the demeaning checked against hand values."""

import pytest

import opencopper.futuresdata as fd


def _mk(monkeypatch, front_native, spot=None):
    """Inject a monthly front-future series and (optionally) a spot history."""
    monkeypatch.setattr(fd, "cached_front", lambda c, **k: front_native)
    if spot is not None:
        class _H:
            months = spot
        import opencopper.history as h
        monkeypatch.setattr(fd, "load_price_history", lambda c: _H(), raising=False)
        # basis_carry imports load_price_history from .history at call time
        monkeypatch.setattr(h, "load_price_history", lambda c: _H())


def test_futures_returns_are_log_and_unit_free(monkeypatch):
    front = [(f"20{20 + i // 12}-{i % 12 + 1:02d}-01", 100.0 * (1.05 ** i)) for i in range(18)]
    monkeypatch.setattr(fd, "cached_front", lambda c, **k: front)
    r = fd.futures_returns("crude-oil")
    assert len(r) == len(front) - 1
    import math
    assert all(abs(v - math.log(1.05)) < 1e-9 for _, v in r)


def test_basis_carry_sign_and_alignment(monkeypatch):
    # backwardation: spot ABOVE front -> positive carry
    front = [("2025-01-01", 100.0), ("2025-02-01", 100.0), ("2025-03-01", 100.0)]
    spot = [("2025-01-01", 105.0), ("2025-02-01", 102.0), ("2025-03-01", 100.0),
            ("2025-04-01", 99.0)]  # extra month with no front -> dropped
    import opencopper.history as h
    class _H: months = spot
    monkeypatch.setattr(h, "load_price_history", lambda c: _H())
    monkeypatch.setattr(fd, "cached_front", lambda c, **k: front)
    b = fd.basis_carry("crude-oil")
    assert [d for d, _ in b] == ["2025-01-01", "2025-02-01", "2025-03-01"]  # inner join
    assert b[0][1] == pytest.approx(0.05) and b[2][1] == pytest.approx(0.0)


def test_carry_signal_demeans_structural_offset(monkeypatch):
    # a constant +20% benchmark offset must demean to ~0 (it's not carry)
    front = [(f"2020-{m:02d}-01", 100.0) for m in range(1, 13)] * 1
    front = [(f"2020-{m:02d}-01", 100.0) for m in range(1, 13)]
    spot = [(d, 120.0) for d, _ in front]  # always 20% above -> structural
    import opencopper.history as h
    class _H: months = spot
    monkeypatch.setattr(h, "load_price_history", lambda c: _H())
    monkeypatch.setattr(fd, "cached_front", lambda c, **k: front)
    raw = fd.basis_carry("crude-oil")
    sig = fd.carry_signal("crude-oil")
    assert all(abs(v - 0.20) < 1e-9 for _, v in raw)   # raw shows the offset
    assert abs(sig[-1][1]) < 1e-9                        # signal demeans it away


def test_carry_only_for_clean_commodities():
    # copper is mapped (for momentum) but carry=False (COMEX-LME distortion)
    assert "copper" in fd.YAHOO_FUTURES and not fd.YAHOO_FUTURES["copper"].carry
    assert "copper" not in fd.carry_commodities()
    assert {"crude-oil", "silver", "wheat", "corn"} <= set(fd.carry_commodities())


def test_real_carry_runs_and_is_sane():
    rows = [(c, fd.latest_carry_signal(c)) for c in fd.carry_commodities()]
    have = [(c, v) for c, v in rows if v is not None]
    if not have:
        pytest.skip("no Yahoo cache")
    # demeaned carry signal should be modest (term-structure variation, not 20%)
    assert all(abs(v) < 0.25 for _, v in have)
    assert "FUTURES CARRY" in fd.render_carry()
