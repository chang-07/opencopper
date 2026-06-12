"""Exposure book + non-metal expansion."""
from pathlib import Path

from opencopper.book import Position, evaluate_book, load_book
from opencopper.commodities import list_commodity_names, load_commodity, load_commodity_scenario

ROOT = Path(__file__).resolve().parents[1]


def test_fourteen_commodities_including_nonmetals():
    names = list_commodity_names()
    assert len(names) == 22
    for n in ("crude-oil", "natural-gas", "wheat"):
        assert n in names
        seed = load_commodity(n)
        assert seed.top_producers and seed.drivers


def test_book_scenario_routing_and_signs():
    sc = load_commodity_scenario(ROOT / "scenarios/commodities/hormuz-disruption.yaml")
    book = [Position("crude-oil", -1000, "short oil"), Position("cobalt", 10, "long co")]
    r = evaluate_book(book, sc, n_paths=300)
    rows = {p["label"]: p for p in r.per_position}
    assert rows["short oil"]["p50"] < 0          # short oil loses in a squeeze
    assert rows["long co"]["p50"] == 0           # oil scenario must not touch cobalt
    long_r = evaluate_book([Position("crude-oil", 1000, "long")], sc, n_paths=300)
    assert long_r.total_p50 > 0                  # long gains; sign symmetry


def test_book_example_file_loads():
    assert len(load_book(ROOT / "examples/book.yaml")) == 3
