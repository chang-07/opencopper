"""Quarterly 3-region trade flows: conservation, calm baseline, anticipation,
and the arb pinning at the tariff wedge."""

import pytest

from opencopper.balance import BASELINE
from opencopper.regional import REGIONS, run_regional
from opencopper.scenario import SCENARIO_DIR, load_scenario


@pytest.fixture(scope="module")
def tariff_run():
    return run_regional(load_scenario(SCENARIO_DIR / "us-refined-tariff-2026.yaml"))


@pytest.fixture(scope="module")
def baseline_run():
    return run_regional(BASELINE)


def test_baseline_premia_stay_calm(baseline_run):
    """With structural deficits met by contracted baseline flows, no region
    should swing — the first version of this model oscillated famine/flood."""
    for row in baseline_run.rows:
        for r in REGIONS:
            assert abs(row.premium_pct[r]) < 16, (row.label, r, row.premium_pct)


def test_anticipation_spikes_before_the_tariff(tariff_run):
    """Buyers front-run the announced wedge: the US premium in the two
    anticipation quarters (2025H2) must exceed the calm early-2025 level."""
    early = tariff_run.row("2025Q1").premium_pct["us"]
    ahead = max(tariff_run.row("2025Q3").premium_pct["us"],
                tariff_run.row("2025Q4").premium_pct["us"])
    assert ahead > early + 10


def test_premium_pins_at_the_wedge(tariff_run):
    """Post-tariff steady state: the marginal imported ton pays the wedge, so
    the US premium converges to ~the tariff rate (25%)."""
    late = [r.premium_pct["us"] for r in tariff_run.rows if r.year >= 2028]
    avg = sum(late) / len(late)
    assert avg == pytest.approx(25.0, abs=5.0)


def test_us_minus_row_is_the_arb(tariff_run, baseline_run):
    """The spread blows out under the tariff vs baseline."""
    t = max(r.us_minus_row for r in tariff_run.rows if r.year in (2026, 2027))
    b = max(abs(r.us_minus_row) for r in baseline_run.rows)
    assert t > b + 15


def test_flows_conserve_metal(baseline_run):
    """No region's inventory goes negative and covers stay physical."""
    for row in baseline_run.rows:
        for r in REGIONS:
            assert row.cover_days[r] >= 0
            assert row.inflow_kt[r] >= 0


def test_quarterly_resolution_and_labels(baseline_run):
    assert len(baseline_run.rows) == 7 * 4
    assert baseline_run.rows[0].label == "2024Q1"
    assert baseline_run.rows[-1].label == "2030Q4"
