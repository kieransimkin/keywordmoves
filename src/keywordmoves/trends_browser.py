"""Validate native browser CSV receipts and reuse the source-native Trends parser."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from .builtin.google_trends import GoogleTrendsPlugin, _clean_series_name, _rows
from .errors import ConfigurationError, InputError
from .models import PluginRequest
from .monitoring.validation import stamp
from .online.common import integer, seeds

SCHEMA = "keywordmoves-trends-browser/v1"
SOURCE = "https://trends.google.com/trends/explore"
COUNTRIES = {"GLB": "Worldwide", "GB": "United Kingdom", "US": "United States",
             "CA": "Canada", "AU": "Australia", "IN": "India", "NZ": "New Zealand", "ZA": "South Africa"}
WINDOWS = {"today 12-m": "Past 12 months", "today 3-m": "Past 90 days", "today 5-y": "Past 5 years"}


def scope(seed, country, window, chart):
    if (not isinstance(seed, str) or not seed.strip() or seed != seed.strip()
            or len(seed) > 200 or "," in seed or seed.startswith(("/m/", "/g/"))
            or any(ord(c) < 32 for c in seed)):
        raise ConfigurationError("Trends browser collection needs one plain search term, up to 200 characters.")
    if (not all(isinstance(x, str) for x in (country, window, chart)) or country not in COUNTRIES
            or window not in WINDOWS or chart not in {"interest", "related"}):
        raise ConfigurationError("Unsupported Trends browser country, window or chart.")


def analyse_capture(data):
    comparison = isinstance(data, dict) and data.get("schema") == "keywordmoves-trends-browser/v2"
    allowed = {"schema", "source", "source_url", "page_url", "observed_at", "seed", "country",
               "window", "chart", "controls", "csv"}
    if comparison:
        allowed = allowed - {"seed"} | {"keywords"}
    if (not isinstance(data, dict) or data.get("schema") not in (SCHEMA, "keywordmoves-trends-browser/v2") or set(data) != allowed
            or data.get("source") != "google-trends" or data.get("source_url") != SOURCE):
        raise InputError("Expected a native keywordmoves-trends-browser/v1 receipt.")
    country, window, chart = (data[k] for k in ("country", "window", "chart"))
    words = data.get("keywords") if comparison else [data["seed"]]
    if (not isinstance(words, list) or not 1 <= len(words) <= 5 or comparison and (len(words) < 2 or chart != "interest")
            or not all(isinstance(w, str) for w in words) or len({w.casefold() for w in words}) != len(words)):
        raise InputError("Trends comparison requires 2-5 unique search terms and the interest chart.")
    seed = words[0]
    try:
        for word in words:
            scope(word, country, window, chart)
    except (ConfigurationError, TypeError) as exc:
        raise InputError("Invalid Trends receipt scope.") from exc
    if not isinstance(data["page_url"], str):
        raise InputError("Trends page URL must be text.")
    page = urlsplit(data["page_url"])
    query = parse_qs(page.query, keep_blank_values=True)
    expected = {"q": [",".join(words)], "gprop": ["youtube"], "hl": ["en"]}
    if (page.scheme != "https" or page.netloc != "trends.google.com" or page.path != "/trends/explore"
            or page.fragment or set(query) - {"q", "gprop", "hl", "geo", "date", "cat"}
            or any(query.get(k) != v for k, v in expected.items())
            or query.get("geo", [""]) != ([""] if country == "GLB" else [country])
            or query.get("date", ["today 12-m"]) != [window] or query.get("cat", ["0"]) != ["0"]):
        raise InputError("Trends URL does not match the requested YouTube Search scope.")
    controls = {**({"keywords": words} if comparison else {"seed": seed}), "country": COUNTRIES[country], "window": WINDOWS[window],
                "property": "YouTube Search", "category": "All categories", "term_type": "Search term"}
    if data["controls"] != controls:
        raise InputError("Trends visible controls disagree with the request.")
    observed = stamp(data["observed_at"])
    if not isinstance(data["csv"], str):
        raise InputError("Native Trends CSV must be UTF-8 text.")
    raw = data["csv"].encode("utf-8")
    rows, sha = _rows(None, raw)
    if not rows or rows[0] != ["Category: All categories"]:
        raise InputError("Native Trends export category changed.")
    rows = [r for r in rows if r and any(r)]
    if chart == "interest":
        columns = [f"{w}: ({COUNTRIES[country]})" for w in words]
        if (len(rows) < 3 or rows[1][0] not in {"Week", "Day", "Month"}
                or rows[1][1:] not in [columns, columns + ["IsPartial"]]):
            raise InputError("Native Trends CSV term, country or chart changed.")
        # Duplicate dates must not inflate completed-period means.
        periods = [r[0] for r in rows[2:] if r]
        if len(periods) != len(set(periods)) or periods != sorted(periods):
            raise InputError("Native Trends periods must be unique and chronological.")
    else:
        if (len(rows) < 4 or len(rows[1]) != 1 or not re.fullmatch(
                re.escape(seed) + r": \(\d{2}/\d{2}/\d{4} - \d{2}/\d{2}/\d{4}, "
                + re.escape(COUNTRIES[country]) + r"\)", rows[1][0])
                or not any(r == ["TOP"] for r in rows) or not any(r == ["RISING"] for r in rows)):
            raise InputError("Native related-query CSV term, country or sections changed.")
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", rows[1][0])
        try:
            start, end = (datetime.strptime(v, "%d/%m/%Y").date() for v in dates)
        except ValueError as exc:
            raise InputError("Invalid native related-query reporting dates.") from exc
        span = (end - start).days
        target = {"today 12-m": 365, "today 3-m": 90, "today 5-y": 1826}[window]
        if abs(span - target) > 2 or abs((end - datetime.fromisoformat(observed).date()).days) > 1:
            raise InputError("Native related-query reporting window disagrees with the capture.")
    options = {"observed_at": observed, "geography": country, "window": window,
               "scope": "native YouTube Search export; " + ("matched comparison" if comparison else "one search term") + "; all categories",
               "category": "0", "search_property": "youtube", "seed_keyword": seed,
               "normalization_id": sha, "completed_periods_only": chart == "interest"}
    parsed = GoogleTrendsPlugin().import_csv(
        PluginRequest("import-interest" if chart == "interest" else "import-related", options=options),
        captured_bytes=raw)
    if chart == "interest" and [_clean_series_name(k.phrase).casefold() for k in parsed.keywords] != [w.casefold() for w in words]:
        raise InputError("Unexpected Trends export series.")
    return replace(parsed, operation="collect-browser", keywords=tuple(replace(k, score=None)
                   for k in parsed.keywords), notes=(
                       "Downloaded through the native CSV control and analysed in memory; no manual import.",
                       "Relative sampled YouTube Search interest, not absolute keyword search volume.",
                       *parsed.notes), metadata={**parsed.metadata, "access": "native-browser-csv",
                       "source_url": SOURCE, "page_url": data["page_url"], "requested_seed": seed,
                       "chart": chart, "controls": controls, "manual_import_required": False,
                       "comparison_keywords": words, "within_export_comparable": comparison,
                       "network_requests": None, "capture_origin": "caller-supplied; scope validation is not browser attestation"})


def collect_browser(request, context):
    if request.inputs or set(request.options) - {"country", "window", "chart", "maxWaitSeconds"}:
        raise ConfigurationError("collect-browser needs a seed and browser driver, not input files.")
    words = seeds(request, maximum=5)
    seed = words[0]
    country, window, chart = (request.options.get(k, v) for k, v in
                              (("country", "GLB"), ("window", "today 12-m"), ("chart", "interest")))
    for word in words:
        scope(word, country, window, chart)
    if len(words) > 1 and chart != "interest":
        raise ConfigurationError("Multiple search terms require the interest chart.")
    integer(request.options, "maxWaitSeconds", 45, 5, 120)
    if context.public_browser is None:
        raise ConfigurationError("Trends collection needs an authorised public_browser driver using browser/google_trends.mjs.")
    data = context.public_browser.collect("google-trends", request)
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in
                                        ((("seed", seed),) if len(words) == 1 else (("keywords", words),))
                                        + (("country", country), ("window", window), ("chart", chart))):
        raise InputError("Trends driver returned a different requested scope.")
    return analyse_capture(data)
