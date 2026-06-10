"""SEC EDGAR full-text search client for S-K 1300 Technical Report Summaries.

EDGAR's full-text search JSON API (the same endpoint behind
https://efts.sec.gov/LATEST/search-index) indexes individual exhibit files,
so a query for "technical report summary" + copper returns EX-96.* exhibits
directly. Documents are then fetched from the EDGAR archive at

    https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}

SEC fair-access rules: declare a real User-Agent and stay well under
10 requests/second. We pace at ~4/s.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
from pydantic import BaseModel

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"
USER_AGENT = "opencopper (open research project; contact: chang@snaptrade.com)"
REQUEST_INTERVAL_S = 0.25

DEFAULT_QUERY = '"technical report summary" copper'


class FilingHit(BaseModel):
    accession: str
    cik: str
    company: str
    filename: str
    form: str
    file_date: str

    @property
    def url(self) -> str:
        return (
            f"{ARCHIVE_URL}/{int(self.cik)}/"
            f"{self.accession.replace('-', '')}/{self.filename}"
        )


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30)


def _get_with_retry(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """EDGAR throttles and intermittently 500s; retry transient failures."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.get(url, params=params)
            if resp.status_code in (429, 500, 502, 503):
                raise httpx.HTTPStatusError(
                    f"transient {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise last_error  # type: ignore[misc]


def search_technical_reports(
    query: str = DEFAULT_QUERY,
    max_hits: int = 100,
    forms: str | None = None,
    filename_contains: str | None = "ex96",
) -> list[FilingHit]:
    """Search EDGAR full text for technical report summary exhibits.

    filename_contains keeps only actual EX-96 exhibits — the same search also
    matches auditor-consent letters (ex23) that merely mention the TRS.
    """
    hits: list[FilingHit] = []
    with _client() as client:
        page_from = 0
        while len(hits) < max_hits:
            params: dict[str, str | int] = {"q": query, "from": page_from}
            if forms:
                params["forms"] = forms
            payload = _get_with_retry(client, FTS_URL, params).json()
            batch = payload.get("hits", {}).get("hits", [])
            if not batch:
                break
            for h in batch:
                accession, _, filename = h["_id"].partition(":")
                src = h["_source"]
                ciks = src.get("ciks") or []
                names = src.get("display_names") or ["?"]
                if not ciks or not filename:
                    continue
                if filename_contains and filename_contains not in filename.lower():
                    continue
                hits.append(
                    FilingHit(
                        accession=accession,
                        cik=ciks[0],
                        company=names[0],
                        filename=filename,
                        form=src.get("root_forms", ["?"])[0],
                        file_date=src.get("file_date", "?"),
                    )
                )
            page_from += len(batch)
            time.sleep(REQUEST_INTERVAL_S)
    return hits[:max_hits]


def download_exhibit(hit: FilingHit, dest_dir: Path) -> Path:
    """Download one exhibit to dest_dir; returns the local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{hit.accession}__{hit.filename.replace('/', '_')}"
    if dest.exists():
        return dest
    with _client() as client:
        resp = _get_with_retry(client, hit.url)
        dest.write_bytes(resp.content)
    time.sleep(REQUEST_INTERVAL_S)
    return dest
