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

from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import anthropic

from .schema import ExtractedMineData

DEFAULT_MODEL = "claude-opus-4-8"
MAX_DOC_CHARS = 600_000  # ~150K tokens; EX-96 summaries normally fit well under this

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


def load_document_text(path: Path) -> str:
    """Read an exhibit file and return plain text. PDFs are not supported in v1."""
    if path.suffix.lower() == ".pdf":
        raise ValueError(f"PDF exhibits not supported yet, skipping: {path.name}")
    raw = path.read_text(errors="replace")
    text = html_to_text(raw) if "<" in raw[:1000] else raw
    return text[:MAX_DOC_CHARS]


def extract_mine_data(
    document_text: str,
    *,
    model: str = DEFAULT_MODEL,
    source_accession: Optional[str] = None,
    source_filename: Optional[str] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> ExtractedMineData:
    """Extract structured mine data from one technical report summary.

    `client` is injectable for testing; by default credentials resolve from
    the environment (ANTHROPIC_API_KEY).
    """
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
