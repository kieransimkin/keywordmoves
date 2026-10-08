
import pytest

from keywordmoves.builtin.google_trends import GoogleTrendsPlugin
from keywordmoves.builtin.native_export import NativeExportPlugin
from keywordmoves.errors import InputError
from keywordmoves.models import ExecutionContext, PluginRequest


def native(tmp_path, csv, **options):
    path = tmp_path / "native.csv"
    path.write_text(csv, encoding="utf-8")
    defaults = {
        "platform": "Pinterest", "source": "Reviewed native report", "metric": "search_interest",
        "unit": "index_0_100", "observed_at": "2026-10-07", "geography": "GB",
        "scope": "Public trends UI", "window": "12 months",
        "evidence_kind": "platform_search_interest", "phrase_column": "Query", "value_column": "Value",
    }
    return NativeExportPlugin().run(PluginRequest(operation="import-csv", inputs=(path,),
                                   options={**defaults, **options}), ExecutionContext(llms=None))


def trends(tmp_path, csv, **options):
    path = tmp_path / "related.csv"
    path.write_text(csv, encoding="utf-8")
    return GoogleTrendsPlugin().run(PluginRequest(operation="import-related", inputs=(path,),
        options={"observed_at": "2026-10-07", "geography": "GB", **options}),
        ExecutionContext(llms=None))


def test_native_export_keeps_missing_zero_censored_and_unicode_separate(tmp_path):
    result = native(tmp_path, 'Query,Value\nامید,0\nsummer music,<10\nquiet song,-\n')
    assert [item.evidence[0].value for item in result.keywords] == [0, "<10", None]
    assert result.keywords[1].metadata["censored"] is True
    assert result.keywords[2].metadata["availability"] == "unavailable"
    assert all(item.score is None for item in result.keywords)
    assert len(result.metadata["source_sha256"]) == 64


def test_native_export_does_not_infer_platform_or_units(tmp_path):
    with pytest.raises(InputError, match="Percentage"):
        native(tmp_path, 'Query,Value\nsong,25%\n')
    with pytest.raises(InputError, match="column"):
        native(tmp_path, 'Query,Value\nsong,25\n', phrase_column="Absent")
    with pytest.raises(InputError, match="header"):
        native(tmp_path, 'Query,query,Value\nsong,song,25\n')


@pytest.mark.parametrize("csv", [
    'Query,Value\nsong,NaN\n', 'Query,Value\nsong,inf\n',
    'Query,Value\nsong,10,extra\n', 'Query,Value\nsong\n',
    'Query,Value\n"unterminated,10\n',
])
def test_native_export_rejects_nonfinite_or_truncated_rows(tmp_path, csv):
    with pytest.raises(InputError):
        native(tmp_path, csv)


def test_native_export_explicit_per_row_metrics_dates_and_units(tmp_path):
    result = native(tmp_path, 'Query,Value,Metric,Unit,Date\nsong,25%,growth,percent_growth,2026-10-07\n',
                    metric_column="Metric", unit_column="Unit", date_column="Date")
    assert result.keywords[0].evidence[0].value == 25
    assert result.keywords[0].evidence[0].unit == "percent_growth"


def test_trends_preserves_top_and_rising_for_the_same_phrase(tmp_path):
    result = trends(tmp_path, 'Category: All categories\nTOP\nsong,100\nRISING\nsong,250%\n',
                    seed_keyword="music", category="0", search_property="web", window="12 months")
    assert len(result.keywords) == 2
    assert [item.evidence[0].unit for item in result.keywords] == ["index_0_100", "percent_growth"]
    assert result.keywords[0].metadata["seed_keyword"] == "music"
    assert result.metadata["export_headers"] == [["Category: All categories"]]


def test_trends_breakout_lower_bound_and_unicode(tmp_path):
    result = trends(tmp_path, 'RISING\nامید,Breakout\nTOP\nآهنگ,75\n')
    assert result.keywords[0].evidence[0].value == "Breakout"
    assert result.keywords[0].metadata["breakout_lower_bound_percent"] == 5000
    assert all(item.score is None for item in result.keywords)


def test_trends_exact_duplicate_dedup_and_conflict_detection(tmp_path):
    result = trends(tmp_path, 'TOP\nsong,25\nsong,25\n')
    assert len(result.keywords) == 1 and result.metadata["duplicate_rows"] == 1
    with pytest.raises(InputError, match="Conflicting"):
        trends(tmp_path, 'TOP\nsong,25\nsong,26\n')


@pytest.mark.parametrize("csv", [
    'TOP\nsong,250%\n', 'TOP\nsong,101\n', 'TOP\nsong,-1\n',
    'TOP\nsong,NaN\n', 'RISING\nsong,inf\n', 'TOP\nsong,Breakout\n',
    'TOP\nsong,10,extra\n', 'TOP\nsong\n', 'RISING\nsong,<1\n',
    'song,25\n',
])
def test_trends_rejects_incompatible_units_and_malformed_values(tmp_path, csv):
    with pytest.raises(InputError):
        trends(tmp_path, csv)


def test_trends_censored_and_unavailable_are_not_zero(tmp_path):
    result = trends(tmp_path, 'TOP\nsong,<1\nquiet,-\nzero,0\n')
    assert [item.evidence[0].value for item in result.keywords] == [None, None, 0]
    assert result.keywords[0].metadata["censored"]
    assert result.keywords[1].metadata["availability"] == "no-data"


def test_trends_explicit_section_for_a_reviewed_single_section_export(tmp_path):
    result = trends(tmp_path, "Query,Value\nsong,50\n", section="top")
    assert result.keywords[0].evidence[0].unit == "index_0_100"


def test_google_search_routes_related_exports_through_shared_parser(tmp_path):
    from keywordmoves.online.google_search import GoogleSearchPlugin
    path = tmp_path / "related.csv"
    path.write_text("TOP\nsong,100\nRISING\nsong,Breakout\n", encoding="utf-8")
    result = GoogleSearchPlugin().run(PluginRequest("trends-related-import", inputs=(path,),
        options={"country": "GB", "observed_at": "2026-10-07", "seed_keyword": "music",
                 "scope": "GB/Web", "window": "12 months", "search_property": "web"}),
        ExecutionContext(llms=None))
    assert [item.evidence[0].unit for item in result.keywords] == ["index_0_100", "growth_label"]
    assert result.metadata["source_sha256"]


def test_trends_youtube_property_keeps_platform_scope(tmp_path):
    result = trends(tmp_path, "TOP\nsong,50\n", search_property="youtube")
    assert result.keywords[0].metadata["platform"] == "YouTube"


def test_interest_keeps_partial_values_without_exact_summary(tmp_path):
    path = tmp_path / "interest.csv"
    path.write_text("Day,song,IsPartial\n2026-10-01,0,false\n2026-10-02,<1,true\n",
                    encoding="utf-8")
    result = GoogleTrendsPlugin().run(PluginRequest("import-interest", inputs=(path,)),
                                    ExecutionContext(llms=None))
    assert len(result.keywords) == 1
    item = result.keywords[0]
    assert item.evidence[0].value is None and item.score is None
    assert item.metadata["series"][0]["value"] == 0
    assert item.metadata["series"][1]["censored"] is True
