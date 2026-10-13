"""Zero-network example of automatic native browser CSV handoff; synthetic data."""
from keywordmoves.browser_capture import analyse_capture

capture = {
    "schema": "keywordmoves-trends-browser/v1", "source": "google-trends",
    "source_url": "https://trends.google.com/trends/explore",
    "page_url": "https://trends.google.com/trends/explore?q=example%20song&gprop=youtube&hl=en",
    "observed_at": "2026-10-13T12:00:00Z", "seed": "example song", "country": "GLB",
    "window": "today 12-m", "chart": "interest",
    "controls": {"seed": "example song", "country": "Worldwide", "window": "Past 12 months",
                 "property": "YouTube Search", "category": "All categories", "term_type": "Search term"},
    "csv": "Category: All categories\n\nWeek,example song: (Worldwide)\n2026-10-04,25\n2026-10-11,100\n",
}
result = analyse_capture(capture)
assert result.keywords[0].metadata["excluded_incomplete_periods"] == 1
assert result.keywords[0].evidence[0].value == 25
assert result.keywords[0].score is None
print("Synthetic native export: one completed week; no network or manual import.")
