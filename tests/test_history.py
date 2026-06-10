"""Historical layer — regime classification and stats. Uses the FRED cache
written by earlier runs; skips cleanly if no cache is present (offline CI)."""

import pytest

from opencopper.history import Regime, load_price_history
from opencopper.pricing import PRICE_CACHE_DIR


def _has_cache(series: str) -> bool:
    return (PRICE_CACHE_DIR / f"{series}.csv").exists()


pytestmark = pytest.mark.skipif(
    not _has_cache("PCOPPUSDM"), reason="no FRED cache (run `opencopper history` once)"
)


def test_copper_history_stats_are_sane():
    h = load_price_history("copper")
    assert h is not None
    assert 0.10 < h.annual_volatility < 0.40   # copper ~22%
    assert h.max_drawdown < 0                   # drawdown is negative
    assert h.regime_now in Regime
    assert abs(sum(h.regime_fractions.values()) - 1.0) < 0.01
    assert len(h.months) > 300                  # decades of data


def test_commodity_without_series_returns_none():
    assert load_price_history("cobalt") is None  # no FRED series
