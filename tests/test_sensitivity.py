from opencopper.sensitivity import render_tornado, run_sensitivity


def test_sensitivity_rows_cover_all_sweeps_and_sort_by_swing():
    rows = run_sensitivity(year=2026)
    assert len(rows) == 12
    swings = [r.swing for r in rows]
    assert swings == sorted(swings, reverse=True)
    assert all(r.swing >= 0 for r in rows)


def test_directions_are_economically_sane():
    rows = {r.param: r for r in run_sensitivity(year=2026)}
    # more demand -> smaller balance
    demand = rows["demand.base_kt_2024"]
    assert demand.high < demand.base < demand.low
    # more mine supply -> larger balance
    supply = rows["world.mine_supply_kt_2024"]
    assert supply.high > supply.base > supply.low
    # bigger disruption allowance -> less supply -> smaller balance
    disruption = rows["world.disruption_allowance_pct"]
    assert disruption.high < disruption.low


def test_render_tornado_is_readable():
    rows = run_sensitivity(year=2026)
    text = render_tornado(rows, 2026, "baseline")
    assert "sensitivity of 2026" in text
    assert "#" in text
