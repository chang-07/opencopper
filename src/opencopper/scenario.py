"""Scenario loading and running."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import TypeAdapter

from .balance import BASELINE, RunResult, run
from .ledger import Assumptions, Ledger, load_assumptions, load_ledger
from .shocks import Event, Scenario

_EVENT_ADAPTER = TypeAdapter(list[Event])

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text())
    return Scenario(
        name=raw["name"],
        description=raw.get("description", ""),
        events=_EVENT_ADAPTER.validate_python(raw.get("events", [])),
    )


def run_scenario(
    scenario: Scenario,
    *,
    ledger: Ledger | None = None,
    assumptions: Assumptions | None = None,
    years: range = range(2024, 2031),
) -> tuple[RunResult, RunResult]:
    """Run a scenario and its baseline counterfactual. Returns (scenario, baseline)."""
    ledger = ledger or load_ledger()
    assumptions = assumptions or load_assumptions()
    return (
        run(ledger, assumptions, scenario, years),
        run(ledger, assumptions, BASELINE, years),
    )
