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
    assert result.keywords[0].score == 0.25
    assert result.keywords[0].evidence[0].unit == "index_0_100"
    assert result.keywords[0].evidence[0].geography == "GB"
    assert "not search-volume" in result.notes[0]


def test_interest_import_records_peak_and_observation_count():
    result = run("import-interest", "trends_interest.csv")
    arcadians = result.keywords[0]
    assert arcadians.evidence[1].value == 50.0
    assert arcadians.metadata["observations"] == 3


def test_related_import_deduplicates_and_retains_first_section():
    result = run("import-related", "trends_related.csv")
    assert [item.phrase for item in result.keywords] == [
        "paper plane song",
        "independent music video",
        "new indie music",
    ]
    assert result.keywords[0].relationship == "trends-rising"
    assert result.keywords[0].evidence[0].value == "Breakout"


def test_invalid_operation_fails():
    with pytest.raises(ConfigurationError):
        run("live-scrape", "trends_interest.csv")


def test_missing_input_fails():
    with pytest.raises(InputError):
        run("import-interest", "missing.csv")

