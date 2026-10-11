"""Google planning context from KE's free tool, excluding unverified suggestions."""
from __future__ import annotations

import re

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginResult
from ..monitoring.validation import finite, label, normal
from .reviewed_html import one, visible_document

URL = "https://keywordseverywhere.com/tools/youtube-keyword-tool/"
TREND = "Last month's search volume vs the average of the prior 11 months"
CURRENCIES = {"USD": "$", "CAD": "$", "AUD": "$", "NZD": "$", "GBP": "£", "EUR": "€", "INR": "₹", "ZAR": "R"}


def number(display, *, percent=False):
    if display in {"-", "—", ""}:
        return None
    value = display[:-1] if percent and display.endswith("%") else display
    pattern = r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?" if percent else r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?"
    if not re.fullmatch(pattern, value) or (percent and not display.endswith("%")):
        raise InputError("Unrecognised Google metric display; no guessed conversion.")
    return finite(float(value.replace(",", "")), "Google metric")


def import_public_html(request, *, captured_bytes=None):
    if request.keywords:
        raise ConfigurationError("Keywords Everywhere's public import uses the captured rows, not a live seed request.")
    doc, observed, metadata = visible_document(request, URL, {"source_url", "observed_at", "country", "currency"}, captured_bytes=captured_bytes)
    country, currency = request.options.get("country"), request.options.get("currency")
    if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}|GLB", country):
        raise ConfigurationError("Confirm the reviewed Google volume country, or GLB for Global.")
    if not isinstance(currency, str) or currency not in CURRENCIES:
        raise ConfigurationError("Confirm the displayed CPC currency using a supported ISO currency code.")
    # Read only the visible Google-labelled table. Do not infer monthly values from SVG charts.
    if "Monthly search volume (Google data) for these YouTube keywords" not in doc.get_text(" ", strip=True):
        raise InputError("Expected the explicit Google-volume label; no YouTube volume substitution.")
    table = one(doc, "table.kt-results-table")
    headings = table.select("thead th")
    if [h.get("data-sort") for h in headings[:5]] != ["keyword", "volume", "cpc", "competition", "trend_pct"] or len(headings) != 6:
        raise InputError("Public metric columns changed; re-review the source.")
    if headings[4].get("title") != TREND:
        raise InputError("Google trend comparison window changed or is unspecified.")
    rows = one(table, "#kt-results-body").find_all("tr", recursive=False)
    if len(rows) > 10000:
        raise InputError("Public result table exceeds 10000 rows.")
    items, seen, skipped = [], set(), 0
    for order, row in enumerate(rows, 1):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 6:
            raise InputError("Incomplete public result row.")
        phrase = label(cells[0].get_text(" ", strip=True), "planning phrase")
        identity = normal(phrase)
        if identity in seen:
            raise InputError("Duplicate planning phrases in this captured table.")
        seen.add(identity)
        displays = [c.get_text(" ", strip=True) for c in cells[1:5]]
        volume = number(displays[0])
        cpc_display = displays[1]
        if cpc_display in {"-", "—", ""}:
            cpc = None
        elif cpc_display.startswith(CURRENCIES[currency]):
            cpc = number(cpc_display[len(CURRENCIES[currency]):])
        else:
            raise InputError("Displayed CPC currency disagrees with the reviewed currency.")
        competition, trend = number(displays[2]), number(displays[3], percent=True)
        if competition is not None and not 0 <= competition <= 1:
            raise InputError("Google paid competition must be in 0..1.")
        if volume is not None and not volume.is_integer():
            raise InputError("Google monthly estimate display must be an integer.")
        if all(v is None for v in (volume, cpc, competition, trend)):
            skipped += 1
            continue
        metrics = (
            ("google_estimated_search_volume", volume, "searches_per_month"),
            ("google_ads_cpc", cpc, currency),
            ("google_paid_competition", competition, "index_0_1"),
            ("google_search_growth_display", trend, "percent_growth"),
        )
        items.append(KeywordCandidate(phrase, "google-planning-context", None, tuple(
            KeywordEvidence("Keywords Everywhere free public tool / Google planning data", metric, value,
                unit, observed, country, "Provider's Google display; not YouTube searches or organic ranking difficulty.")
            for metric, value, unit in metrics), {
                "platform": "Google", "evidence_kind": "search_volume_estimate", "source_url": URL,
                "capture_sha256": metadata["input_sha256"], "raw_displays": displays, "displayed_order": order,
                "geography": country, "currency": currency, "scope_basis": "caller reviewed visible country and currency",
                "window": "average monthly Google searches over the past 12 months; exact calendar dates unspecified",
                "google_trend_window": TREND, "provider_updated_at": None, "approximate": True,
                "completeness": "partial", "metric_platform": "Google",
                "phrase_origin": "provider-claimed YouTube autocomplete; not independently verified",
            }))
    metadata.update(row_count=len(items), displayed_rows=len(rows), unmeasured_rows_skipped=skipped,
        platform="Google", country=country, currency=currency, completeness="partial",
        youtube_search_volume=False)
    return PluginResult("keywords-everywhere", "import-public-html", tuple(items), (
        "Free reviewed HTML import; no API key, HTTP request or paid call.",
        "Every metric is Google planning context, including paid-ad competition; no metric becomes YouTube volume.",
        "Unmeasured phrase rows are excluded; autocomplete claims need independent review before being treated as observed searches.",
        "Capture time and provider measurement time remain separate. Missing values remain null, and displayed growth may be rounded.",
    ), metadata)
