"""News ingest -> rule-matched events -> simulated price impacts -> brief.

Keyless and deterministic by design so it can RUN UNATTENDED (the daily
GitHub Action): Google News RSS feeds are fetched, headlines are matched
against transparent keyword rules (data/seed/news_rules.yaml), matches become
country-supply events with prior severities, and the incidence + linkage
machinery turns them into cross-commodity price impacts. The brief always
shows the headline beside the simulated impact — the human judges relevance;
severities are priors, not measurements. No LLM in the loop.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import yaml

from .linkages import render_ripple, ripple

RULES_PATH = Path(__file__).resolve().parents[2] / "data" / "seed" / "news_rules.yaml"
GOOGLE_NEWS = "https://news.google.com/rss/search"


@dataclass
class NewsHit:
    headline: str
    link: str
    published: str
    rule_note: str
    commodity: str
    country: str
    severity: float


def load_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text())


def fetch_headlines(feeds: list[str], client: httpx.Client | None = None, limit: int = 25) -> list[dict]:
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True,
                                    headers={"User-Agent": "opencopper news brief"})
    items: list[dict] = []
    try:
        for q in feeds:
            try:
                r = client.get(GOOGLE_NEWS, params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
                r.raise_for_status()
            except Exception:
                continue
            for m in list(re.finditer(r"<item>(.*?)</item>", r.text, re.S))[:limit]:
                block = m.group(1)
                t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S)
                l = re.search(r"<link>(.*?)</link>", block, re.S)
                d = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
                if t:
                    items.append({"title": html.unescape(t.group(1).strip()),
                                  "link": (l.group(1).strip() if l else ""),
                                  "published": (d.group(1).strip() if d else "")})
    finally:
        if own:
            client.close()
    # dedupe by title
    seen, out = set(), []
    for it in items:
        key = it["title"].lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def recent_items(items: list[dict], max_age_days: int, now: datetime | None = None) -> list[dict]:
    """Google News search RSS resurfaces months-old stories; a daily brief
    only wants the last few days. Unparseable dates are kept (conservative)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    out = []
    for it in items:
        try:
            published = parsedate_to_datetime(it["published"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            out.append(it)
            continue
        if published >= cutoff:
            out.append(it)
    return out


def match_rules(items: list[dict], rules: list[dict]) -> list[NewsHit]:
    hits: list[NewsHit] = []
    for it in items:
        low = it["title"].lower()
        for rule in rules:
            # plain substring containment — rules are words, not regexes
            ok = all(any(alt in low for alt in term.split("|")) for term in rule["match"])
            if ok:
                hits.append(NewsHit(
                    headline=it["title"], link=it["link"], published=it["published"],
                    rule_note=rule.get("note", ""), commodity=rule["commodity"],
                    country=rule["country"], severity=rule["severity"],
                ))
                break  # one rule per headline
    return hits


def build_brief(hits: list[NewsHit], today: str) -> str:
    lines = [f"# News-driven model brief — {today}", "",
             "Headlines matched by transparent keyword rules; severities are PRIORS,",
             "impacts are first-order model mechanics (incidence + linkages). The",
             "human judges relevance. NOT investment advice.", ""]
    if not hits:
        lines.append("_No rule-matched headlines today._")
        return "\n".join(lines)
    # one block per EVENT (commodity, country, severity); corroborating
    # headlines listed compactly under it
    groups: dict[tuple, list[NewsHit]] = {}
    for h in hits:
        groups.setdefault((h.commodity, h.country, h.severity), []).append(h)
    for (commodity, country, severity), hs in groups.items():
        h = hs[0]
        src = f" — [source]({h.link})" if h.link else ""
        lines += [f"## {h.headline}", f"*{h.published}*{src}",
                  f"Rule: {commodity} / {country} / severity prior {severity:+.0%}"
                  + (f" — {h.rule_note}" if h.rule_note else ""), ""]
        if severity <= 0:
            lines += [f"_Supply addition (severity {severity:+.0%}) — eases the {commodity} balance._", ""]
        else:
            try:
                rows = ripple(commodity, country, severity)
            except Exception as exc:  # unattended runs must flag, never die
                lines += [f"_Headline flagged but impact not computed: {exc}_", ""]
            else:
                lines += ["```", render_ripple(rows, f"{country} {commodity} -{severity:.0%} (prior)"), "```", ""]
        if len(hs) > 1:
            lines.append(f"_{len(hs) - 1} corroborating headline(s):_")
            lines += [f"- [{x.headline}]({x.link})" for x in hs[1:4]]
            if len(hs) > 4:
                lines.append(f"- …and {len(hs) - 4} more")
            lines.append("")
    return "\n".join(lines)


def run_news(out_dir: Path = Path("out"), data_dir: Path = Path("data/news")) -> Path:
    from .theses import analytics, generate_auto_theses, mark_all

    cfg = load_rules()
    items = fetch_headlines(cfg["feeds"])
    fresh = recent_items(items, cfg.get("max_age_days", 14))
    hits = match_rules(fresh, cfg["rules"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"hits-{today}.json").write_text(json.dumps([asdict(h) for h in hits], indent=1))

    # every distinct event becomes a trackable thesis; the scorecard rides
    # along in the brief so the system grades yesterday's calls in the same
    # breath as it makes today's
    added = generate_auto_theses([asdict(h) for h in hits], today)
    a = analytics(mark_all())
    scorecard = ["", "---", "", "## Scorecard",
                 f"{a['n']} theses: {a['hit']} hit / {a['miss']} miss / {a['open']} open"
                 f" / {a['needs_res']} need resolution"
                 + (f" · hit rate {a['hit_rate']:.0%}" if a["hit_rate"] is not None else "")
                 + (f" · open auto avg move {a['open_auto_avg_move_pct']:+.1f}%"
                    if a["open_auto_avg_move_pct"] is not None else ""),
                 ""]
    scorecard += [f"- new thesis: **{t['id']}** — {t['claim']}" for t in added]
    scorecard.append("\nFull ledger: `opencopper theses` / the demo's Scorecard tab.")

    out_dir.mkdir(parents=True, exist_ok=True)
    brief = out_dir / "news-brief.md"
    brief.write_text(build_brief(hits, today) + "\n".join(scorecard))
    print(f"{len(items)} headlines ({len(fresh)} recent), {len(hits)} rule matches, "
          f"{len(added)} new theses -> {brief}")
    return brief
