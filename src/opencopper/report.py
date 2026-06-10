"""Render a scenario run to a self-contained interactive HTML report."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .balance import RunResult
from .shocks import Scenario


def render_report(
    scenario: Scenario, result: RunResult, baseline: RunResult, out_path: Path
) -> Path:
    years = [r.year for r in result.rows]

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.09,
        subplot_titles=(
            "Refined balance (kt): surplus / deficit",
            "Inventory cover (days of consumption)",
            "Concentrate tightness (TC pressure: >0 = treatment charges falling)",
        ),
    )

    fig.add_trace(
        go.Bar(
            x=years,
            y=[r.refined_balance_kt for r in baseline.rows],
            name="baseline balance",
            marker_color="#9aa5b1",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=years,
            y=[r.refined_balance_kt for r in result.rows],
            name=f"{scenario.name} balance",
            marker_color="#b87333",  # copper, obviously
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=years,
            y=[r.inventory_days for r in baseline.rows],
            name="baseline cover",
            line={"color": "#9aa5b1", "dash": "dash"},
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=years,
            y=[r.inventory_days for r in result.rows],
            name=f"{scenario.name} cover",
            line={"color": "#b87333"},
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=years,
            y=[r.tc_pressure * 100 for r in baseline.rows],
            name="baseline TC pressure (%)",
            line={"color": "#9aa5b1", "dash": "dash"},
        ),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=years,
            y=[r.tc_pressure * 100 for r in result.rows],
            name=f"{scenario.name} TC pressure (%)",
            line={"color": "#b87333"},
        ),
        row=3,
        col=1,
    )

    fig.update_layout(
        title={
            "text": f"opencopper — {scenario.name}<br><sup>{scenario.description}</sup>",
            "x": 0.02,
        },
        height=860,
        barmode="group",
        template="plotly_white",
        legend={"orientation": "h", "y": -0.06},
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out_path, include_plotlyjs="cdn")
    return out_path
