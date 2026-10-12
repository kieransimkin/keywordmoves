"""Low-rate public routes used by free web forms; no private API or browser bypass."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlencode

from ..builtin.ytkeywordtool import URL as YT_URL
from ..errors import ConfigurationError
from ..models import KeywordCandidate, KeywordEvidence, PluginRequest, PluginResult
from ..monitoring.validation import label, normal
from .common import HTTP, OnlineSourceError, array, integer, number, obj, seeds
from .websites import check_robots

NETWORK_OPTIONS = {"max_requests", "max_response_bytes", "timeout", "min_interval", "limit"}
YT_ENDPOINT = YT_URL + "api/keywords"


def _options(request, extra=()):
    if request.inputs or set(request.options) - NETWORK_OPTIONS - set(extra):
        raise ConfigurationError("collect-public accepts seeds and documented bounded network options, not files or credentials.")
    options = {"max_requests": 2, **request.options}
    # Fail before network I/O if the robots + query budget cannot be met.
    integer(options, "max_requests", 2, 2, 20)
    integer(options, "limit", 200, 1, 1000)
    return options


def collect_ytkeywordtool(request, transport=None):
    options = _options(request)
    requested = seeds(request)[0]
    timestamp = datetime.now(timezone.utc).isoformat()
    url = YT_ENDPOINT + "?" + urlencode({"q": requested})
    with HTTP(options, ("ytkeywordtool.com",), transport, interval=10) as http:
        check_robots(http, url)
        _, raw = http.request("GET", url)
        count = http.requests
    from ..online.youtube_imports import decode_json
    data = obj(decode_json(raw))
    if data.get("error"):
        raise OnlineSourceError("YT Keyword Tool reported an unavailable query; no retry or fallback was made.")
    if data.get("input") != requested:
        raise OnlineSourceError("The public response does not echo the requested input; stale or unrelated data rejected.")
    effective = label(data.get("seed"), "provider effective seed")
    rows = array(data.get("keywords"))
    total = data.get("count")
    if isinstance(total, bool) or not isinstance(total, int) or not 0 <= total <= 10000 or total != len(rows):
        raise OnlineSourceError("The public response count/schema changed; no guessed extraction.")
    items, seen = [], set()
    raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    for order, row in enumerate(rows, 1):
        row = obj(row)
        word = label(row.get("term"), "provider suggestion")
        tier, category = row.get("tier"), row.get("category")
        if tier not in {"High", "Medium", "Low"} or category not in {"Related", "Question", "Comparison", "Modifier"}:
            raise OnlineSourceError("The public suggestion tier/category changed.")
        if normal(word) in seen:
            raise OnlineSourceError("Duplicate suggestions in a public response.")
        seen.add(normal(word))
        score = number(row.get("score"))
        if score is not None and score < 0:
            raise OnlineSourceError("The provider returned a negative batch score.")
        items.append(KeywordCandidate(word, "provider-language-suggestion", None, (
            KeywordEvidence("YT Keyword Tool", "provider_autocomplete_tier", tier, "within_batch_label", timestamp, None,
                "Provider-reported within-batch ranking; not monthly volume or calibrated demand."),
            KeywordEvidence("YT Keyword Tool", "displayed_order", order, "ordinal", timestamp, None,
                "Provider result ordering, not YouTube result rank."),
        ), {"platform": "YouTube", "evidence_kind": "language_suggestion", "category": category,
            "seed": effective, "requested_seed": requested, "seed_basis": "public response input echo and effective seed",
            "source_url": YT_URL, "endpoint_url": YT_ENDPOINT, "capture_sha256": raw_hash,
            "geography": None, "provider_updated_at": None, "provider_batch_score": score,
            "scope": "returned provider batch", "window": "retrieval only", "completeness": "provider-limited",
            "approximate": False}))
    limit = integer(options, "limit", 200, 1, 1000)
    return PluginResult("ytkeywordtool", request.operation, tuple(items[:limit]), (
        "Free public form endpoint found in the site's linked browser application; not an official developer API.",
        "Robots checked first; low-rate requests, no redirects, automatic retries, cookies, credentials or paid calls.",
        "Suggestion tiers and native batch scores are provider claims, not absolute search volume or country demand.",
    ), {"access": "public-form-json", "retrieved_at": timestamp, "observed_at": timestamp,
        "provider_updated_at": None, "source_url": YT_URL, "endpoint_url": YT_ENDPOINT,
        "response_sha256": raw_hash, "network_requests": count, "seed": effective, "requested_seed": requested,
        "geography": None, "rows_received": total, "row_count": min(total, limit),
        "output_truncated": total > limit, "completeness": "provider-limited", "paid_calls": 0})


def collect_vidiq(request, transport=None):
    from ..builtin.vidiq_public import URL, import_public_html
    options = _options(request, ("max_age_days",))
    if request.keywords:
        raise ConfigurationError("The public vidIQ feed has featured rows, not arbitrary seed queries.")
    age = integer(options, "max_age_days", 30, 1, 366)
    timestamp = datetime.now(timezone.utc).isoformat()
    with HTTP(options, ("vidiq.com",), transport, interval=10) as http:
        check_robots(http, URL)
        _, body = http.request("GET", URL)
        count = http.requests
    parsed = import_public_html(PluginRequest("import-public-html", options={
        "source_url": URL, "observed_at": timestamp, "max_age_days": age}), captured_bytes=body.encode("utf-8"))
    limit = integer(options, "limit", 200, 1, 1000)
    return replace(parsed, operation=request.operation, keywords=parsed.keywords[:limit],
        notes=("Automatically fetched the fixed public page after its robots policy allowed it; no account API, retries or bypass.", *parsed.notes[1:]),
        metadata={**parsed.metadata, "access": "public-html-http", "network_requests": count,
            "retrieved_at": timestamp, "output_truncated": len(parsed.keywords) > limit, "paid_calls": 0})
