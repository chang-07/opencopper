"""Batch extraction plumbing — request construction, schema strictness,
manifest round-trip, result validation. The API itself is faked."""

import json
from types import SimpleNamespace

from opencopper.batch import (
    build_batch_requests,
    collect_results,
    strict_schema,
    submit_batch,
)
from opencopper.schema import ExtractedMineData


def _walk(node, fn):
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            _walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk(v, fn)


def test_strict_schema_conforms_to_structured_outputs_rules():
    schema = strict_schema(ExtractedMineData)
    problems = []

    def check(node):
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") is not False:
                problems.append(f"object without additionalProperties:false: {list(node)[:4]}")
        for key in ("minimum", "maximum", "minLength", "maxLength", "pattern", "default"):
            if key in node:
                problems.append(f"unsupported key {key}")

    _walk(schema, check)
    assert not problems, problems


def test_build_requests_embeds_document_and_schema(tmp_path):
    doc = tmp_path / "mine.htm"
    doc.write_text("<html><body>Production was 100 kt in 2024.</body></html>")
    requests, manifest = build_batch_requests([doc], model="claude-opus-4-8")
    assert len(requests) == 1
    req = requests[0]
    assert req["custom_id"] in manifest
    assert manifest[req["custom_id"]] == str(doc)
    assert "Production was 100 kt" in req["params"]["messages"][0]["content"]
    fmt = req["params"]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False


class _FakeBatches:
    def __init__(self, results=()):
        self._results = list(results)
        self.created_with = None

    def create(self, requests):
        self.created_with = requests
        return SimpleNamespace(id="batch_test_123")

    def results(self, batch_id):
        assert batch_id == "batch_test_123"
        return iter(self._results)


class _FakeClient:
    def __init__(self, results=()):
        self.messages = SimpleNamespace(batches=_FakeBatches(results))


def _success(custom_id: str, payload: dict):
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(
            type="succeeded",
            message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            ),
        ),
    )


def test_submit_and_collect_round_trip(tmp_path):
    doc = tmp_path / "elarco.htm"
    doc.write_text("<p>El Arco copper project</p>")
    manifest_path = tmp_path / "manifest.json"

    client = _FakeClient()
    batch_id = submit_batch([doc], manifest_path=manifest_path, client=client)
    assert batch_id == "batch_test_123"
    manifest = json.loads(manifest_path.read_text())
    cid = next(iter(manifest["items"]))

    good = _success(cid, {"mine_name": "El Arco", "country": "Mexico", "commodities": ["copper"]})
    bad = SimpleNamespace(custom_id="x9999-missing", result=SimpleNamespace(type="errored"))
    invalid = _success(cid + "b", {"not_a_field": True})

    out_dir = tmp_path / "extracted"
    client2 = _FakeClient(results=[good, bad, invalid])
    ok, failed = collect_results(manifest_path, out_dir, client=client2)
    assert (ok, failed) == (1, 2)

    written = list(out_dir.glob("*.json"))
    assert len(written) == 1
    data = ExtractedMineData.model_validate_json(written[0].read_text())
    assert data.mine_name == "El Arco"
    assert data.source_filename == "elarco.htm"
