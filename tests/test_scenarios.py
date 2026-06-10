"""Every shipped scenario file must load and run; backtests must move the
balance in the historically correct direction."""

from pathlib import Path

import pytest

from opencopper.scenario import SCENARIO_DIR, load_scenario, run_scenario

ALL_SCENARIOS = sorted(SCENARIO_DIR.glob("*.yaml"))


@pytest.mark.parametrize("path", ALL_SCENARIOS, ids=lambda p: p.stem)
def test_scenario_loads_and_runs(path: Path):
    scenario = load_scenario(path)
    result, baseline = run_scenario(scenario)
    assert len(result.rows) == len(baseline.rows) > 0


def test_grasberg_backtest_direction_and_magnitude():
    """The mud rush should widen the 2026 deficit by roughly 50% of Grasberg
    (~300-450kt vs a ~800kt baseline) — directionally matching consensus."""
    scenario = load_scenario(SCENARIO_DIR / "grasberg-2025.yaml")
    result, baseline = run_scenario(scenario)
    delta_2026 = result.row(2026).refined_balance_kt - baseline.row(2026).refined_balance_kt
    assert -500 < delta_2026 < -250


def test_cobre_panama_restart_adds_about_120kt_in_2026():
    scenario = load_scenario(SCENARIO_DIR / "cobre-panama-restart-2026.yaml")
    result, baseline = run_scenario(scenario)
    delta_2026 = result.row(2026).refined_balance_kt - baseline.row(2026).refined_balance_kt
    assert delta_2026 == pytest.approx(120, abs=15)


def test_world_2026_shows_tighter_market_than_baseline():
    scenario = load_scenario(SCENARIO_DIR / "world-2026.yaml")
    result, baseline = run_scenario(scenario)
    assert result.row(2026).refined_balance_kt < baseline.row(2026).refined_balance_kt
    assert result.row(2026).inventory_days < baseline.row(2026).inventory_days
    # composed real events should be worth at least ~400kt in 2026
    delta = result.row(2026).refined_balance_kt - baseline.row(2026).refined_balance_kt
    assert delta < -400
