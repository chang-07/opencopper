"""Cross-commodity linkages + news ingest. Fully offline (injected fetcher)."""

import json

import pytest

from opencopper.linkages import load_linkages, ripple
from opencopper.news import (
    NewsHit,
    build_brief,
    fetch_headlines,
    load_rules,
    match_rules,
    recent_items,
)

# ---------------------------------------------------------------- linkages


def test_linkages_yaml_references_real_commodities():
    from opencopper.commodities import list_commodity_names

    names = set(list_commodity_names())
    for ln in load_linkages():
        refs = {ln.get(k) for k in ("host", "dependent", "from", "to", "input", "output")}
        assert (refs - {None}) <= names, f"unknown commodity in link {ln}"


def test_byproduct_drags_dependent_supply():
    rows = ripple("copper", "Congo (Kinshasa)", 0.5)
    by_name = {r.commodity: r for r in rows}
    # cobalt rides DRC copper: coupling 0.7 x severity 0.5 x DRC cobalt share (~0.74)
    cobalt = by_name["cobalt"]
    assert cobalt.channel == "byproduct"
    assert cobalt.supply_shock == pytest.approx(0.7 * 0.5 * 0.742, abs=0.02)
    assert cobalt.clamped and cobalt.price_change_pct == pytest.approx(300, abs=1)
    # the direct copper move is modest (DRC ~14% of world, half out)
    assert 10 < by_name["copper"].price_change_pct < 30
    # rows come sorted by |impact|, biggest first
    assert rows[0].commodity == "cobalt"


def test_byproduct_skipped_when_country_does_not_host_it():
    # a Chile copper outage must NOT drag cobalt (link is DRC-specific)
    rows = ripple("copper", "Chile", 0.3)
    assert "cobalt" not in {r.commodity for r in rows}


def test_substitution_demand_shift_has_the_right_sign():
    rows = ripple("copper", "Congo (Kinshasa)", 0.5)
    sub = next(r for r in rows if r.channel == "substitution")
    assert sub.commodity == "aluminum"
    assert sub.demand_shift > 0 and sub.price_change_pct > 0  # copper up -> Al demand up


def test_input_cost_passthrough_scales_with_direct_move():
    rows = ripple("natural-gas", None, 0.15)
    direct = next(r for r in rows if r.channel == "direct")
    al = next(r for r in rows if r.commodity == "aluminum")
    assert al.channel == "input_cost"
    assert al.price_change_pct == pytest.approx(0.20 * direct.price_change_pct, abs=0.11)


# ---------------------------------------------------------------- news rules

HEADLINES = [
    {"title": "Freeport declares force majeure at Grasberg mine", "link": "u1",
     "published": "Wed, 10 Jun 2026 08:00:00 GMT"},
    {"title": "Workers at Escondida begin strike over pay", "link": "u2",
     "published": "Tue, 09 Jun 2026 08:00:00 GMT"},
    {"title": "Escondida posts record output", "link": "u3",  # no action word -> no match
     "published": "Tue, 09 Jun 2026 08:00:00 GMT"},
    {"title": "DRC extends cobalt export BAN through 2027", "link": "u4",
     "published": "Mon, 08 Jun 2026 08:00:00 GMT"},
    {"title": "Tanker rates spike as Strait of Hormuz closure persists", "link": "u5",
     "published": "Mon, 08 Jun 2026 08:00:00 GMT"},
]


def test_match_rules_terms_alternation_and_case():
    hits = match_rules(HEADLINES, load_rules()["rules"])
    by_link = {h.link: h for h in hits}
    assert by_link["u1"].commodity == "copper" and by_link["u1"].country == "Indonesia"
    assert by_link["u2"].country == "Chile"  # "escondida" + "strike" alternation
    assert "u3" not in by_link  # all terms must hit
    assert by_link["u4"].commodity == "cobalt"  # case-insensitive "BAN"
    assert by_link["u5"].commodity == "crude-oil"
    # one rule per headline, never duplicates
    assert len(hits) == len({h.link for h in hits})


def test_recent_items_filters_stale_keeps_unparseable():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    items = [
        {"title": "fresh", "published": "Wed, 10 Jun 2026 08:00:00 GMT"},
        {"title": "stale", "published": "Thu, 25 Sep 2025 07:00:00 GMT"},
        {"title": "undated", "published": ""},
    ]
    kept = {i["title"] for i in recent_items(items, 14, now=now)}
    assert kept == {"fresh", "undated"}


def test_fetch_headlines_parses_rss_and_dedupes():
    rss = """<rss><channel>
      <item><title><![CDATA[Copper hits record]]></title><link>http://a</link>
        <pubDate>Wed, 10 Jun 2026 08:00:00 GMT</pubDate></item>
      <item><title>Copper hits record</title><link>http://b</link></item>
      <item><title>Mine &amp; smelter halt</title><link>http://c</link></item>
    </channel></rss>"""

    class _Resp:
        text = rss
        def raise_for_status(self): pass

    class _Client:
        def get(self, *a, **k): return _Resp()
        def close(self): pass

    items = fetch_headlines(["any query"], client=_Client())
    assert [i["title"] for i in items] == ["Copper hits record", "Mine & smelter halt"]
    assert items[0]["link"] == "http://a"  # first occurrence wins the dedupe


# ---------------------------------------------------------------- brief


def _hit(**kw):
    base = dict(headline="h", link="u", published="p", rule_note="",
                commodity="copper", country="Chile", severity=0.1)
    base.update(kw)
    return NewsHit(**base)


def test_brief_groups_corroborating_headlines_into_one_event():
    hits = [_hit(headline="first"), _hit(headline="second"), _hit(headline="third")]
    brief = build_brief(hits, "2026-06-11")
    assert brief.count("CROSS-COMMODITY RIPPLE") == 1
    assert "2 corroborating headline(s)" in brief
    assert "NOT investment advice" in brief


def test_brief_flags_bad_rule_instead_of_dying():
    brief = build_brief([_hit(country="Atlantis")], "2026-06-11")
    assert "impact not computed" in brief and "Atlantis" in brief


def test_brief_supply_addition_and_empty_day():
    brief = build_brief([_hit(severity=-0.1)], "2026-06-11")
    assert "eases the copper balance" in brief
    assert "No rule-matched headlines" in build_brief([], "2026-06-11")


def test_news_payload_groups_and_prices(tmp_path):
    from dataclasses import asdict

    from opencopper.export_web import _news_payload

    assert _news_payload(tmp_path) == {"date": None, "events": []}
    hits = [asdict(_hit(headline="a")), asdict(_hit(headline="b")),
            asdict(_hit(commodity="cobalt", country="Congo (Kinshasa)", severity=0.4))]
    (tmp_path / "hits-2026-06-11.json").write_text(json.dumps(hits))
    p = _news_payload(tmp_path)
    assert p["date"] == "2026-06-11" and len(p["events"]) == 2
    copper = next(e for e in p["events"] if e["commodity"] == "copper")
    assert copper["corroborating"] == 1
    assert any(i["channel"] == "direct" for i in copper["impacts"])
