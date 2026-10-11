import hashlib

import pytest

from keywordmoves.builtin.vidiq import VidiqPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest

URL = "https://vidiq.com/features/keyword-tools/"


def widget(volume="1.2M", growth="+2,500%", date="October 11, 2026"):
    updated = f"Updated {date}." if date else "Refresh date unavailable."
    return f'''<section><h2>The most searched keywords on YouTube right now</h2><p>{updated}</p>
<div><div><h3>Top Searched Keywords</h3><p>Highest search volume in the United States</p></div>
<ol><li><div><span>1</span><span>example 2026</span></div><div><p>{volume}</p><p>searches / mo</p></div></li></ol></div>
<div><div><h3>Rising Keywords</h3><p>Fastest growing search terms, past 7 days</p></div>
<ol><li><div><span>1</span><span>another phrase</span></div><div><p>{growth}</p><p>vs. previous period</p></div></li></ol></div></section>'''


def invoke(tmp_path, html=None, **options):
    path = tmp_path / "overview.html"
    path.write_bytes((html or widget()).encode("utf-8"))
    return VidiqPlugin().run(PluginRequest("import-public-html", inputs=(path,), options={
        "source_url": URL, "observed_at": "2026-10-11", **options}), ExecutionContext(None))


def test_public_overview_preserves_rounding_country_growth_and_hash(tmp_path):
    result = invoke(tmp_path)
    top, rising = result.keywords
    assert top.phrase == "example 2026"
    assert top.evidence[0].value == "1.2M"
    assert top.metadata["nominal_display_value"] == 1200000
    assert top.metadata["rounded"] is True
    assert top.evidence[0].geography == "US"
    assert top.evidence[0].unit == "searches_per_month"
    assert "unspecified" in top.metadata["window"]
    assert rising.evidence[0].value == 2500
    assert rising.evidence[0].unit == "percent_growth"
    assert rising.evidence[0].geography is None
    assert "past 7 days" in rising.metadata["window"]
    assert all(k.score is None for k in result.keywords)
    assert result.metadata["input_sha256"] == hashlib.sha256(widget().encode()).hexdigest()
    assert result.metadata["network_requests"] == 0
    assert result.metadata["independent_of_vidiq"] is False


@pytest.mark.parametrize("date,expected", [("July 1, 2026", "stale"), (None, "unknown"),
                                         ("October 11, 2026", "current-by-displayed-date")])
def test_update_date_does_not_rejuvenate_metrics(tmp_path, date, expected):
    assert invoke(tmp_path, widget(date=date)).metadata["freshness"] == expected


@pytest.mark.parametrize("volume,expected", [("0", 0), ("1,200", 1200), ("1.2K", "1.2K")])
def test_numeric_and_abbreviated_display_values(tmp_path, volume, expected):
    assert invoke(tmp_path, widget(volume=volume)).keywords[0].evidence[0].value == expected


@pytest.mark.parametrize("volume", ["NaN", "-1", "1,2K", "Very high", "4%", "1e9", "9" * 1000])
def test_invalid_counts_fail(tmp_path, volume):
    with pytest.raises(InputError):
        invoke(tmp_path, widget(volume=volume))


@pytest.mark.parametrize("replacement", [
    ("United States", "United Kingdom"), ("searches / mo", "video views"),
    ("past 7 days", "past 30 days"), ("<span>1</span>", "<span>2</span>"),
    ("October 11, 2026", "October 12, 2026"), ("October 11, 2026", "February 30, 2026"),
    ("Top Searched Keywords", "Top videos"),
])
def test_schema_geography_date_or_unit_changes_require_review(tmp_path, replacement):
    with pytest.raises(InputError):
        invoke(tmp_path, widget().replace(*replacement))


def test_duplicate_sections_or_rows_fail(tmp_path):
    with pytest.raises(InputError):
        invoke(tmp_path, widget() + widget())
    with pytest.raises(InputError):
        invoke(tmp_path, widget().replace("<ol>", "<ol><li><span>1</span><span>example 2026</span><p>0</p><p>searches / mo</p></li>", 1))


def test_scripts_and_hidden_numbers_are_ignored(tmp_path):
    result = invoke(tmp_path, widget().replace("<section>", '<section><script>volume=90000000</script><div hidden>99M</div>'))
    assert result.keywords[0].evidence[0].value == "1.2M"


@pytest.mark.parametrize("options", [{"source_url": "https://example.org/"}, {"api_key": "secret"},
                                     {"max_age_days": True}, {"observed_at": "yesterday"}])
def test_explicit_offline_context_required(tmp_path, options):
    with pytest.raises((InputError, ConfigurationError)):
        invoke(tmp_path, **options)
