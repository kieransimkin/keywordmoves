"""Explicit Reddit imports, captured observations and compatible snapshot comparisons."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, PluginResult
from .common import boolean, integer, text
from .reddit_analysis import context, evidence, metric, timestamp

OBS_SCHEMA = "keywordmoves-reddit-observations/v1"
# These are distinct measures, never a single interchangeable popularity score.
OBS_METRICS = {
    "estimated_search_volume": "searches_per_month",
    "reported_conversation_volume": "conversations_in_period",
    "reported_mentions": "mentions_in_period",
    "reported_views": "views", "reported_impressions": "impressions",
    "reported_subscribers": "subscribers", "reported_net_score": "reported_net_score",
    "reported_comments": "comments", "reported_shares": "shares",
    "suggestion_position": "ordinal", "reported_paid_competition": "provider_native_scale",
}


def read(path: Path, maximum: int = 10_000_000) -> str:
    try:
        with Path(path).open("rb") as handle:
            content = handle.read(maximum + 1)
        if len(content) > maximum:
            raise InputError("Import exceeds the configured file-size limit.")
        return content.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise InputError("Cannot read the input as UTF-8; check path, encoding and permissions.") from None


def load_json(path: Path) -> Any:
    def invalid(_):
        raise ValueError
    try:
        return json.loads(read(path), parse_constant=invalid)
    except (ValueError, RecursionError):
        raise InputError("Input must be bounded, valid JSON with finite values.") from None


def flatten(payload: Any, maximum: int = 10000) -> tuple[list[dict], list[str]]:
    """Read native Listings/comment trees without recursive calls or invented pagination."""
    result, more, stack = [], [], [payload]
    visited = 0
    while stack:
        value = stack.pop()
        visited += 1
        if visited > maximum * 10:
            raise InputError("Reddit response tree exceeds the traversal limit.")
        if isinstance(value, list):
            stack.extend(reversed(value))
        elif isinstance(value, dict):
            k, data = value.get("kind"), value.get("data")
            if k == "Listing" and isinstance(data, dict) and isinstance(data.get("children"), list):
                stack.append(data["children"])
            elif k in {"t1", "t3", "t5"} and isinstance(data, dict):
                result.append(value)
                if data.get("replies"):
                    stack.append(data["replies"])
            elif k == "more" and isinstance(data, dict):
                children = data.get("children")
                if not isinstance(children, list) or any(not isinstance(i, str) for i in children):
                    raise InputError("Unexpected more-comments record.")
                more.extend(children)
            elif "id" in value and value.get("kind") in {"t1", "t3", "t5"}:
                result.append(value)
            else:
                raise InputError("Unrecognised Reddit JSON shape; use canonical records or native Listings.")
        else:
            raise InputError("Expected Reddit Listing/Thing objects, not scalar data.")
        if len(result) + len(more) > maximum:
            raise InputError("Reddit response exceeds max_records.")
    return result, list(dict.fromkeys(more))


def imported_records(path: Path, options: Mapping[str, Any], kind: str):
    """Canonical JSON/Reddit Things and CSV with explicit field mappings."""
    if path.suffix.lower() == ".csv":
        rows = []
        mappings = {}
        for field in ("id", "title", "body", "subreddit", "score", "num_comments", "upvote_ratio",
                      "created_at", "flair", "parent_id", "link_id", "permalink"):
            if field + "_column" in options:
                mappings[field] = text(options, field + "_column")
        if "id" not in mappings or not ({"title", "body"} & set(mappings)):
            raise ConfigurationError("CSV imports require id_column and title_column or body_column.")
        delimiter = text(options, "delimiter", ",")
        if len(delimiter) != 1 or delimiter in "\r\n\x00":
            raise ConfigurationError("delimiter must be one non-newline character.")
        reader = csv.DictReader(io.StringIO(read(path)), delimiter=delimiter)
        if not reader.fieldnames or not set(mappings.values()) <= set(reader.fieldnames):
            raise InputError("CSV is missing a mapped column.")
        for row in reader:
            if None in row or any(row.get(name) is None for name in mappings.values()):
                raise InputError("CSV row does not match the mapped header.")
            rows.append({"kind": kind, **{key: row[name] or None for key, name in mappings.items()}})
        return rows
    payload = load_json(path)
    if isinstance(payload, dict) and "records" in payload:
        payload = payload["records"]
    elif isinstance(payload, dict) and "metadata" in payload:
        payload = payload["metadata"].get("records")
        if payload is None:
            raise InputError("This output has no records; recollect using include_records=true.")
    rows, _ = flatten(payload, integer(options, "max_records", 1000, 1, 10000))
    return [row for row in rows if row.get("kind") == kind]


def observations(path: Path, options: Mapping[str, Any]) -> PluginResult:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != OBS_SCHEMA:
        raise InputError(f"Observations must use schema {OBS_SCHEMA}.")
    ctx = context(text(payload, "source"), text(payload, "scope"), payload.get("observed_at"),
                  country=payload.get("country"), period=payload.get("period"),
                  live_query_performed=False)
    rows = payload.get("observations")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise InputError("observations must be an array of at most 10000 objects.")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("metrics"), dict):
            raise InputError("Each observation needs phrase and metrics.")
        phrase = text(row, "phrase")
        kind = text(row, "kind", "keyword")
        entries = []
        approximate = boolean(row, "approximate")
        availability = text(row, "availability", "observed")
        if availability not in {"observed", "unavailable"}:
            raise InputError("availability must be observed or unavailable.")
        if availability == "unavailable" and any(v not in (None, "") for v in row["metrics"].values()):
            raise InputError("Unavailable observations must not contain fabricated numeric measurements.")
        for key, value in row["metrics"].items():
            if key not in OBS_METRICS:
                raise InputError("Unknown Reddit observation metric; use the documented names.")
            # A rounded string is retained as display text only; numeric precision is not invented.
            if approximate and isinstance(value, str):
                val = value
            else:
                val = metric(value, signed=key == "reported_net_score")
            entries.append(evidence(ctx, key, val, OBS_METRICS[key],
                                    "Source-reported observation; not independently measured."))
        candidates.append(KeywordCandidate(phrase, "reddit-observation", None, tuple(entries),
                          {"kind": kind, "approximate": approximate,
                           "country": ctx["country"], "period": ctx["period"], "scope": ctx["scope"],
                           "availability": availability}))
    limit = integer(options, "limit", 50, 1, 1000)
    return PluginResult("reddit", "import-observations", tuple(candidates[:limit]),
                        ("Reported mentions, search estimates and views are distinct measurements.",),
                        {**ctx, "rows_received": len(rows), "output_truncated": len(rows) > limit})


def html_records(path: Path, options: Mapping[str, Any], kind: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConfigurationError("Install keywordmoves[online] for saved HTML imports.") from None
    soup = BeautifulSoup(read(path), "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    mappings = {field: text(options, field + "_selector") for field in ("id", "title", "body",
                "subreddit", "score", "num_comments", "created_at") if field + "_selector" in options}
    if "id" not in mappings or not ({"title", "body"} & set(mappings)):
        raise ConfigurationError("HTML imports require id_selector and title_selector or body_selector.")
    try:
        elements = soup.select(text(options, "row_selector"))
        if not elements:
            if "empty_selector" in options and soup.select(text(options, "empty_selector")):
                return []
            raise InputError("No result rows found; login/challenge/changed markup is not zero demand.")
        rows = []
        for element in elements:
            row = {"kind": kind}
            for field, selector in mappings.items():
                found = element.select_one(selector)
                if not found and field in {"id", "title", "body"}:
                    raise InputError("Required result field is missing from captured HTML.")
                row[field] = found.get_text(" ", strip=True) if found else None
            rows.append(row)
        return rows
    except InputError:
        raise
    except Exception:
        raise InputError("Invalid or unsupported HTML selector; no partial import was returned.") from None


def compare(paths: tuple[Path, ...], options: Mapping[str, Any]) -> PluginResult:
    if len(paths) != 2:
        raise ConfigurationError("compare requires exactly two saved Reddit results, oldest first.")
    first, second = (load_json(path) for path in paths)
    for item in (first, second):
        if not isinstance(item, dict) or item.get("plugin") != "reddit" or not isinstance(item.get("keywords"), list):
            raise InputError("Compare saved Reddit PluginResult JSON outputs.")
    a, b = first.get("metadata", {}), second.get("metadata", {})
    keys = ("platform", "source", "scope", "country", "period", "analysis_signature")
    if a.get("platform") != "Reddit" or not all(a.get(k) == b.get(k) for k in keys) or not a.get("scope") or not a.get("source"):
        raise InputError("Snapshots differ in source, scope, geography, period or analysis settings.")
    if a.get("output_truncated") or b.get("output_truncated"):
        raise InputError("Compare untruncated outputs; increase limit when creating the snapshots.")
    begin, end = timestamp(a.get("observed_at"), required=True), timestamp(b.get("observed_at"), required=True)
    seconds = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
               datetime.fromisoformat(begin.replace("Z", "+00:00"))).total_seconds()
    if seconds <= 0:
        raise InputError("The second snapshot must be newer than the first.")
    indexed = []
    for payload in (first, second):
        index = {}
        for item in payload["keywords"]:
            if item.get("metadata", {}).get("approximate"):
                continue
            for entry in item.get("evidence", []):
                value = entry.get("value")
                if isinstance(value, bool) or (value is not None and not isinstance(value, (int, float))):
                    continue
                key = (item["phrase"], item["relationship"], entry["metric"], entry.get("unit"),
                       entry.get("source"), entry.get("geography"))
                if key in index:
                    raise InputError("Duplicate metric keys make snapshot comparison ambiguous.")
                index[key] = value
        indexed.append(index)
    output = []
    ctx = context(a["source"], a["scope"], end, country=a.get("country"), period=a.get("period"),
                  live_query_performed=False)
    for key in sorted(indexed[0].keys() | indexed[1].keys(), key=str):
        before, after = indexed[0].get(key), indexed[1].get(key)
        delta = after - before if before is not None and after is not None else None
        ev = (evidence(ctx, key[2] + "_net_change", delta, key[3]),)
        output.append(KeywordCandidate(key[0], "reddit-comparison", None, ev,
                      {"metric": key[2], "source_relationship": key[1], "before": before, "after": after,
                       "before_present": key in indexed[0], "after_present": key in indexed[1],
                       "net_change_per_day": delta * 86400 / seconds if delta is not None else None,
                       "percent_change": delta / before * 100 if delta is not None and before > 0 else None}))
    limit = integer(options, "limit", 50, 1, 1000)
    return PluginResult("reddit", "compare", tuple(output[:limit]), (
        "Net change describes compatible reported snapshots, not exact new votes or complete population growth.",
        "An absent observation remains unknown. Samples may change; independently delete removed/expired data.",),
        {**ctx, "before_observed_at": begin, "elapsed_seconds": seconds,
         "output_truncated": len(output) > limit, "metric_comparisons": len(output)})
