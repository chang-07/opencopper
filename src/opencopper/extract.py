"""LLM extraction of mine-level data from S-K 1300 Technical Report Summaries.

Design notes:
- Uses the Anthropic SDK's ``messages.parse()`` with a Pydantic schema, so the
  response is validated structured output — no JSON parsing or retries here.
- Every extracted value must carry a verbatim citation; the system prompt
  forbids values not stated in the document. Extraction accuracy is benchmarked
  separately (see tests / evals) — citations make spot-checking cheap.
- EX-96 exhibits are HTML; we strip to text before sending (3-5x token savings).
  PDF exhibits are skipped in v1.
- Default model is claude-opus-4-8. For bulk runs, pass model="claude-haiku-4-5"
  (~5x cheaper) or move to the Batches API (additional 50% off) — cost/quality
  tradeoff is the caller's choice.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import anthropic

from .schema import ExtractedMineData

DEFAULT_MODEL = "claude-opus-4-8"
MAX_DOC_CHARS = 600_000  # ~150K tokens; EX-96 summaries normally fit well under this

# The headline numbers (reserves, production, cash cost, mine life) cluster in a
# handful of sections of a 100-300 page technical report. The pre-filter keeps
# the executive summary plus the highest-signal sections and drops the rest,
# cutting input tokens ~80% and the per-document cost with it (and usually
# improving extraction quality by removing legal/QA/environmental boilerplate).
RELEVANT_TERMS = (
    "mineral reserve", "mineral resource", "proven", "probable", "measured",
    "indicated", "inferred", "life of mine", "life-of-mine", "annual production",
    "production schedule", "throughput", "cash cost", "c1 ", "all-in sustaining",
    "aisc", "recovery", "head grade", "mill feed", "contained copper",
    "contained metal", "cu grade", "copper grade", "copper production",
    "metal production", "payable", "tonnes per", "ktpa", "mtpa", "tpd",
)
_NUM = re.compile(r"\d[\d,\.]{2,}")
_PAGE_SPLIT = re.compile(r"(?=\[page \d+\])")

SYSTEM_PROMPT = """\
You extract structured data about copper mines from SEC S-K 1300 Technical Report
Summaries (mining disclosure documents).

Rules:
- Only report values explicitly stated in the document. If a field is not stated,
  leave it null. Never estimate or fill from outside knowledge.
- Every populated field needs a verbatim citation: a short exact quote from the
  document (and the section heading if identifiable).
- Normalize units to kt (thousand tonnes) of CONTAINED COPPER per year:
  - million lb (Mlb) copper x 0.4536 = kt
  - tonnes of ore is NOT copper content — only use it combined with grade and
    recovery if the document states all three; otherwise leave production null.
  - short tons x 0.9072 = tonnes
- annual_production_kt: prefer the most recent actual year stated; record which
  year in the field. Life-of-mine averages are acceptable only if no annual
  figure exists (note it in the citation and lower confidence).
- cash_cost_usd_lb: C1 / cash cost net of by-products if stated.
- commodities: ordered by revenue contribution if determinable, primary first.
- confidence: 1.0 = unambiguous single stated figure; lower it for unit
  conversions you performed, life-of-mine averages, or conflicting figures.
"""


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._chunks)


def html_to_text(raw: str) -> str:
    """Strip an HTML exhibit down to its text content."""
    parser = _TextExtractor()
    parser.feed(raw)
    return parser.text()


def pdf_to_text(path: Path) -> str:
    """Extract text from a PDF exhibit (most EX-96 filings are PDFs)."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"[page {i + 1}]\n{text}")
    return "\n\n".join(pages)


def load_document_text(path: Path) -> str:
    """Read an exhibit file (HTML, PDF, or plain text) and return plain text."""
    if path.suffix.lower() == ".pdf":
        return pdf_to_text(path)[:MAX_DOC_CHARS]
    raw = path.read_text(errors="replace")
    text = html_to_text(raw) if "<" in raw[:1000] else raw
    return text[:MAX_DOC_CHARS]


def _segment(text: str) -> list[str]:
    if "[page " in text:
        return [p for p in _PAGE_SPLIT.split(text) if p.strip()]
    return [text[i : i + 3000] for i in range(0, len(text), 3000)]


def _score(segment: str) -> float:
    low = segment.lower()
    keywords = sum(low.count(term) for term in RELEVANT_TERMS)
    numbers = len(_NUM.findall(segment))
    return keywords * 3 + numbers * 0.3


def relevant_sections(
    text: str,
    *,
    lead_segments: int = 8,
    top_k: int = 22,
    max_chars: int = 120_000,
) -> str:
    """Keep the lead (executive summary) + highest-signal sections of a report.

    Returns the document unchanged when it is already small enough that
    filtering would save nothing.
    """
    segments = _segment(text)
    if len(segments) <= lead_segments + top_k:
        return text[:max_chars]

    lead = list(range(min(lead_segments, len(segments))))
    scored = sorted(
        ((i, _score(s)) for i, s in enumerate(segments) if i >= lead_segments),
        key=lambda pair: -pair[1],
    )
    keep = sorted(set(lead) | {i for i, _ in scored[:top_k]})

    out: list[str] = []
    total = 0
    for i in keep:
        chunk = segments[i]
        if total + len(chunk) > max_chars:
            chunk = chunk[: max_chars - total]
        out.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n…\n".join(out)


def extract_mine_data(
    document_text: str,
    *,
    model: str = DEFAULT_MODEL,
    prefilter: bool = True,
    source_accession: Optional[str] = None,
    source_filename: Optional[str] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> ExtractedMineData:
    """Extract structured mine data from one technical report summary.

    `prefilter` keeps only the high-signal sections (big token/cost saving);
    `client` is injectable for testing; by default credentials resolve from
    the environment (ANTHROPIC_API_KEY).
    """
    if prefilter:
        document_text = relevant_sections(document_text)
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract the structured mine data from this Technical Report"
                    " Summary:\n\n" + document_text
                ),
            }
        ],
        output_format=ExtractedMineData,
    )
    data = response.parsed_output
    data.source_accession = source_accession
    data.source_filename = source_filename
    return data
