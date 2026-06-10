"""MinMod ingestion: the DARPA CriticalMAAS copper deposit knowledge graph.

MinMod (minmod.isi.edu, MIT-licensed) is LLM-extracted mineral-site data from
NI 43-101 / SEC filings — which makes it the route into the Canadian-filer
universe without scraping SEDAR+. Its public API serves ~4,300 deduplicated
copper sites with grade-tonnage models.

Two deliberate caveats, stated here because they shape how the data is used:

1. MinMod models DEPOSITS (contained metal in the ground), not PRODUCTION.
   It can enrich reserves and map the global deposit pipeline; it cannot feed
   the production side of the balance model.
2. The data is research-grade machine extraction. Values land in this project
   as references to reconcile against, never as silent ledger updates.

TLS note: the server omits its intermediate certificate, so Python's bundled
CA store (certifi) fails the chain while browsers and macOS curl succeed via
the OS trust store. We use `truststore` (the same approach pip uses) rather
than disabling verification.
"""

from __future__ import annotations

import json
import ssl
import time
from pathlib import Path
from typing import Optional

import httpx
import truststore
from pydantic import BaseModel

MINMOD_API = "https://minmod.isi.edu/api/v1"
DEFAULT_CACHE = Path("data/minmod/copper-sites.json")
PAGE_SIZE = 500
REQUEST_INTERVAL_S = 0.2

# Physical-plausibility ceiling for a single deposit's contained copper.
# The largest known deposits (El Teniente, Escondida district) hold roughly
# 100-150 Mt contained Cu; USGS puts ALL world reserves near 1,000,000 kt.
# Anything above this is almost certainly an upstream unit-conversion error
# (e.g. Mlb read as Mt) and is quarantined, not used.
PLAUSIBLE_MAX_KT = 150_000.0

_NAME_STOPWORDS = {
    "the", "project", "mine", "mines", "mining", "operation", "operations",
    "deposit", "district", "property", "prospect", "complex",
}


class MinModSite(BaseModel):
    minmod_id: str
    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    country: Optional[str] = None
    deposit_type: Optional[str] = None
    contained_kt: Optional[float] = None  # contained copper, kt
    tonnage_mt: Optional[float] = None
    grade_pct: Optional[float] = None
    modified_at: Optional[str] = None


def _client() -> httpx.Client:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(
        base_url=MINMOD_API,
        verify=ctx,
        timeout=60,
        headers={"User-Agent": "opencopper (open research project)"},
    )


def _qid(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


def fetch_lookups(client: httpx.Client) -> tuple[dict, dict]:
    """QID -> name maps for countries and deposit types."""
    countries = {_qid(c["uri"]): c["name"] for c in client.get("/countries").json()}
    deposit_types = {_qid(d["uri"]): d["name"] for d in client.get("/deposit-types").json()}
    return countries, deposit_types


def normalize_record(rec: dict, countries: dict, deposit_types: dict) -> MinModSite:
    """One dedup-mineral-sites record -> a flat MinModSite."""
    gt_entries = [g for g in rec.get("grade_tonnage", []) if g.get("total_contained_metal")]
    best_gt = max(gt_entries, key=lambda g: g["total_contained_metal"], default=None)

    location = rec.get("location") or {}
    country_qids = location.get("country") or []
    country = ", ".join(filter(None, (countries.get(_qid(q) if "/" in str(q) else q) for q in country_qids))) or None

    dts = rec.get("deposit_types") or []
    best_dt = max(dts, key=lambda d: d.get("confidence", 0), default=None)
    deposit_type = None
    if best_dt:
        dt_id = best_dt["id"] if "/" not in str(best_dt["id"]) else _qid(best_dt["id"])
        deposit_type = deposit_types.get(dt_id)

    return MinModSite(
        minmod_id=rec["id"],
        name=" ".join(rec.get("name", "?").split()),
        lat=location.get("lat"),
        lon=location.get("lon"),
        country=country,
        deposit_type=deposit_type,
        contained_kt=round(best_gt["total_contained_metal"] * 1000, 1) if best_gt else None,
        tonnage_mt=best_gt.get("total_tonnage") if best_gt else None,
        grade_pct=best_gt.get("total_grade") if best_gt else None,
        modified_at=rec.get("modified_at"),
    )


def fetch_copper_sites(
    client: Optional[httpx.Client] = None,
    max_sites: Optional[int] = None,
) -> list[MinModSite]:
    client = client or _client()
    countries, deposit_types = fetch_lookups(client)
    sites: list[MinModSite] = []
    offset = 0
    while True:
        batch = client.get(
            "/dedup-mineral-sites",
            params={
                "commodity": "copper",
                "has_grade_tonnage": True,
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        ).json()
        sites.extend(normalize_record(r, countries, deposit_types) for r in batch)
        offset += len(batch)
        if len(batch) < PAGE_SIZE or (max_sites and offset >= max_sites):
            break
        time.sleep(REQUEST_INTERVAL_S)
    return sites[:max_sites] if max_sites else sites


def save_sites(sites: list[MinModSite], path: Path = DEFAULT_CACHE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.model_dump() for s in sites], indent=1))
    return path


def load_sites(path: Path = DEFAULT_CACHE) -> list[MinModSite]:
    return [MinModSite(**r) for r in json.loads(path.read_text())]


def partition_plausible(sites: list[MinModSite]) -> tuple[list[MinModSite], list[MinModSite]]:
    """Split into (plausible, quarantined) on the contained-copper ceiling."""
    plausible, quarantined = [], []
    for s in sites:
        if s.contained_kt and s.contained_kt > PLAUSIBLE_MAX_KT:
            quarantined.append(s)
        else:
            plausible.append(s)
    return plausible, quarantined


def _normalize_name(name: str) -> frozenset[str]:
    tokens = "".join(c if c.isalnum() or c.isspace() else " " for c in name.lower()).split()
    return frozenset(t for t in tokens if t not in _NAME_STOPWORDS)


def match_ledger(sites: list[MinModSite], ledger) -> list[tuple]:
    """(MineRecord, MinModSite) pairs where normalized names overlap fully.

    Only plausible sites are matched — a quarantined unit-error record must
    never become a ledger reference.
    """
    plausible, _ = partition_plausible(sites)
    matches = []
    for mine in ledger.mines:
        mine_tokens = _normalize_name(mine.name)
        if not mine_tokens:
            continue
        candidates = []
        for site in plausible:
            site_tokens = _normalize_name(site.name)
            if site_tokens and (mine_tokens <= site_tokens or site_tokens <= mine_tokens):
                candidates.append(site)
        if candidates:
            best = max(candidates, key=lambda s: s.contained_kt or 0)
            matches.append((mine, best))
    return matches


def render_report(sites: list[MinModSite], ledger) -> str:
    plausible, quarantined = partition_plausible(sites)
    with_gt = [s for s in plausible if s.contained_kt]
    total_mt = sum(s.contained_kt for s in with_gt) / 1000
    junk_mt = sum(s.contained_kt for s in quarantined) / 1000
    lines = [
        f"MinMod copper sites with grade-tonnage: {len(sites):,}",
        f"  plausible:   {len(plausible):,}  ({total_mt:,.0f} Mt contained Cu — deposits, not production)",
        f"  quarantined: {len(quarantined):,}  (> {PLAUSIBLE_MAX_KT/1000:,.0f} Mt each; {junk_mt:,.0f} Mt of suspected"
        " unit-conversion errors excluded — machine extraction needs verification layers)",
        "",
        "LARGEST PLAUSIBLE DEPOSITS:",
    ]
    for s in sorted(with_gt, key=lambda s: -s.contained_kt)[:12]:
        lines.append(
            f"  {s.name[:34]:<34} {s.country or '?':<18} "
            f"{s.contained_kt:>10,.0f} kt  [{s.deposit_type or '?'}]"
        )
    matches = match_ledger(sites, ledger)
    lines += ["", f"LEDGER MATCHES ({len(matches)} of {len(ledger.mines)} tracked mines):"]
    for mine, site in sorted(matches, key=lambda p: -(p[1].contained_kt or 0)):
        lines.append(
            f"  {mine.name:<18} <- {site.name[:30]:<30} "
            f"contained {site.contained_kt or 0:>9,.0f} kt (MinMod reference, deposits-basis)"
        )
    return "\n".join(lines)
