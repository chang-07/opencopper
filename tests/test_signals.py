"""Desk-sheet signals: structure, fallbacks, and the non-negotiable disclaimer."""
import json

from opencopper.signals import DISCLAIMER, build_signals, render_signals, signals_json


def test_signals_cover_all_commodities_with_futures_and_disclaimer():
    s = build_signals(n_paths=200)
    assert len(s) == 14
    by = {x.commodity: x for x in s}
    assert by["copper"].futures["symbol"] == "HG"
    assert by["copper"].live and by["copper"].gap_vs_anchor_pct is not None
    assert by["silver"].live is not None          # Pink Sheet fallback
    assert by["gold"].model_p50_2026 is None      # excluded from shock pricing
    text = render_signals(s)
    assert "NOT INVESTMENT ADVICE" in text
    payload = json.loads(signals_json(s))
    assert payload["disclaimer"] == DISCLAIMER and len(payload["signals"]) == 14
