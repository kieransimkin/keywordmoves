"""Strict local monitoring schemas; no network access or universal demand score."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..errors import InputError

SCHEMA = "keywordmoves-monitor-observations/v1"
WATCH_SCHEMA = "keywordmoves-watchlist/v1"
KINDS = {
    "search_volume_estimate", "relative_interest", "property_performance",
    "channel_referral", "platform_search_interest", "content_supply",
    "sampled_engagement", "language_suggestion", "text_salience", "proposal", "unknown",
}
DEMAND_KINDS = {"search_volume_estimate", "relative_interest", "platform_search_interest"}
SECRET_KEYS = {
    "api_key", "access_token", "refresh_token", "password", "cookie", "cookies",
    "authorization", "client_secret", "developer_token", "llm_api_key",
}
IDENTITY_FIELDS = (
    "subject", "platform", "phrase", "source", "metric", "unit", "geography",
    "scope", "window", "evidence_kind", "dimensions", "normalization_id",
)
DEFAULT_CONTEXT = {
    "subject": "", "platform": "", "scope": "unspecified", "window": "unspecified",
    "geography": "unspecified", "evidence_kind": "unknown", "dimensions": {},
}
MAX_BYTES = 20_000_000


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InputError("Monitoring data must be finite JSON values.") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normal(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip()).casefold()


def label(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise InputError(f"{field} must be non-empty text, at most 4000 characters.")
    if any(ord(c) < 32 for c in value):
        raise InputError(f"{field} cannot contain control characters.")
    return unicodedata.normalize("NFC", value.strip())


def stamp(value: Any) -> str:
    if not isinstance(value, str):
        raise InputError("Dates must use ISO dates or timezone-aware ISO timestamps.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(),
                                      tzinfo=timezone.utc)
        elif re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        else:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError as exc:
        raise InputError("Dates must use ISO dates or timezone-aware ISO timestamps.") from exc


def now_stamp(value: str | None = None) -> str:
    return stamp(value) if value else datetime.now(timezone.utc).isoformat()


def finite(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(f"{field} must be a finite number, not a boolean.")
    return value


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise InputError(f"{field} must be a boolean.")
    return value


def reject_secrets(value: Any) -> None:
    if isinstance(value, dict):
        if any(str(key).casefold() in SECRET_KEYS for key in value):
            raise InputError("Do not put credentials in monitoring files; use environment secrets.")
        for item in value.values():
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


def read_json(path: Path) -> tuple[bytes, Any]:
    try:
        if path.stat().st_size > MAX_BYTES:
            raise InputError("Monitoring input exceeds the 20 MB bound.")
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise InputError("Monitoring input exceeds the 20 MB bound.")
        obj = json.loads(raw.decode("utf-8-sig"),
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        reject_secrets(obj)
        return raw, obj
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise InputError("Monitoring input must be readable, bounded UTF-8 JSON.") from exc


def observation(row: Mapping[str, Any], defaults: Mapping[str, Any] | None = None,
                *, now: str | None = None) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise InputError("Each monitoring observation must be an object.")
    data = {**DEFAULT_CONTEXT, **(defaults or {}), **row}
    result = {key: label(data.get(key), key) for key in (
        "subject", "platform", "phrase", "source", "metric", "scope", "window", "geography")}
    result["unit"] = label(data.get("unit") or "unspecified", "unit")
    result["observed_at"] = stamp(data.get("observed_at"))
    if result["observed_at"] > now_stamp(now):
        raise InputError("A captured observation cannot be in the future.")
    kind = data["evidence_kind"]
    if kind not in KINDS:
        raise InputError("Use a documented evidence_kind; suggestions are not demand.")
    result["evidence_kind"] = kind
    availability = data.get("availability", "observed")
    if availability not in {"observed", "unavailable", "error", "suppressed", "no-data"}:
        raise InputError("Unknown monitoring availability state.")
    result["availability"] = availability
    value = data.get("value")
    if value is not None and not isinstance(value, str):
        finite(value, "value")
    if isinstance(value, str):
        if len(value) > 10000:
            raise InputError("Displayed metric text exceeds the bound.")
    if availability != "observed" and value is not None:
        raise InputError("Unavailable measurements must have a null value, never a guessed zero.")
    result["value"] = value
    result["approximate"] = boolean(
        data.get("approximate", kind == "search_volume_estimate"), "approximate")
    result["censored"] = boolean(data.get("censored", False), "censored")
    completeness = data.get("completeness", "unknown")
    if completeness not in {"complete", "partial", "unknown"}:
        raise InputError("completeness must be complete, partial or unknown.")
    result["completeness"] = completeness
    dimensions = data.get("dimensions", {})
    if not isinstance(dimensions, dict) or len(canonical(dimensions)) > 20000:
        raise InputError("dimensions must be a bounded object of source analysis settings.")
    reject_secrets(dimensions)
    result["dimensions"] = dimensions
    result["normalization_id"] = label(data.get("normalization_id"), "normalization_id",
                                       optional=True)
    period_start, period_end = data.get("period_start"), data.get("period_end")
    if (period_start is None) != (period_end is None):
        raise InputError("A reporting period needs both start and end dates.")
    result["period_start"] = result["period_end"] = None
    if period_start is not None:
        start, end = stamp(period_start), stamp(period_end)
        if len(period_start) != 10 or len(period_end) != 10 or start > end:
            raise InputError("Reporting periods require ordered YYYY-MM-DD dates.")
        if end > result["observed_at"]:
            raise InputError("A captured report cannot end after its capture date.")
        result.update(period_start=period_start, period_end=period_end)
    for key in ("notes", "source_url", "capture_file", "capture_sha256", "target_surface"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise InputError(f"{key} must be text or null.")
        result[key] = value
    if result["capture_sha256"] and not re.fullmatch(r"[a-fA-F0-9]{64}",
                                                    result["capture_sha256"]):
        raise InputError("capture_sha256 must be a SHA-256 hex digest.")
    result["identity"] = digest({
        key: normal(result[key]) if isinstance(result[key], str) else result[key]
        for key in IDENTITY_FIELDS
    })
    result["id"] = digest({key: value for key, value in result.items() if key != "id"})
    return result


WATCH_FIELDS = {
    "id", "subject", "platform", "phrase", "source", "metric", "unit", "scope", "window",
    "geography", "evidence_kind", "dimensions", "cadence_hours", "freshness_days",
    "min_absolute_change", "min_percent_change", "collector", "active",
}


def watch(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) - WATCH_FIELDS:
        raise InputError("Watch entries must use the documented watchlist fields.")
    result = {key: label(row.get(key), key) for key in ("id", "subject", "platform")}
    for key in ("phrase", "source", "metric", "unit", "scope", "window", "geography"):
        result[key] = label(row.get(key), key, optional=True)
    result["evidence_kind"] = row.get("evidence_kind")
    if result["evidence_kind"] is not None and result["evidence_kind"] not in KINDS:
        raise InputError("Unknown watch evidence_kind.")
    dimensions = row.get("dimensions", {})
    if not isinstance(dimensions, dict):
        raise InputError("Watch dimensions must be an object.")
    reject_secrets(dimensions)
    result["dimensions"] = dimensions
    for key, default, maximum in (("cadence_hours", 168, 87600),
                                  ("freshness_days", 30, 3650)):
        value = finite(row.get(key, default), key)
        if not 1 <= value <= maximum:
            raise InputError(f"{key} is outside the documented bound.")
        result[key] = value
    for key, default in (("min_absolute_change", 1), ("min_percent_change", None)):
        value = row.get(key, default)
        if value is not None and finite(value, key) < 0:
            raise InputError("Change thresholds cannot be negative.")
        result[key] = value
    result["active"] = boolean(row.get("active", True), "active")
    collector = row.get("collector")
    if collector is not None:
        if not isinstance(collector, dict) or set(collector) != {"plugin", "operation", "options"}:
            raise InputError("A collector needs plugin, operation and options only.")
        label(collector["plugin"], "plugin")
        label(collector["operation"], "operation")
        if not isinstance(collector["options"], dict):
            raise InputError("Collector options must be an object.")
        reject_secrets(collector)
    result["collector"] = collector
    return result


def matches(watched: dict[str, Any], measured: dict[str, Any]) -> bool:
    for key in ("subject", "platform", "phrase", "source", "metric", "unit",
                "scope", "window", "geography", "evidence_kind"):
        expected = watched.get(key)
        if expected is not None and normal(str(expected)) != normal(str(measured.get(key, ""))):
            return False
    return all(measured["dimensions"].get(k) == v
               for k, v in watched["dimensions"].items())
