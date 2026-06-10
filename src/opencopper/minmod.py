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

class CommodityRules(BaseModel):
    """Per-commodity physical-plausibility rules. Each commodity has its own
    physics: a fine zinc grade is an impossible lithium grade, and the largest
    credible single deposit differs by orders of magnitude. Every number here
    is a disputable round figure with its rationale in the comment — PRs with
    better geology welcome."""

    query: str  # exact name the MinMod API accepts
    ceiling_kt: float  # max credible contained metal in ONE deposit
    grade_bounds_pct: tuple[float, float]


# Ceilings anchor on the largest known real deposits/districts; grade bounds on
# realistic ore grades for the commodity's deposit classes.
COMMODITIES: dict[str, CommodityRules] = {
    # El Teniente / Escondida district ~100-150 Mt Cu; porphyries 0.2-1%,
    # best sediment-hosted a few %.
    "copper": CommodityRules(query="copper", ceiling_kt=150_000, grade_bounds_pct=(0.05, 15.0)),
    # Norilsk-Talnakh is the giant at ~tens of Mt Ni; laterites ~1-2%,
    # massive sulfides up to ~5-8%.
    "nickel": CommodityRules(query="nickel", ceiling_kt=30_000, grade_bounds_pct=(0.1, 8.0)),
    # Red Dog / Broken Hill scale ~25-35 Mt Zn; zinc lenses can run >25%.
    "zinc": CommodityRules(query="zinc", ceiling_kt=60_000, grade_bounds_pct=(0.5, 40.0)),
    # Big Cu-Co deposits hold a few Mt Co at 0.1-0.5% typical grades.
    "cobalt": CommodityRules(query="cobalt", ceiling_kt=8_000, grade_bounds_pct=(0.01, 4.0)),
    # Atacama-scale brine systems ~8-9 Mt Li metal; spodumene ~0.3-0.9% Li,
    # brines lower (grades here are Li metal %, not Li2O).
    "lithium": CommodityRules(query="lithium", ceiling_kt=15_000, grade_bounds_pct=(0.01, 6.0)),
    # Bayan Obo holds ~35-50 Mt REO; carbonatite grades up to ~6-10% TREO.
    "rare-earths": CommodityRules(
        query="rare earth elements", ceiling_kt=60_000, grade_bounds_pct=(0.02, 12.0)
    ),
}

# Back-compat aliases for the copper-only API.
PLAUSIBLE_MAX_KT = COMMODITIES["copper"].ceiling_kt
GRADE_BOUNDS_PCT = COMMODITIES["copper"].grade_bounds_pct

# A grade-tonnage triple must be internally consistent:
# contained ≈ tonnage x grade. Disagreement beyond this relative tolerance
# means at least one of the three numbers is wrong — quarantine, since we
# cannot know which.
CONSISTENCY_TOL = 0.15

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


def fetch_sites(
    commodity: str = "copper",
    client: Optional[httpx.Client] = None,
    max_sites: Optional[int] = None,
) -> list[MinModSite]:
    rules = COMMODITIES[commodity]
    client = client or _client()
    countries, deposit_types = fetch_lookups(client)
    sites: list[MinModSite] = []
    offset = 0
    while True:
        batch = client.get(
            "/dedup-mineral-sites",
            params={
                "commodity": rules.query,
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


def fetch_copper_sites(
    client: Optional[httpx.Client] = None, max_sites: Optional[int] = None
) -> list[MinModSite]:
    return fetch_sites("copper", client=client, max_sites=max_sites)


def cache_path(commodity: str) -> Path:
    return Path("data/minmod") / f"{commodity}-sites.json"


def save_sites(sites: list[MinModSite], path: Path = DEFAULT_CACHE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.model_dump() for s in sites], indent=1))
    return path


def load_sites(path: Path = DEFAULT_CACHE) -> list[MinModSite]:
    return [MinModSite(**r) for r in json.loads(path.read_text())]


def quarantine_reason(
    site: MinModSite,
    max_kt: float = PLAUSIBLE_MAX_KT,
    grade_bounds: tuple[float, float] = GRADE_BOUNDS_PCT,
) -> Optional[str]:
    """Why a site's grade-tonnage record can't be trusted, or None if it can."""
    if not site.contained_kt:
        return None  # nothing to judge; site stays usable as a located deposit
    if site.contained_kt > max_kt:
        return "above unit ceiling"
    if site.grade_pct is not None and not (grade_bounds[0] <= site.grade_pct <= grade_bounds[1]):
        return "implausible grade"
    if site.tonnage_mt and site.grade_pct:
        implied_kt = site.tonnage_mt * site.grade_pct / 100 * 1000
        if implied_kt > 0 and abs(site.contained_kt - implied_kt) / implied_kt > CONSISTENCY_TOL:
            return "grade x tonnage inconsistent"
    return None


def quarantine_with_reasons(
    sites: list[MinModSite],
    commodity: str = "copper",
) -> tuple[list[MinModSite], list[tuple[MinModSite, str]]]:
    rules = COMMODITIES[commodity]
    plausible, quarantined = [], []
    for s in sites:
        reason = quarantine_reason(s, rules.ceiling_kt, rules.grade_bounds_pct)
        if reason:
            quarantined.append((s, reason))
        else:
            plausible.append(s)
    return plausible, quarantined


def partition_plausible(
    sites: list[MinModSite], commodity: str = "copper"
) -> tuple[list[MinModSite], list[MinModSite]]:
    """Split into (plausible, quarantined) on all validity checks."""
    plausible, quarantined = quarantine_with_reasons(sites, commodity)
    return plausible, [s for s, _ in quarantined]


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


def render_report(sites: list[MinModSite], ledger, commodity: str = "copper") -> str:
    plausible, quarantined = quarantine_with_reasons(sites, commodity)
    with_gt = [s for s in plausible if s.contained_kt]
    total_mt = sum(s.contained_kt for s in with_gt) / 1000
    junk_mt = sum(s.contained_kt for s, _ in quarantined) / 1000
    reasons: dict[str, int] = {}
    for _, reason in quarantined:
        reasons[reason] = reasons.get(reason, 0) + 1
    reason_summary = ", ".join(f"{n} {r}" for r, n in sorted(reasons.items(), key=lambda x: -x[1]))
    lines = [
        f"MinMod {commodity} sites with grade-tonnage: {len(sites):,}",
        f"  plausible:   {len(plausible):,}  ({total_mt:,.0f} Mt contained metal — deposits, not production)",
        f"  quarantined: {len(quarantined):,}  ({junk_mt:,.0f} Mt of suspect records excluded: {reason_summary or 'none'})"
        " — machine extraction needs verification layers",
        "",
        "LARGEST PLAUSIBLE DEPOSITS:",
    ]
    for s in sorted(with_gt, key=lambda s: -s.contained_kt)[:12]:
        lines.append(
            f"  {s.name[:34]:<34} {s.country or '?':<18} "
            f"{s.contained_kt:>10,.0f} kt  [{s.deposit_type or '?'}]"
        )
    if commodity == "copper":
        matches = match_ledger(sites, ledger)
        lines += ["", f"LEDGER MATCHES ({len(matches)} of {len(ledger.mines)} tracked mines):"]
        for mine, site in sorted(matches, key=lambda p: -(p[1].contained_kt or 0)):
            lines.append(
                f"  {mine.name:<18} <- {site.name[:30]:<30} "
                f"contained {site.contained_kt or 0:>9,.0f} kt (MinMod reference, deposits-basis)"
            )
    return "\n".join(lines)
