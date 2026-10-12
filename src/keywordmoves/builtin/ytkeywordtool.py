"""YT Keyword Tool's reviewed batch-relative suggestions, never search counts."""
from __future__ import annotations

import re

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginDescriptor, PluginResult
from ..monitoring.validation import label, normal
from .reviewed_html import one, visible_document

URL = "https://ytkeywordtool.com/"


class YTKeywordToolPlugin:
    descriptor = PluginDescriptor(
        "ytkeywordtool", "Collect free public-form suggestions or import reviewed within-batch tier labels.",
        ("import", "language-suggestions", "public-form", "browser-collection"),
        ("import-public-html", "collect-public", "collect-browser"),
    )

    def __init__(self, *, transport=None):
        self._transport = transport

    def run(self, request, context):
        if request.operation == "collect-public":
            from ..online.free_public import collect_ytkeywordtool
            return collect_ytkeywordtool(request, self._transport)
        if request.operation == "collect-browser":
            from ..browser_capture import collect_browser
            return collect_browser(self.descriptor.name, request, context)
        return self.import_html(request)

    def import_html(self, request, *, captured_bytes=None):
        if request.operation != "import-public-html" or len(request.keywords) != 1:
            raise ConfigurationError("ytkeywordtool import-public-html requires one explicit seed keyword.")
        seed = label(request.keywords[0], "reviewed seed")
        doc, observed, metadata = visible_document(request, URL, {"source_url", "observed_at"}, captured_bytes=captured_bytes)
        root = one(doc, "#tool-results")
        count = one(root, "#results-count").get_text(" ", strip=True)
        match = re.fullmatch(r"(\d+) keywords? found", count)
        if not match or not 1 <= int(match[1]) <= 10000:
            raise InputError("Expected a bounded displayed keyword count; empty/error pages are unavailable.")
        total = int(match[1])
        rows = one(root, "#keyword-list").find_all("li", recursive=False)
        if not 1 <= len(rows) <= total:
            raise InputError("Displayed suggestion rows disagree with the reported count.")
        items, seen = [], set()
        for order, row in enumerate(rows, 1):
            button = one(row, "button[data-term]")
            spans = button.find_all("span", recursive=False)
            if len(spans) != 3:
                raise InputError("Suggestion chip structure changed.")
            tier = spans[0].get_text(" ", strip=True)
            phrase = label(spans[1].get_text(" ", strip=True), "suggestion")
            if tier not in {"High", "Medium", "Low"} or button.get("data-term") != phrase:
                raise InputError("Tier or displayed phrase changed; no guessed extraction.")
            identity = normal(phrase)
            if identity in seen:
                raise InputError("Duplicate suggestions in the same captured batch.")
            seen.add(identity)
            category = label(row.get("data-category"), "suggestion category")
            items.append(KeywordCandidate(phrase, "provider-language-suggestion", None, (
                KeywordEvidence("YT Keyword Tool", "provider_autocomplete_tier", tier, "within_batch_label",
                    observed, None, "Provider ranks suggestions within this batch; the tier is not monthly volume or calibrated demand."),
                KeywordEvidence("YT Keyword Tool", "displayed_order", order, "ordinal", observed, None,
                    "Visible presentation order, not a YouTube search-result ranking."),
            ), {"platform": "YouTube", "evidence_kind": "language_suggestion", "category": category,
                "seed": seed, "seed_basis": "caller reviewed the displayed seed; live input values may not serialize in HTML",
                "source_url": URL, "capture_sha256": metadata["input_sha256"],
                "geography": None, "scope": "captured provider batch", "window": "capture only",
                "provider_updated_at": None, "approximate": False,
                "completeness": "complete" if len(rows) == total else "partial"}))
        metadata.update(row_count=len(items), reported_keyword_count=total, seed=seed,
            geography=None, completeness="complete" if len(rows) == total else "partial")
        return PluginResult(self.descriptor.name, request.operation, tuple(items), (
            "Reviewed public suggestions only; provider autocomplete methodology is attributed, not independently certified.",
            "High/Medium/Low is relative to this batch; no numeric volume, universal score or country is invented.",
            "Source measurement time is unspecified; the capture timestamp does not refresh the underlying data.",
        ), metadata)
