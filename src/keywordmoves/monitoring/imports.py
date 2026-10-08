"""Adapt saved evidence into the monitor schema without fetching or inventing metrics."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from ..errors import InputError
from .validation import SCHEMA, canonical, observation, read_json

CONTEXT_KEYS = (
    "subject", "platform", "geography", "scope", "window", "evidence_kind",
    "period_start", "period_end", "normalization_id", "dimensions", "observed_at",
    "approximate", "censored", "completeness",
)
DIMENSION_KEYS = (
    "page", "video_id", "channel_id", "account_id", "kind", "language", "device",
    "category", "search_property", "provider", "analysis_signature",
)


def _context(metadata: dict[str, Any]) -> dict[str, Any]:
    # Only retain explicit semantic fields. File paths, counters and retrieval times
    # are provenance, not comparison dimensions.
    result = {key: metadata[key] for key in CONTEXT_KEYS if key in metadata}
    if "geography" not in result and isinstance(metadata.get("country"), str):
        result["geography"] = metadata["country"]
    dimensions = dict(result.get("dimensions") or {})
    for key in DIMENSION_KEYS:
        if key in metadata:
            dimensions[key] = metadata[key]
    result["dimensions"] = dimensions
    if not isinstance(result.get("scope", ""), str):
        dimensions["native_scope"] = result.pop("scope")
    return result


def _reviewed_row(row: Any) -> Any:
    """Adapt declared legacy observation labels, preserving their original wording."""
    if not isinstance(row, dict):
        return row
    result = dict(row)
    kinds = {
        "native-autocomplete": "language_suggestion",
        "native-caption-language": "language_suggestion",
        "native-search-sample": "language_suggestion",
        "third-party-estimate": "search_volume_estimate",
    }
    original_kind = result.get("evidence_kind")
    original_availability = result.get("availability", "observed")
    notes = [str(result["notes"])] if result.get("notes") else []
    if original_kind in kinds:
        result["evidence_kind"] = kinds[original_kind]
        notes.append("Imported reviewed evidence kind: " + original_kind + ".")
    if original_availability == "sampled-language":
        result["availability"] = "observed"
        notes.append("Original availability: sampled-language; no measured demand implied.")
    if original_availability in {"unavailable", "error", "suppressed", "no-data"}:
        if result.get("value") is not None:
            notes.append("Original unavailable display: " + str(result["value"]))
        result["value"] = None
    for key in ("capture_file", "capture_sha256"):
        if result.get(key) == "unavailable":
            notes.append("Original " + key + ": unavailable.")
            result[key] = None
    if result.get("limitations"):
        notes.append(str(result["limitations"]))
    if notes:
        result["notes"] = " ".join(notes)
    if original_kind == "third-party-estimate":
        result["approximate"] = True
        if isinstance(result.get("value"), str) and re.search(r"[<>]\s*\d", result["value"]):
            result["censored"] = True
    if result.get("seed_keyword"):
        result["dimensions"] = {**result.get("dimensions", {}),
                                "seed_keyword": result["seed_keyword"]}
    return result


def decode_payload(payload: Any, defaults: Mapping[str, Any] | None = None,
                   *, now: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise InputError("Monitoring evidence must be a JSON object.")
    defaults = dict(defaults or {})
    if set(defaults) - set(CONTEXT_KEYS):
        raise InputError("Unknown monitoring import context option.")
    if payload.get("schema") in {SCHEMA, "keywordmoves-observations/v1"}:
        rows = payload.get("observations")
        if not isinstance(rows, list) or not rows or len(rows) > 50000:
            raise InputError("Evidence needs between 1 and 50000 observation rows.")
        if payload["schema"] == "keywordmoves-observations/v1":
            rows = [_reviewed_row(row) for row in rows]
        return [observation(row, defaults, now=now) for row in rows]
    if not isinstance(payload.get("plugin"), str) or not isinstance(payload.get("keywords"), (list, tuple)):
        raise InputError("Import monitor observations, reviewed observations or a PluginResult JSON.")
    if len(payload["keywords"]) > 50000:
        raise InputError("PluginResult exceeds the monitoring row bound.")
    root = payload.get("metadata", {})
    if not isinstance(root, dict):
        raise InputError("PluginResult metadata must be an object.")
    root_context = {**_context(root), **defaults}
    if root.get("output_truncated") or root.get("incomplete_data"):
        root_context["completeness"] = "partial"
    result = []
    for item in payload["keywords"]:
        if not isinstance(item, dict) or not isinstance(item.get("evidence"), (list, tuple)):
            raise InputError("Each PluginResult candidate needs an evidence array.")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise InputError("Candidate metadata must be an object.")
        native = _context(metadata)
        context = {**root_context, **native}
        # Explicit caller settings fill gaps; they never override row-labelled
        # platforms, sources, units or geographic evidence.
        context["dimensions"] = {
            **root_context.get("dimensions", {}), **native.get("dimensions", {})}
        if (root.get("output_truncated") or root.get("incomplete_data")
                or metadata.get("output_truncated")):
            context["completeness"] = "partial"
        for evidence in item["evidence"]:
            if not isinstance(evidence, dict):
                raise InputError("Evidence entries must be objects.")
            row = {
                **context, "phrase": item.get("phrase"), "source": evidence.get("source"),
                "metric": evidence.get("metric"), "unit": evidence.get("unit"),
                "value": evidence.get("value"),
            }
            if evidence.get("observed_at") is not None:
                row["observed_at"] = evidence["observed_at"]
            if evidence.get("geography") is not None:
                row["geography"] = evidence["geography"]
            for key in ("availability", "capture_file", "capture_sha256",
                        "source_url", "target_surface", "notes", "normalization_id"):
                if key in metadata:
                    row[key] = metadata[key]
            if "capture_file" not in row and root.get("source_file"):
                row["capture_file"] = root["source_file"]
            if "capture_sha256" not in row and root.get("source_sha256"):
                row["capture_sha256"] = root["source_sha256"]
            if evidence.get("notes") is not None:
                row["notes"] = evidence["notes"]
            if payload["plugin"] == "observed-evidence":
                row["limitations"] = metadata.get("limitations")
                row["seed_keyword"] = metadata.get("seed_keyword")
                row = _reviewed_row(row)
            result.append(observation(row, now=now))
    if not result:
        raise InputError("An empty PluginResult is not a zero; record an explicit no-data observation.")
    return result


def load_evidence(path: Path, defaults: Mapping[str, Any] | None = None,
                  *, now: str | None = None) -> tuple[bytes, str, list[dict[str, Any]]]:
    raw, payload = read_json(path)
    rows = decode_payload(payload, defaults, now=now)
    sha = hashlib.sha256(raw).hexdigest()
    return raw, sha, rows


def snapshot_id(raw_sha: str, defaults: Mapping[str, Any] | None = None) -> str:
    return hashlib.sha256(canonical({
        "raw_sha256": raw_sha, "context": dict(defaults or {})}).encode("utf-8")).hexdigest()
