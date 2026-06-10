import json

import pytest

from opencopper.export_web import GRASBERG_SEVERITIES, TARIFF_RATES, build_payload, export_web


@pytest.fixture(scope="module")
def payload():
    return build_payload()


def test_payload_structure_and_grids(payload):
    assert payload["meta"]["years"] == list(range(2024, 2031))
    assert len(payload["baseline"]) == 7
    assert "world-2026" in payload["scenarios"]
    assert set(payload["labs"]["tariff"]["runs"]) == {str(r) for r in TARIFF_RATES}
    assert set(payload["labs"]["grasberg"]["runs"]) == {f"{s:.1f}" for s in GRASBERG_SEVERITIES}
    assert payload["meta"]["tracked_mines"] == len(payload["mines"])
    assert 30 <= payload["meta"]["coverage_pct"] <= 60


def test_decision_runs_differ_by_restart_ramp(payload):
    runs = payload["labs"]["decision"]["runs"]
    yes_2026 = next(r for r in runs["yes"] if r["year"] == 2026)
    no_2026 = next(r for r in runs["no"] if r["year"] == 2026)
    assert yes_2026["refined_balance_kt"] - no_2026["refined_balance_kt"] == pytest.approx(120, abs=15)


def test_zero_severity_grasberg_equals_2025_only_outage(payload):
    zero = payload["labs"]["grasberg"]["runs"]["0.0"]
    z26 = next(r for r in zero if r["year"] == 2026)
    b26 = next(r for r in payload["baseline"] if r["year"] == 2026)
    # severity 0 in 2026 -> only the 2025 event differs from baseline
    assert z26["mine_supply_kt"] == b26["mine_supply_kt"]


def test_export_writes_loadable_js(tmp_path):
    out = export_web(tmp_path / "data.js")
    text = out.read_text()
    assert text.startswith("// generated")
    data = json.loads(text.split("=", 1)[1].rstrip().rstrip(";"))
    assert "baseline" in data
