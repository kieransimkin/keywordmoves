"""Reviewable public vidIQ HTML imports; no hidden endpoints or live requests."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginResult
from ..monitoring.validation import finite, label, normal, stamp
from ..online.youtube_imports import soup

URL = "https://vidiq.com/features/keyword-tools/"
HEADING = "The most searched keywords on YouTube right now"
MONTHS = {name: i for i, name in enumerate(("January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"), 1)}
OPTIONS = {"observed_at", "source_url", "max_age_days"}


def _display(value, growth=False):
    pattern = r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?%" if growth else r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?[KkMm]?"
    if not re.fullmatch(pattern, value):
        raise InputError("Unexpected public metric display; no guessed conversion.")
    if growth:
        return finite(float(value[:-1].replace(",", "")), "public growth"), None
    scale = {"k": 1000, "m": 1000000}.get(value[-1:].lower(), 1)
    nominal = float((value[:-1] if scale != 1 else value).replace(",", "")) * scale
    finite(nominal, "public volume")
    # Abbreviated counts are retained as displays, never promoted to precise counts.
    return value if scale != 1 else nominal, nominal


def import_public_html(request, *, captured_bytes=None):
    if request.keywords or len(request.inputs) != (1 if captured_bytes is None else 0) or set(request.options) - OPTIONS:
        raise ConfigurationError("vidiq import-public-html requires one saved public widget/page and documented offline options.")
    observed = stamp(request.options.get("observed_at"))
    source_url = request.options.get("source_url")
    if source_url != URL:
        raise ConfigurationError("Confirm the exact official public keyword-tools source_url.")
    age = request.options.get("max_age_days", 30)
    if isinstance(age, bool) or not isinstance(age, int) or not 1 <= age <= 366:
        raise ConfigurationError("max_age_days must be an integer from 1 to 366.")
    path = Path(request.inputs[0]) if captured_bytes is None else None
    try:
        if path is not None:
            if path.stat().st_size > 5_000_000:
                raise InputError("Public HTML import exceeds 5 MB.")
            raw = path.read_bytes()
        else:
            raw = captured_bytes
        if not isinstance(raw, bytes):
            raise InputError("Automatic captures must supply UTF-8 bytes.")
        if len(raw) > 5_000_000:
            raise InputError("Public HTML import exceeds 5 MB.")
        body = raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InputError("Public HTML must be readable UTF-8.") from exc
    doc = soup(body)
    headings = [h for h in doc.find_all("h2") if h.get_text(" ", strip=True) == HEADING]
    if len(headings) != 1 or headings[0].find_parent("section") is None:
        raise InputError("Expected one public keyword overview section; no generic fallback selector.")
    section = headings[0].find_parent("section")
    # Ignore scripts/hidden state; only the displayed section and its labelled rows are read.
    for node in section.find_all(["script", "style", "template", "noscript"]):
        node.decompose()
    for node in section.select('[hidden], [aria-hidden="true"]'):
        node.decompose()
    updated_text = headings[0].find_next("p")
    if updated_text is None or section not in updated_text.parents:
        raise InputError("Public overview update context is missing.")
    date_match = re.search(r"Updated (\w+) (\d{1,2}), (\d{4})\.", updated_text.get_text(" ", strip=True))
    updated = None
    if date_match:
        try:
            updated = datetime(int(date_match[3]), MONTHS[date_match[1]], int(date_match[2])).date().isoformat()
        except (ValueError, KeyError) as exc:
            raise InputError("Invalid displayed public update date.") from exc
        if stamp(updated)[:10] > observed[:10]:
            raise InputError("Displayed update date is after the capture date.")
    elif "Updated" in updated_text.get_text():
        raise InputError("Unrecognised public update date; no timezone or date guess.")
    elapsed = ((datetime.fromisoformat(observed) - datetime.fromisoformat(stamp(updated))).days
               if updated else None)
    freshness = "unknown" if elapsed is None else "stale" if elapsed > age else "current-by-displayed-date"
    items = []
    for name, growth in (("Top Searched Keywords", False), ("Rising Keywords", True)):
        found = [h for h in section.find_all("h3") if h.get_text(" ", strip=True) == name]
        if len(found) != 1:
            raise InputError("Public overview sections are missing or duplicated.")
        heading = found[0]
        card, listing = None, None
        for parent in heading.parents:
            if parent == section:
                break
            direct = parent.find_all("ol", recursive=False)
            if direct:
                if len(direct) != 1:
                    raise InputError("Ambiguous public keyword list.")
                card, listing = parent, direct[0]
                break
        if listing is None:
            raise InputError("Public row structure changed; no guessed extraction.")
        context = heading.find_next("p")
        scope_text = context.get_text(" ", strip=True) if context else ""
        expected = "Fastest growing search terms, past 7 days" if growth else "Highest search volume in the United States"
        if scope_text != expected or card not in context.parents:
            raise InputError("Public geography/window context changed; re-review before importing.")
        rows = listing.find_all("li", recursive=False)
        if not 1 <= len(rows) <= 100:
            raise InputError("Public list needs 1..100 rows; empty data is unavailable.")
        seen = set()
        for rank, row in enumerate(rows, 1):
            spans, paragraphs = row.find_all("span"), row.find_all("p")
            if len(spans) != 2 or len(paragraphs) != 2 or spans[0].get_text(strip=True) != str(rank):
                raise InputError("Public row fields/ranks changed or are incomplete.")
            phrase = normal(label(spans[1].get_text(" ", strip=True), "public keyword"))
            if phrase in seen:
                raise InputError("Duplicate public keywords in the same section.")
            seen.add(phrase)
            display = paragraphs[0].get_text(" ", strip=True)
            if paragraphs[1].get_text(" ", strip=True) != ("vs. previous period" if growth else "searches / mo"):
                raise InputError("Public units changed; counts and growth must remain separate.")
            value, nominal = _display(display, growth)
            geography = None if growth else "US"
            window = "past 7 days versus previous period" if growth else "monthly; provider averaging period unspecified"
            metric = "public_search_growth" if growth else "public_monthly_search_estimate"
            unit = "percent_growth" if growth else "searches_per_month"
            items.append(KeywordCandidate(phrase, "vidiq-public-rising" if growth else "vidiq-public-top", None,
                (KeywordEvidence("vidIQ public keyword overview", metric, value, unit, observed, geography,
                    "Third-party provider display; public update date is a provider statement, not independent accuracy verification."),),
                {"platform": "YouTube", "source_url": URL, "section": name, "rank": rank,
                 "scope": scope_text, "window": window, "geography": geography, "raw_value": display,
                 "nominal_display_value": nominal, "rounded": not growth and isinstance(value, str),
                 "approximate": True, "provider_displayed_update": updated, "freshness": freshness,
                 "evidence_kind": "platform_search_interest" if growth else "search_volume_estimate",
                 "availability": "observed", "completeness": "partial", "capture_sha256": hashlib.sha256(raw).hexdigest()}))
    return PluginResult("vidiq", "import-public-html", tuple(items), (
        "Free public overview import only; no login, charge, API key or hidden endpoint.",
        "Top counts are US estimates. Rising geography is unspecified; seven-day growth is not monthly volume.",
        "Abbreviated displays stay rounded. Provider update dates are preserved; no independent model or arbitrary-query access.",
    ), {"input_path": str(path.resolve()) if path else None, "input_sha256": hashlib.sha256(raw).hexdigest(),
        "network_requests": 0, "source_url": URL, "observed_at": observed,
        "provider_displayed_update": updated, "freshness": freshness, "row_count": len(items),
        "access": "reviewed public HTML capture", "independent_of_vidiq": False})
