"""Offline imports of authorised Keyword Tool YouTube JSON; no credential or HTTP access."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginResult
from ..monitoring.validation import finite, label, normal, read_json, stamp

OPTIONS = {"platform", "country", "observed_at", "currency", "seed_keyword", "subject"}


def _number(value, field, *, maximum=None):
    if value is None:
        return None
    value = finite(value, field)
    if value < 0 or (maximum is not None and value > maximum):
        raise InputError(f"{field} is outside its documented range.")
    return value


def _monthly(row, captured):
    points, seen = [], set()
    fields = {k: v for k, v in row.items() if re.fullmatch(r"m\d+(?:_month|_year)?", k)}
    for key in fields:
        slot = int(re.match(r"m(\d+)", key)[1])
        if not 1 <= slot <= 48 or not key.startswith(f"m{slot}"):
            raise InputError("Monthly history slots must be m1..m48 without leading zeros.")
    slots = sorted({int(re.match(r"m(\d+)", k)[1]) for k in fields})
    for slot in slots:
        value = _number(row.get(f"m{slot}"), "monthly volume")
        month, year = row.get(f"m{slot}_month"), row.get(f"m{slot}_year")
        if month is None and year is None:
            if value is not None:
                raise InputError("Monthly volumes need their documented month and year.")
            points.append({"slot": slot, "period": None, "value": None, "completed": None})
            continue
        if (isinstance(month, bool) or not isinstance(month, int) or
                isinstance(year, bool) or not isinstance(year, int)):
            raise InputError("Monthly dates require integer month and year pairs.")
        try:
            start = date(year, month, 1)
        except ValueError as exc:
            raise InputError("Invalid monthly history date.") from exc
        period = start.isoformat()[:7]
        if period in seen:
            raise InputError("Monthly history cannot repeat a reporting month.")
        seen.add(period)
        points.append({"slot": slot, "period": period, "value": value,
                       "completed": (start.year, start.month) < (captured.year, captured.month)})
    return fields, points


def import_youtube(request):
    if request.keywords or len(request.inputs) != 1 or set(request.options) - OPTIONS:
        raise ConfigurationError("Keyword Tool import-json requires one JSON, no keywords and documented offline options.")
    path = Path(request.inputs[0])
    raw, data = read_json(path)
    if not isinstance(data, dict):
        raise InputError("Keyword Tool JSON must be an object.")
    options = dict(request.options)
    if "schema" in data:
        if data["schema"] != "keywordmoves-keywordtool-capture/v1":
            raise InputError("Unknown Keyword Tool capture schema.")
        if not isinstance(data.get("request"), dict) or not isinstance(data.get("result"), dict):
            raise InputError("Capture requires request context and a result object.")
        recorded = data["request"]
        if set(recorded) - {"platform", "country", "currency", "seed_keyword"}:
            raise InputError("Capture request uses unsupported fields; remove credentials.")
        for key, value in {**recorded, "observed_at": data.get("observed_at")}.items():
            if key in options and normal(str(options[key])) != normal(str(value)):
                raise InputError("Import options conflict with recorded capture context.")
            options[key] = value
        data = data["result"]
    if options.get("platform") != "youtube":
        raise ConfigurationError("Offline Keyword Tool imports require explicit platform=youtube.")
    country = label(options.get("country"), "country").upper()
    if not re.fullmatch(r"[A-Z]{2}|GLB", country):
        raise ConfigurationError("YouTube country must be an ISO two-letter code or GLB for worldwide.")
    observed = stamp(options.get("observed_at"))
    captured = date.fromisoformat(observed[:10])
    currency = label(options.get("currency", "USD"), "currency").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ConfigurationError("Currency must be a three-letter code.")
    for field in ("subject", "seed_keyword"):
        if field in options:
            options[field] = label(options[field], field)
    results = data.get("results")
    if not isinstance(results, dict) or not 1 <= len(results) <= 10000:
        raise InputError("Expected 1..10000 Keyword Tool result objects; empty responses are unavailable.")
    if data.get("error") or data.get("error_code"):
        raise InputError("Provider errors are unavailable evidence; do not import them as metrics.")
    notice = data.get("notice")
    geography = "worldwide" if country == "GLB" else country
    sha = hashlib.sha256(raw).hexdigest()
    items, seen = [], set()
    for ordinal, (key, row) in enumerate(results.items(), 1):
        if not isinstance(row, dict):
            raise InputError("Each Keyword Tool result must be an object.")
        source_phrase = label(row.get("string", key), "keyword")
        phrase = normal(source_phrase)
        if phrase in seen:
            raise InputError("Duplicate normalised Keyword Tool phrases are ambiguous.")
        seen.add(phrase)
        monthly_raw, monthly = _monthly(row, captured)
        volume = _number(row.get("volume"), "volume")
        cpc = _number(row.get("cpc"), "cpc")
        competition = _number(row.get("cmp"), "cmp", maximum=1)
        trend = None if row.get("trend") is None else finite(row["trend"], "trend")
        evidence = tuple(KeywordEvidence(
            "Keyword Tool YouTube JSON", metric, value, unit, observed, geography, note)
            for metric, value, unit, note in (
                ("estimated_search_volume", volume, "searches_per_month",
                 "Provider estimate: average monthly YouTube searches over the last 12 months; not a 30-day total."),
                ("google_ads_cpc_proxy", cpc, currency,
                 "Google Ads advertising proxy; not YouTube organic difficulty."),
                ("google_ads_competition_proxy", competition, "index_0_1",
                 "Google Ads advertising proxy; not YouTube organic competition."),
                ("provider_volume_trend", trend, "fractional_change",
                 "Provider trend retains its native fraction; capture time is not a metric refresh date."),
            ))
        items.append(KeywordCandidate(phrase, "provider-keyword", None, evidence, {
            "platform": "YouTube", "source_phrase": source_phrase, "source_sha256": sha,
            "country": country, "scope": "YouTube keyword searches", "geography": geography,
            "window": "average monthly over last 12 months", "evidence_kind": "search_volume_estimate",
            "observed_at": observed, "metrics_as_of": None, "freshness": "unknown",
            "approximate": True, "availability": "observed" if volume is not None else "unavailable",
            "completeness": "partial" if notice or any(v is None for v in (volume, cpc, competition, trend)) else "complete",
            "metric_availability": {"volume": volume is not None, "cpc": cpc is not None,
                                    "cmp": competition is not None, "trend": trend is not None},
            "returned_order": ordinal, "monthly_metrics": monthly_raw, "monthly_series": monthly,
            **{k: options[k] for k in ("subject", "seed_keyword") if k in options},
        }))
    return PluginResult("keywordtool", "import-json", tuple(items), (
        "Offline authorised data only; no account, API key, network request or billing.",
        "Provider estimates remain separate from vidIQ's 30-day estimates and Trends' relative indices.",
        "Capture date is not provider freshness; CPC/competition are Google Ads proxies.",
    ), {"source_file": str(path.resolve()), "source_sha256": sha, "network_requests": 0,
        "platform": "YouTube", "country": country, "geography": geography,
        "observed_at": observed, "partial_notice": bool(notice), "total_keywords": data.get("total_keywords"),
        "window": "average monthly over last 12 months"})
