"""Extraction module tests — the LLM call is faked; live extraction quality is
benchmarked separately against company guidance (see README roadmap)."""

from types import SimpleNamespace

import pytest

from opencopper.extract import (
    extract_mine_data,
    html_to_text,
    load_document_text,
    relevant_sections,
)
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


def test_load_document_extracts_pdf(tmp_path, monkeypatch):
    import pypdf

    class _FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _FakeReader:
        def __init__(self, path):
            self.pages = [_FakePage("Mineral Reserves: 1,000 kt"), _FakePage("  ")]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    pdf = tmp_path / "exhibit.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    text = load_document_text(pdf)
    assert "Mineral Reserves: 1,000 kt" in text
    assert "[page 1]" in text
    assert "[page 2]" not in text  # blank pages dropped


def test_relevant_sections_keeps_signal_drops_filler():
    # 8 lead pages + 40 filler pages + 1 dense reserve page
    lead = "".join(f"[page {i}]\nIntroduction and qualified persons.\n" for i in range(1, 9))
    filler = "".join(
        f"[page {i}]\nEnvironmental permitting and community relations boilerplate.\n"
        for i in range(9, 49)
    )
    signal = (
        "[page 49]\nMineral Reserve Statement: Proven and Probable reserves of "
        "1,250 Mt at 0.42% copper grade. Life of mine 28 years. Annual production "
        "180 ktpa contained copper. C1 cash cost 1.45 $/lb.\n"
    )
    text = lead + filler + signal
    filtered = relevant_sections(text, lead_segments=8, top_k=10, max_chars=100_000)
    assert "Mineral Reserve Statement" in filtered          # signal kept
    assert "180 ktpa" in filtered
    assert "[page 1]" in filtered                            # lead kept
    assert filtered.count("boilerplate") < 40               # filler thinned
    assert len(filtered) < len(text)


def test_relevant_sections_passthrough_when_small():
    text = "[page 1]\nshort doc\n[page 2]\nalso short\n"
    assert relevant_sections(text) == text


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
