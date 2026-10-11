import json

import pytest

from keywordmoves.builtin.vidiq import VidiqPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest


def result(tmp_path, payload=None, **options):
    path = tmp_path / "capture.json"
    data = payload if payload is not None else {"mode": "research", "country": "GB", "seedKeyword": {
        "keyword": "example song", "estimatedMonthlySearch": 0, "monthlySearchesDisplay": "<750",
        "competition": 25, "countryVolume": None, "metricsAsOf": None}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return VidiqPlugin().run(PluginRequest("import-json", inputs=(path,), options={"observed_at": "2026-10-11", **options}), ExecutionContext(None))


def test_censoring_wins_over_zero_and_missing_country_stays_missing(tmp_path):
    row = result(tmp_path).keywords[0]
    assert row.score is None
    assert row.evidence[0].value == "<750"
    assert row.evidence[1].value is None
    assert row.evidence[0].geography == "worldwide"
    assert row.evidence[1].geography == "GB"
    assert row.metadata["raw_estimated_monthly_searches"] == 0
    assert row.metadata["freshness"] == "unknown"


def test_capture_date_does_not_rejuvenate_stale_metrics(tmp_path):
    row = result(tmp_path, {"mode": "research", "country": "GB", "seedKeyword": {
        "keyword": "example song", "estimatedMonthlySearch": 100, "countryVolume": 4,
        "competition": 0, "metricsAsOf": "2026-07-01T00:00:00Z"}}).keywords[0]
    assert row.metadata["freshness"] == "stale"
    assert row.metadata["metrics_as_of"] == "2026-07-01T00:00:00+00:00"
    assert row.evidence[0].value == 100
    assert row.evidence[1].value == 4
    assert row.evidence[3].value == 0
    assert row.evidence[0].observed_at == "2026-10-11T00:00:00+00:00"


@pytest.mark.parametrize("wrapper", ["structuredContent", "content", "envelope"])
def test_supported_mcp_and_reviewed_native_wrappers(tmp_path, wrapper):
    payload = {"mode": "questions", "questions": [{"question": "what is example music?", "estimatedMonthlySearch": 21}]}
    if wrapper == "content":
        payload = {"content": [{"type": "text", "text": "Summary\nKeyword data (JSON):\n" + json.dumps(payload)}]}
    elif wrapper == "envelope":
        payload = {"schema": "keywordmoves-vidiq-capture/v1", "observed_at": "2026-10-10", "request": {"keyword": "example music"}, "result": payload}
    else:
        payload = {wrapper: payload}
    imported = result(tmp_path, payload)
    assert imported.keywords[0].phrase == "what is example music?"
    assert imported.metadata["network_requests"] == 0
    assert len(imported.metadata["input_sha256"]) == 64


@pytest.mark.parametrize("row", [{"competition": 101}, {"countryVolume": 12}, {"estimatedMonthlySearch": True}, {"estimatedMonthlySearch": -1}])
def test_invalid_native_metrics_are_not_coerced(tmp_path, row):
    with pytest.raises(InputError):
        result(tmp_path, {"mode": "research", "seedKeyword": {"keyword": "example song", **row}})


@pytest.mark.parametrize("data", [{"mode": "unknown"}, {"mode": "research", "relatedKeywords": {}}, {"isError": True}, {"mode": "research", "access_token": "synthetic-secret"}])
def test_reject_failed_ambiguous_or_secret_captures(tmp_path, data):
    with pytest.raises(InputError):
        result(tmp_path, data)


def test_country_conflict_and_unknown_option_fail(tmp_path):
    with pytest.raises(InputError):
        result(tmp_path, country="US")
    with pytest.raises(ConfigurationError):
        result(tmp_path, api_key="synthetic")


def test_exact_zero_without_censor_and_negative_growth_are_preserved(tmp_path):
    row = result(tmp_path, {"mode": "matching_terms", "matchingTerms": [{"keyword": "example song", "estimatedMonthlySearch": 0, "monthlySearchesDisplay": "0", "searchDemandGrowthPct": -50}]}).keywords[0]
    assert row.evidence[0].value == 0
    assert row.evidence[6].value == -50
    assert row.metadata["estimated_monthly_searches_censored"] is False
