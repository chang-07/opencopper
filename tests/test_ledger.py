from opencopper.ledger import load_assumptions, load_ledger


def test_seed_ledger_loads():
    ledger = load_ledger()
    assert len(ledger.mines) >= 20
    assert all(m.sources for m in ledger.mines), "every seed mine needs a source note"


def test_tracked_coverage_is_plausible():
    """Tracked mines should cover a meaningful but partial share of world
    supply — catches both a broken ledger and double counting."""
    ledger = load_ledger()
    assumptions = load_assumptions()
    tracked = sum(m.production(2024) for m in ledger.mines)
    share = tracked / assumptions.world.mine_supply(2024)
    assert 0.30 < share < 0.60, f"tracked share {share:.0%} outside sanity band"


def test_suspended_mine_produces_zero():
    ledger = load_ledger()
    cobre = ledger.get("Cobre Panama")
    assert cobre.production(2026) == 0


def test_country_lookup():
    ledger = load_ledger()
    chile = ledger.in_country("Chile")
    assert len(chile) >= 5
