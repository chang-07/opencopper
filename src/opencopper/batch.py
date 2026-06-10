"""Bulk extraction via the Anthropic Batches API (50% of standard price).

Workflow:
    opencopper batch submit data/raw          -> batch id + manifest
    opencopper batch status <batch_id>
    opencopper batch collect <manifest.json>  -> data/extracted/*.json

Batches can't use ``messages.parse()``, so structured output goes through
``output_config.format`` with a strictified JSON schema, and results are
validated back through the same Pydantic model on collection — identical
guarantees, half the price.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel

from .extract import DEFAULT_MODEL, SYSTEM_PROMPT, load_document_text, relevant_sections
from .schema import ExtractedMineData

# JSON-schema keywords the structured-outputs grammar doesn't support; the SDK
# strips these in parse(), but batch requests carry the raw schema.
_UNSUPPORTED_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "pattern",
    "default",
}


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic schema -> structured-outputs-compatible schema."""
    schema = model.model_json_schema()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
            for key in list(node):
                if key in _UNSUPPORTED_KEYS:
                    node.pop(key)
                else:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema


def _custom_id(index: int, path: Path) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]", "-", path.stem)[:48]
    return f"x{index:04d}-{stem}"


def build_batch_requests(
    paths: list[Path], model: str = DEFAULT_MODEL, prefilter: bool = True
) -> tuple[list[dict], dict]:
    """Returns (requests, manifest_items) where manifest maps custom_id -> path."""
    schema = strict_schema(ExtractedMineData)
    requests: list[dict] = []
    manifest: dict[str, str] = {}
    for i, path in enumerate(paths):
        text = load_document_text(path)
        if prefilter:
            text = relevant_sections(text)
        cid = _custom_id(i, path)
        manifest[cid] = str(path)
        requests.append(
            {
                "custom_id": cid,
                "params": {
                    "model": model,
                    "max_tokens": 16000,
                    "thinking": {"type": "adaptive"},
                    "system": SYSTEM_PROMPT,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Extract the structured mine data from this Technical"
                                " Report Summary:\n\n" + text
                            ),
                        }
                    ],
                    "output_config": {"format": {"type": "json_schema", "schema": schema}},
                },
            }
        )
    return requests, manifest


def submit_batch(
    paths: list[Path],
    *,
    model: str = DEFAULT_MODEL,
    manifest_path: Path,
    client: Optional[anthropic.Anthropic] = None,
) -> str:
    client = client or anthropic.Anthropic()
    requests, manifest = build_batch_requests(paths, model)
    batch = client.messages.batches.create(requests=requests)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"batch_id": batch.id, "model": model, "items": manifest}, indent=2)
    )
    return batch.id


def batch_status(batch_id: str, client: Optional[anthropic.Anthropic] = None) -> dict:
    client = client or anthropic.Anthropic()
    batch = client.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    return {
        "status": batch.processing_status,
        "processing": counts.processing,
        "succeeded": counts.succeeded,
        "errored": counts.errored,
    }


def collect_results(
    manifest_path: Path,
    out_dir: Path,
    client: Optional[anthropic.Anthropic] = None,
) -> tuple[int, int]:
    """Validate and write extraction JSONs; returns (ok, failed)."""
    client = client or anthropic.Anthropic()
    manifest = json.loads(manifest_path.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for result in client.messages.batches.results(manifest["batch_id"]):
        cid = result.custom_id
        source = manifest["items"].get(cid, "?")
        if result.result.type != "succeeded":
            failed += 1
            print(f"  {cid}: {result.result.type} ({source})")
            continue
        message = result.result.message
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            data = ExtractedMineData.model_validate_json(text)
        except ValueError as exc:
            failed += 1
            print(f"  {cid}: schema validation failed ({exc})")
            continue
        data.source_filename = Path(source).name
        (out_dir / f"{cid}.json").write_text(data.model_dump_json(indent=2))
        ok += 1
    return ok, failed
