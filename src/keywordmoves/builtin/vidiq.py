"""Offline imports of authorised vidIQ keyword captures, never hidden endpoints."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginDescriptor, PluginResult
from ..monitoring.validation import (
    canonical,
    finite,
    label,
    normal,
    read_json,
    reject_secrets,
    stamp,
)

METRICS = {
    "estimatedMonthlySearch": ("estimated_monthly_searches", "searches_per_30_days"),
    "countryVolume": ("country_estimated_monthly_searches", "searches_per_30_days"),
    "volume": ("volume_index", "provider_index_0_100"),
    "competition": ("competition_index", "provider_index_0_100"),
    "overall": ("overall_index", "provider_index_0_100"),
    "baselineMonthlySearches30d": ("baseline_monthly_searches_30d", "searches_per_30_days"),
    "searchDemandGrowthPct": ("search_demand_growth", "percent_growth"),
    "relatedScore": ("related_score", "provider_score"),
}
MODES = {"research", "questions", "matching_terms", "country_top", "country_search", "rising"}


def _payload(data):
    if not isinstance(data, dict):
        raise InputError("vidIQ capture must be a JSON object.")
    if data.get("isError") is True:
        raise InputError("A failed vidIQ call cannot establish keyword evidence.")
    if "structuredContent" in data:
        return _payload(data["structuredContent"])
    if "content" in data:
        if not isinstance(data["content"], list):
            raise InputError("MCP content must be an array.")
        matches = []
        for block in data["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block.get("text", "")
                if "Keyword data (JSON):\n" in raw:
                    raw = raw.split("Keyword data (JSON):\n", 1)[1]
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict) and value.get("mode") in MODES:
                    matches.append(value)
        if len(matches) != 1:
            raise InputError("Expected one unambiguous vidIQ keyword JSON result.")
        return matches[0]
    return data


class VidiqPlugin:
    descriptor = PluginDescriptor("vidiq", "Import authorised vidIQ keyword JSON with dates, censoring and country metrics intact.",
                                  ("import", "youtube", "provider-estimates", "public-html"), ("import-json", "import-public-html", "collect-public"))

    def __init__(self, *, transport=None):
        self._transport = transport

    def run(self, request, context):
        del context
        if request.operation == "collect-public":
            from ..online.free_public import collect_vidiq
            return collect_vidiq(request, self._transport)
        if request.operation == "import-public-html":
            from .vidiq_public import import_public_html
            return import_public_html(request)
        if request.operation != "import-json" or len(request.inputs) != 1 or request.keywords:
            raise ConfigurationError("vidiq import-json requires exactly one saved JSON capture.")
        options = dict(request.options)
        if set(options) - {"observed_at", "subject", "seed", "country", "max_age_days"}:
            raise ConfigurationError("Unknown vidIQ import option.")
        path = request.inputs[0]
        raw, data = read_json(path)
        reject_secrets(data)
        envelope = data if isinstance(data, dict) and data.get("schema") == "keywordmoves-vidiq-capture/v1" else {}
        captured = stamp(options.get("observed_at", envelope.get("observed_at")))
        original_request = envelope.get("request", {})
        if not isinstance(original_request, dict):
            raise InputError("Capture request must be an object.")
        payload = _payload(envelope.get("result", data))
        canonical(payload)
        reject_secrets(payload)
        mode = payload.get("mode")
        if mode not in MODES:
            raise InputError("Unknown or missing vidIQ keyword mode.")
        if original_request.get("mode", mode) != mode:
            raise InputError("Requested and returned vidIQ modes conflict.")
        if envelope.get("completeness", "unknown") not in {"complete", "partial", "unknown"}:
            raise InputError("Capture completeness must be complete, partial or unknown.")
        age = options.get("max_age_days", 30)
        if isinstance(age, bool) or not isinstance(age, int) or not 1 <= age <= 366:
            raise ConfigurationError("max_age_days must be an integer from 1 to 366.")
        country = options.get("country", original_request.get("country", payload.get("country")))
        if country is not None:
            if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
                raise InputError("country must be a declared ISO alpha-2 code.")
            country = country.upper()
        returned_country = payload.get("country")
        if returned_country is not None and (not isinstance(returned_country, str) or returned_country.upper() != country):
            raise InputError("Requested and returned vidIQ countries conflict.")
        seed = options.get("seed", original_request.get("keyword"))
        seed_row = payload.get("seedKeyword")
        if seed is None and isinstance(seed_row, dict):
            seed = seed_row.get("keyword")
        if seed is not None:
            seed = label(seed, "seed")
            if isinstance(seed_row, dict) and normal(label(seed_row.get("keyword"), "seed keyword")) != normal(seed):
                raise InputError("Requested and returned vidIQ seed keywords conflict.")
        subject = label(options.get("subject", seed or "vidIQ keyword research"), "subject")
        rows = []
        if seed_row is not None:
            rows.append(("seed", seed_row))
        for key in ("relatedKeywords", "questions", "matchingTerms", "risingKeywords"):
            values = payload.get(key, [])
            if not isinstance(values, list):
                raise InputError("vidIQ keyword collections must be arrays.")
            rows.extend((key, row) for row in values)
        if len(rows) > 5000:
            raise InputError("vidIQ capture exceeds 5000 keyword rows; no truncation.")
        out = []
        for index, (collection, row) in enumerate(rows):
            if not isinstance(row, dict):
                raise InputError("vidIQ keyword rows must be objects.")
            phrase = label(row.get("keyword", row.get("question")), "keyword")
            metrics_date = stamp(row["metricsAsOf"]) if row.get("metricsAsOf") is not None else None
            age_days = (datetime.fromisoformat(captured) - datetime.fromisoformat(metrics_date)).total_seconds() / 86400 if metrics_date else None
            display = row.get("monthlySearchesDisplay")
            censored = isinstance(display, str) and bool(re.fullmatch(r"\s*[<>]\s*[\d,]+(?:\.\d+)?\s*", display))
            evidence = []
            for field, (metric, unit) in METRICS.items():
                value = row.get(field)
                if value is not None:
                    value = finite(value, field)
                    if unit == "provider_index_0_100" and not 0 <= value <= 100:
                        raise InputError("vidIQ indices must remain in their native 0-100 range.")
                    if field != "searchDemandGrowthPct" and value < 0:
                        raise InputError("vidIQ counts and scores cannot be negative.")
                if field == "estimatedMonthlySearch" and censored:
                    value = display.strip()
                if field == "countryVolume" and value is not None and country is None:
                    raise InputError("countryVolume requires a known requested country.")
                geography = country if field == "countryVolume" else "worldwide"
                evidence.append(KeywordEvidence("vidIQ", metric, value, unit, captured, geography,
                    "Third-party YouTube estimate/index; capture date is separate from provider metricsAsOf."))
            metadata = {"platform": "YouTube", "subject": subject, "seed": seed, "mode": mode,
                "source_collection": collection, "provider_order": index + 1,
                "metrics_as_of": metrics_date, "metrics_age_days": age_days,
                "freshness": "unknown" if age_days is None else "future" if age_days < 0 else "stale" if age_days > age else "within_threshold",
                "monthly_searches_display": display, "raw_estimated_monthly_searches": row.get("estimatedMonthlySearch"),
                "estimated_monthly_searches_censored": censored,
                "competition_label": row.get("competitionLabel"), "volume_label": row.get("volumeScoreLabel"),
                "country": country, "top_markets": row.get("topMarkets", []),
                "window": "provider rolling 30-day estimate; exact period unavailable",
                "evidence_kind": "search_volume_estimate", "approximate": True,
                "capture_route": envelope.get("capture_route", "supplied-json"),
                "capture_reference": envelope.get("capture_reference"), "capture_sha256": envelope.get("capture_sha256"),
                "completeness": envelope.get("completeness", "unknown")}
            out.append(KeywordCandidate(phrase, collection, None, tuple(evidence), metadata))
        return PluginResult(self.descriptor.name, request.operation, tuple(out),
            ("Offline import only: no account changes, requests, charges or hidden-endpoint scraping.",
             "Country estimates, worldwide estimates, proprietary indices and growth retain separate metrics.",
             "Censored displays override raw zero placeholders; unknown dates stay unknown. No universal score.",),
            {"input_path": str(path), "input_sha256": hashlib.sha256(raw).hexdigest(), "mode": mode,
             "seed": seed, "subject": subject, "country": country, "observed_at": captured, "network_requests": 0,
             "row_count": len(out), "provider_notes": payload.get("notes", [])})
