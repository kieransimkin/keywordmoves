"""Direct handoff from supported browser collectors to the existing public parsers.

The bundled JS helper operates a caller-provided browser tab. This module reads
its bounded envelope through stdin, without manual HTML files or temporary captures.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from .errors import ConfigurationError, InputError, KeywordMovesError
from .models import PluginRequest
from .monitoring.validation import label

SCHEMA = "keywordmoves-public-browser/v1"
SOURCES = {
    "ytkeywordtool": "https://ytkeywordtool.com/",
    "keywords-everywhere": "https://keywordseverywhere.com/tools/youtube-keyword-tool/",
}


def analyse_capture(data):
    if isinstance(data, dict) and data.get("schema") == "keywordmoves-trends-browser/v1":
        from .trends_browser import analyse_capture as analyse_trends
        return analyse_trends(data)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise InputError("Expected keywordmoves-public-browser/v1.")
    if set(data) - {"schema", "source", "source_url", "page_url", "observed_at", "seed", "effective_seed", "country", "currency", "html", "collection"}:
        raise InputError("Unknown browser-capture fields.")
    source = data.get("source")
    if not isinstance(source, str) or source not in SOURCES or data.get("source_url") != SOURCES[source]:
        raise InputError("Unexpected browser source or URL; no arbitrary-page extraction.")
    page_url = data.get("page_url", SOURCES[source])
    if not isinstance(page_url, str):
        raise InputError("Browser page URL must be text.")
    if page_url != SOURCES[source]:
        import re
        parsed, base = urlsplit(page_url), urlsplit(SOURCES[source])
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (source != "keywords-everywhere" or parsed.scheme != base.scheme or parsed.netloc != base.netloc
            or parsed.path != base.path or parsed.fragment or set(query) != {"job"} or len(query["job"]) != 1
            or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", query["job"][0])):
            raise InputError("Unexpected browser navigation; no arbitrary-page extraction.")
    seed = label(data.get("seed"), "browser seed")
    body = data.get("html")
    if not isinstance(body, str) or len(body.encode("utf-8")) > 5_000_000:
        raise InputError("Browser HTML must be bounded UTF-8 text.")
    collection = data.get("collection")
    if not isinstance(collection, dict) or set(collection) - {"stopped_early", "displayed_country", "displayed_total"}:
        raise InputError("Unexpected browser collection metadata.")
    if not isinstance(collection.get("stopped_early"), bool):
        raise InputError("Browser collection needs a stopped_early state.")
    total = collection.get("displayed_total")
    if isinstance(total, bool) or not isinstance(total, int) or not 0 <= total <= 10000:
        raise InputError("Browser displayed total must be bounded.")
    options = {"source_url": SOURCES[source], "observed_at": data.get("observed_at")}
    if source == "ytkeywordtool":
        from .builtin.ytkeywordtool import YTKeywordToolPlugin
        effective = label(data.get("effective_seed", seed), "provider displayed seed")
        parsed = YTKeywordToolPlugin().import_html(PluginRequest("import-public-html", (effective,), options=options), captured_bytes=body.encode("utf-8"))
        if parsed.metadata["reported_keyword_count"] != total:
            raise InputError("Browser collection count disagrees with the visible result.")
    else:
        from .builtin.keywords_everywhere_public import import_public_html
        country, currency = data.get("country"), data.get("currency")
        expected = "" if country == "GLB" else "uk" if country == "GB" else str(country).lower()
        if collection.get("displayed_country") != expected:
            raise InputError("The selected browser country disagrees with the requested scope.")
        options.update(country=country, currency=currency)
        parsed = import_public_html(PluginRequest("import-public-html", options=options), captured_bytes=body.encode("utf-8"))
        if parsed.metadata["displayed_rows"] > total:
            raise InputError("Browser table rows exceed its displayed total.")
    return replace(parsed, operation="collect-browser", keywords=tuple(replace(k, metadata={
        **k.metadata, "requested_seed": seed, "scope_basis": "browser form country selection and currency validation" if source == "keywords-everywhere" else "provider has no declared geographic control",
        "seed_basis": "collector checked the submitted browser form value",
    }) for k in parsed.keywords), notes=(
        "Automatically collected through the source's normal browser form and analysed in memory; no manual HTML import.",
        "Browser/provider request counts are unknown; bounded harvest time and visible rows are not a shared daily quota ledger.",
        *tuple(n for n in parsed.notes if "no API key, HTTP request" not in n),
    ), metadata={**parsed.metadata, "access": "public-browser-ui", "network_requests": None,
        "requested_seed": seed, "collection": collection, "manual_html_required": False,
        **({"effective_seed": effective} if source == "ytkeywordtool" else {}),
        "page_url": page_url,
        "capture_origin": "collector-supplied; parser validates scope and layout, not browser authenticity"})


def collect_browser(source, request, context):
    from .online.common import integer, seeds
    expected_seed = seeds(request)[0]
    allowed = {"maxWaitSeconds", "maxHarvestSeconds", "maxRows"}
    if source == "keywords-everywhere":
        allowed |= {"country", "currency"}
    if request.inputs or set(request.options) - allowed:
        raise ConfigurationError("collect-browser takes one seed and a supported browser driver, not input files.")
    integer(request.options, "maxWaitSeconds", 45, 5, 120)
    integer(request.options, "maxHarvestSeconds", 20, 5, 60)
    integer(request.options, "maxRows", 30, 1, 200)
    country, currency = request.options.get("country", "GLB"), request.options.get("currency", "USD")
    if source == "keywords-everywhere":
        pairs = {"GLB": "USD", "US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "IN": "INR", "NZ": "NZD", "ZA": "ZAR"}
        if not isinstance(country, str) or pairs.get(country) != currency:
            raise ConfigurationError("Use the supported public browser country/currency pair.")
    if context.public_browser is None:
        raise ConfigurationError("collect-browser needs a caller-provided public_browser driver. Use the bundled browser/free_public.mjs helper with an authorised connected tab; no manual HTML import is needed.")
    data = context.public_browser.collect(source, request)
    if not isinstance(data, dict) or data.get("source") != source or expected_seed != data.get("seed"):
        raise InputError("Browser driver returned the wrong source or seed.")
    if source == "keywords-everywhere" and (data.get("country") != country or data.get("currency") != currency):
        raise InputError("Browser driver returned the wrong requested scope.")
    return analyse_capture(data)


def main():
    try:
        raw = sys.stdin.buffer.read(12_000_001)
        if len(raw) > 12_000_000:
            raise InputError("Browser envelope exceeds 12 MB.")
        from .online.youtube_imports import decode_json
        result = analyse_capture(decode_json(raw.decode("utf-8-sig")))
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0
    except (KeywordMovesError, UnicodeError) as exc:
        print(json.dumps({"error_kind": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
