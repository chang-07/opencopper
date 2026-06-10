"""Extraction module tests — the LLM call is faked; live extraction quality is
benchmarked separately against company guidance (see README roadmap)."""

from types import SimpleNamespace

import pytest

from opencopper.extract import extract_mine_data, html_to_text, load_document_text
from opencopper.schema import Citation, ExtractedField, ExtractedMineData


def test_html_to_text_strips_markup():
    html = """
    <html><head><style>body{color:red}</style><script>var x=1;</script></head>
    <body><h1>Technical Report Summary</h1>
    <p>The Escondida mine produced <b>1,280 kt</b> of copper.</p></body></html>
    """
    text = html_to_text(html)
    assert "Technical Report Summary" in text
    assert "1,280 kt" in text
    assert "var x=1" not in text
    assert "color:red" not in text


def test_load_document_rejects_pdf(tmp_path):
    pdf = tmp_path / "exhibit.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="PDF"):
        load_document_text(pdf)


class _FakeClient:
    """Stands in for anthropic.Anthropic; returns a canned parsed_output."""

    def __init__(self, parsed: ExtractedMineData):
        self._parsed = parsed
        self.last_kwargs = None
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.last_kwargs = kwargs
                return SimpleNamespace(parsed_output=outer._parsed)

        self.messages = _Messages()


def test_extract_attaches_source_and_passes_document():
    canned = ExtractedMineData(
        mine_name="Test Mine",
        country="Chile",
        commodities=["copper"],
        annual_production_kt=ExtractedField(
            value=100.0,
            unit="kt Cu",
            year=2024,
            citation=Citation(quote="produced 100 kt of copper in 2024"),
            confidence=0.95,
        ),
    )
    client = _FakeClient(canned)
    result = extract_mine_data(
        "some document text",
        source_accession="0001-23-456",
        source_filename="ex96.htm",
        client=client,
    )
    assert result.mine_name == "Test Mine"
    assert result.source_accession == "0001-23-456"
    assert result.source_filename == "ex96.htm"
    assert result.is_copper_primary()
    # the document text actually reached the API call
    sent = client.last_kwargs["messages"][0]["content"]
    assert "some document text" in sent
    assert client.last_kwargs["output_format"] is ExtractedMineData
