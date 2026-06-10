"""MinMod ingestion — normalization, plausibility quarantine, ledger matching.
All offline against canned API payloads."""

from opencopper.ledger import load_ledger
from opencopper.minmod import (
    PLAUSIBLE_MAX_KT,
    MinModSite,
    match_ledger,
    normalize_record,
    partition_plausible,
    render_report,
)

COUNTRIES = {"Q1028": "Botswana", "Q1042": "Chile"}
DEPOSIT_TYPES = {"Q476": "Sediment-hosted copper", "Q387": "Porphyry copper"}

RECORD = {
    "id": "dedup_site__x__abc",
    "name": "THE  SELKIRK PROJECT",
    "deposit_types": [
        {"id": "Q476", "confidence": 0.9},
        {"id": "Q387", "confidence": 0.5},
    ],
    "grade_tonnage": [
        {"commodity": "Q538", "total_contained_metal": 0.55894956, "total_tonnage": 358.301, "total_grade": 0.156}
    ],
    "modified_at": "2024-11-15T03:06:09Z",
    "location": {"lat": -25.0, "lon": 20.0, "country": ["Q1028"], "state_or_province": []},
}


def test_normalize_record_units_and_lookups():
    site = normalize_record(RECORD, COUNTRIES, DEPOSIT_TYPES)
    assert site.name == "THE SELKIRK PROJECT"  # whitespace collapsed
    assert site.contained_kt == 558.9  # Mt -> kt
    assert site.tonnage_mt == 358.301
    assert site.country == "Botswana"
    assert site.deposit_type == "Sediment-hosted copper"  # highest confidence wins
    assert site.lat == -25.0


def test_normalize_record_handles_missing_everything():
    site = normalize_record({"id": "x", "name": "Bare"}, {}, {})
    assert site.contained_kt is None
    assert site.lat is None
    assert site.country is None


def _site(name, contained_kt, lat=0.0, lon=0.0):
    return MinModSite(minmod_id=name, name=name, contained_kt=contained_kt, lat=lat, lon=lon)


def test_partition_quarantines_unit_errors():
    sites = [_site("ok", 5000.0), _site("junk", PLAUSIBLE_MAX_KT * 40), _site("no-gt", None)]
    plausible, quarantined = partition_plausible(sites)
    assert [s.name for s in quarantined] == ["junk"]
    assert {s.name for s in plausible} == {"ok", "no-gt"}


def test_match_ledger_never_uses_quarantined_records():
    ledger = load_ledger()
    sites = [
        _site("Escondida", 5_600_000.0),      # unit error -> must be ignored
        _site("Escondida-Main", 104_317.0),   # plausible -> must win
        _site("Unrelated Deposit", 2_000.0),
    ]
    matches = dict((m.name, s) for m, s in match_ledger(sites, ledger))
    assert matches["Escondida"].name == "Escondida-Main"


def test_report_mentions_quarantine():
    ledger = load_ledger()
    text = render_report([_site("ok", 5000.0), _site("junk", 9e6)], ledger)
    assert "quarantined: 1" in text
    assert "deposits, not production" in text
