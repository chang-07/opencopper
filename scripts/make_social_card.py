"""Generate the social card (1200x630 PNG) from the model's real data:
the criticality-colored producer map, opencopper's signature view.

Run:  uv run --group dev python scripts/make_social_card.py
Out:  web/social-card.png (served by Pages -> stable og:image URL)
"""

from pathlib import Path

import plotly.graph_objects as go

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from opencopper.commodities import list_commodity_names, load_commodity  # noqa: E402
from opencopper.geo import centroid  # noqa: E402


def country_criticality() -> list[dict]:
    by_country: dict[str, dict] = {}
    for name in list_commodity_names():
        seed = load_commodity(name)
        world = seed.world.production_kt[seed.world.latest_year]
        for p in seed.top_producers:
            loc = centroid(p.country)
            if not loc:
                continue
            e = by_country.setdefault(p.country, {"lat": loc[0], "lon": loc[1], "crit": 0.0})
            e["crit"] += (p.production_kt / world) ** 2
    return list(by_country.values())


def main() -> None:
    rows = country_criticality()
    fig = go.Figure(
        go.Scattergeo(
            lat=[r["lat"] for r in rows],
            lon=[r["lon"] for r in rows],
            mode="markers",
            marker=dict(
                size=[10 + (r["crit"] ** 0.5) * 38 for r in rows],
                color=[r["crit"] for r in rows],
                colorscale=[[0, "#4fa8a0"], [0.3, "#c9a227"], [1, "#c4604f"]],
                cmin=0,
                cmax=0.6,
                opacity=0.85,
                line=dict(color="#0f1318", width=1),
            ),
        )
    )
    fig.update_layout(
        width=1200,
        height=630,
        paper_bgcolor="#0f1318",
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="natural earth",
            bgcolor="rgba(0,0,0,0)",
            lataxis_range=[-56, 80],
            lonaxis_range=[-168, 188],
            showland=True,
            landcolor="#1b232e",
            showocean=True,
            oceancolor="#11161d",
            showcountries=True,
            countrycolor="#283342",
            coastlinecolor="#2f3b4d",
            showframe=False,
        ),
        annotations=[
            dict(
                text="<b>open<i>copper</i></b>",
                x=0.035, y=0.93, xref="paper", yref="paper", showarrow=False,
                font=dict(family="Georgia", size=54, color="#e8e3da"), xanchor="left",
            ),
            dict(
                text="an open world model for commodity markets",
                x=0.037, y=0.80, xref="paper", yref="paper", showarrow=False,
                font=dict(family="Georgia", size=24, color="#b87333"), xanchor="left",
            ),
            dict(
                text="shock a country · predict the ripple · every assumption disputable",
                x=0.037, y=0.085, xref="paper", yref="paper", showarrow=False,
                font=dict(family="Georgia", size=19, color="#8a94a0"), xanchor="left",
            ),
        ],
    )
    out = Path(__file__).resolve().parents[1] / "web" / "social-card.png"
    fig.write_image(out, scale=1)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
