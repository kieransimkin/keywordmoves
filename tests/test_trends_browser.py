"""Synthetic native CSV receipts; never live queries or account captures."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from keywordmoves.browser_capture import analyse_capture
from keywordmoves.builtin.google_trends import GoogleTrendsPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest


def receipt(chart="interest"):
    csv = (
        (
            "Category: All categories\n\nWeek,example song: (Worldwide)\n"
            "2026-10-04,25\n2026-10-11,100\n"
        )
        if chart == "interest"
        else (
            'Category: All categories\n"example song: (13/10/2025 - 13/10/2026, Worldwide)"\n\n'
            "TOP\nexample music,100\n\nRISING\nexample music,Breakout\nnew example song,50%\n"
        )
    )
    return {
        "schema": "keywordmoves-trends-browser/v1",
        "source": "google-trends",
        "source_url": "https://trends.google.com/trends/explore",
        "page_url": "https://trends.google.com/trends/explore?gprop=youtube&q=example%20song&hl=en",
        "seed": "example song",
        "observed_at": "2026-10-13T12:00:00Z",
        "country": "GLB",
        "window": "today 12-m",
        "chart": chart,
        "csv": csv,
        "controls": {
            "seed": "example song",
            "country": "Worldwide",
            "window": "Past 12 months",
            "property": "YouTube Search",
            "category": "All categories",
            "term_type": "Search term",
        },
    }


def test_native_interest_excludes_unfinished_week_and_has_no_volume_score():
    result = analyse_capture(receipt())
    k = result.keywords[0]
    assert k.score is None and k.evidence[0].value == 25
    assert k.metadata["summary_observations"] == 1
    assert k.metadata["excluded_incomplete_periods"] == 1
    assert k.metadata["platform"] == "YouTube"
    assert result.metadata["network_requests"] is None
    assert result.metadata["manual_import_required"] is False
    assert result.metadata["source_file"] is None


def test_related_query_sections_keep_distinct_units_and_duplicate_phrase():
    result = analyse_capture(receipt("related"))
    assert [k.evidence[0].unit for k in result.keywords] == [
        "index_0_100",
        "growth_label",
        "percent_growth",
    ]
    assert len(result.keywords) == 3
    assert result.keywords[1].evidence[0].value == "Breakout"
    assert result.keywords[0].phrase == result.keywords[1].phrase


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "https://example.com"),
        ("source", "other"),
        ("country", []),
        ("window", "today 7-m"),
        ("chart", "regions"),
        ("seed", "wrong"),
        ("csv", 1),
        ("controls", {}),
        ("observed_at", "bad"),
        ("page_url", "https://trends.google.com/trends/explore?gprop=web&q=example%20song&hl=en"),
        (
            "page_url",
            "https://trends.google.com/trends/explore?gprop=youtube&q=example%20song&hl=en&geo=GB",
        ),
        (
            "page_url",
            "https://trends.google.com/trends/explore?gprop=youtube&q=example%20song&hl=en&q=example%20song",
        ),
        (
            "page_url",
            "https://trends.google.com@evil.test/trends/explore?gprop=youtube&q=example%20song&hl=en",
        ),
    ],
)
def test_mismatched_receipt_rejected(field, value):
    data = receipt()
    data[field] = value
    with pytest.raises((InputError, ConfigurationError)):
        analyse_capture(data)


@pytest.mark.parametrize(
    "change", ["country", "term", "duplicate", "ordering", "oversize", "category"]
)
def test_changed_or_unbounded_csv_rejected(change):
    data = receipt()
    if change == "country":
        data["csv"] = data["csv"].replace("Worldwide", "United Kingdom")
    if change == "term":
        data["csv"] = data["csv"].replace("example song", "wrong song")
    if change == "duplicate":
        data["csv"] += "2026-10-04,50\n"
    if change == "ordering":
        data["csv"] = data["csv"].replace(
            "2026-10-04,25\n2026-10-11,100", "2026-10-11,100\n2026-10-04,25"
        )
    if change == "oversize":
        data["csv"] = "x" * 5000001
    if change == "category":
        data["csv"] = data["csv"].replace("All categories", "Music")
    with pytest.raises(InputError):
        analyse_capture(data)


def test_driver_validates_requested_scope_and_missing_driver_is_accurate():
    plugin = GoogleTrendsPlugin()
    request = PluginRequest("collect-browser", ("example song",))
    with pytest.raises(ConfigurationError, match="authorised public_browser"):
        plugin.run(request, ExecutionContext(None))
    driver = SimpleNamespace(collect=lambda source, request: receipt())
    result = plugin.run(request, ExecutionContext(None, public_browser=driver))
    assert result.operation == "collect-browser"
    wrong = deepcopy(receipt())
    wrong["chart"] = "related"
    with pytest.raises(InputError, match="different requested scope"):
        plugin.run(
            request,
            ExecutionContext(None, public_browser=SimpleNamespace(collect=lambda *args: wrong)),
        )


def test_bom_and_source_byte_identity_preserved():
    import hashlib

    data = receipt()
    data["csv"] = "\ufeff" + data["csv"]
    assert (
        analyse_capture(data).metadata["source_sha256"]
        == hashlib.sha256(data["csv"].encode()).hexdigest()
    )


def test_country_preset_and_native_partial_flag_are_checked():
    data = receipt()
    data["country"] = "GB"
    data["controls"]["country"] = "United Kingdom"
    data["page_url"] += "&geo=GB"
    data["csv"] = data["csv"].replace("Worldwide", "United Kingdom")
    result = analyse_capture(data)
    assert result.keywords[0].evidence[0].geography == "GB"
    data["csv"] = data["csv"].replace("(United Kingdom)\n", "(United Kingdom),IsPartial\n")
    data["csv"] = (
        data["csv"]
        .replace("2026-10-04,25", "2026-10-04,25,true")
        .replace("2026-10-11,100", "2026-10-11,100,false")
    )
    assert analyse_capture(data).keywords[0].evidence[0].value is None


@pytest.mark.parametrize(
    "dates", ["99/10/2025 - 13/10/2026", "13/10/2020 - 13/10/2026", "13/10/2024 - 13/10/2025"]
)
def test_related_reporting_window_must_match(dates):
    data = receipt("related")
    data["csv"] = data["csv"].replace("13/10/2025 - 13/10/2026", dates)
    with pytest.raises(InputError):
        analyse_capture(data)
