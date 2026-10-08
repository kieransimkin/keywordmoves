from pathlib import Path

import pytest

from keywordmoves.builtin.google_trends import GoogleTrendsPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest

FIXTURES = Path(__file__).parent / "fixtures"


def run(operation, name, **options):
    return GoogleTrendsPlugin().run(
        PluginRequest(operation=operation, inputs=(FIXTURES / name,), options=options),
        ExecutionContext(llms=None),
    )


def test_interest_import_normalises_series_and_preserves_caveat():
    result = run("import-interest", "trends_interest.csv", geography="GB")
    assert [item.phrase for item in result.keywords] == ["arcadians", "independent music"]
    assert result.keywords[0].score is None
    assert result.keywords[0].metadata["censored"]
    assert result.keywords[0].evidence[0].unit == "index_0_100"
    assert result.keywords[0].evidence[0].geography == "GB"
    assert "not search-volume" in result.notes[0]


def test_interest_import_records_peak_and_observation_count():
    result = run("import-interest", "trends_interest.csv")
    arcadians = result.keywords[0]
    assert arcadians.evidence[1].value is None
    assert arcadians.metadata["numeric_observations"] == 2
    assert arcadians.metadata["observations"] == 3


def test_related_import_retains_each_section_with_distinct_units():
    result = run("import-related", "trends_related.csv")
    assert [item.phrase for item in result.keywords] == [
        "paper plane song",
        "independent music video",
        "new indie music",
        "paper plane song",
    ]
    assert result.keywords[0].relationship == "trends-rising"
    assert result.keywords[0].evidence[0].value == "Breakout"
    assert result.keywords[0].evidence[0].unit == "growth_label"
    assert result.keywords[1].evidence[0].unit == "percent_growth"
    assert result.keywords[-1].evidence[0].unit == "index_0_100"
    assert all(item.score is None for item in result.keywords)


def test_invalid_operation_fails():
    with pytest.raises(ConfigurationError):
        run("live-scrape", "trends_interest.csv")


def test_missing_input_fails():
    with pytest.raises(InputError):
        run("import-interest", "missing.csv")

