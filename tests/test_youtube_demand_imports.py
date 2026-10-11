import hashlib
import json

import pytest

from keywordmoves.builtin.google_trends import GoogleTrendsPlugin
from keywordmoves.builtin.research_batch import ResearchBatchPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.commercial import KeywordToolPlugin

CTX = ExecutionContext(None)


def trends(tmp_path, content, **options):
    path = tmp_path / "trends.csv"
    path.write_bytes(content.encode("utf-8"))
    return GoogleTrendsPlugin().run(PluginRequest("import-interest", inputs=(path,), options=options), CTX)


def test_completed_weeks_preserve_raw_current_period(tmp_path):
    content = "Week,Example\n2026-10-04,20\n2026-10-11,100\n"
    result = trends(tmp_path, content, observed_at="2026-10-11", completed_periods_only=True)
    item = result.keywords[0]
    assert item.evidence[0].value == 20
    assert item.evidence[1].value == 20
    assert item.metadata["observations"] == 2
    assert item.metadata["summary_observations"] == 1
    assert item.metadata["excluded_incomplete_periods"] == 1
    assert item.metadata["series"][1]["value"] == 100
    assert item.metadata["summary_window"] == {"start": "2026-10-04", "end": "2026-10-10"}
    assert result.metadata["source_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert trends(tmp_path, content).keywords[0].evidence[0].value == 60


@pytest.mark.parametrize("header,period,observed,expected", [
    ("Month", "2024-02-01", "2024-03-01", 10),
    ("Month", "2024-02-01", "2024-02-29", None),
    ("Day", "2026-10-10", "2026-10-11", 10),
    ("Date", "2026-10-11", "2026-10-11", None),
])
def test_completed_period_calendar_boundaries(tmp_path, header, period, observed, expected):
    item = trends(tmp_path, f"{header},Example\n{period},10\n", observed_at=observed,
                  completed_periods_only=True).keywords[0]
    assert item.evidence[0].value == expected
    if expected is None:
        assert item.metadata["summary_window"] is None
        assert item.metadata["availability"] == "no-data"


def test_completed_summary_respects_partial_flags_and_censoring(tmp_path):
    item = trends(tmp_path, "Week,Example,IsPartial\n2026-09-20,<1,false\n2026-09-27,90,true\n",
                  observed_at="2026-10-11", completed_periods_only="true").keywords[0]
    assert item.evidence[0].value is None
    assert item.metadata["excluded_incomplete_periods"] == 1
    assert item.metadata["series"][0]["raw_value"] == "<1"


@pytest.mark.parametrize("content,options", [
    ("Week,Example\n2026-10-04,20\n", {}),
    ("Week,Example\n2026-10-04,20\n", {"observed_at": "yesterday"}),
    ("Week,Example\n2026-10-04,20\n", {"observed_at": "2026-10-11T00:00:00"}),
    ("Region,Example\nGB,20\n", {"observed_at": "2026-10-11"}),
    ("Month,Example\n2026-09-02,20\n", {"observed_at": "2026-10-11"}),
    ("Week,Example\nnot-a-date,20\n", {"observed_at": "2026-10-11"}),
])
def test_completed_period_invalid_context_fails(tmp_path, content, options):
    with pytest.raises((InputError, ConfigurationError)):
        trends(tmp_path, content, completed_periods_only=True, **options)


def kt(tmp_path, data=None, **options):
    path = tmp_path / "keywordtool.json"
    path.write_text(json.dumps(data if data is not None else {"results": {"Example Song": {
        "volume": 1200, "cpc": 0.5, "cmp": 0.25, "trend": -0.1,
        "m1": 1000, "m1_month": 9, "m1_year": 2026}}}), encoding="utf-8")
    return KeywordToolPlugin().run(PluginRequest("import-json", inputs=(path,), options={
        "platform": "youtube", "country": "GLB", "observed_at": "2026-10-11", **options}), CTX)


def test_keywordtool_worldwide_source_windows_dates_and_proxies(tmp_path, monkeypatch):
    monkeypatch.setattr("keywordmoves.online.commercial.secret", lambda *a: pytest.fail("Credential read"))
    result = kt(tmp_path)
    item = result.keywords[0]
    assert result.metadata["network_requests"] == 0
    assert item.phrase == "example song" and item.score is None
    assert all(e.geography == "worldwide" for e in item.evidence)
    values = {e.metric: e for e in item.evidence}
    assert values["estimated_search_volume"].value == 1200
    assert values["estimated_search_volume"].unit == "searches_per_month"
    assert "last 12 months" in item.metadata["window"]
    assert values["google_ads_competition_proxy"].value == 0.25
    assert values["provider_volume_trend"].value == -0.1
    assert item.metadata["freshness"] == "unknown"
    assert item.metadata["metrics_as_of"] is None
    assert item.metadata["monthly_series"] == [{"slot": 1, "period": "2026-09", "value": 1000, "completed": True}]


def test_keywordtool_notice_missing_and_true_zero_are_separate(tmp_path):
    result = kt(tmp_path, {"notice": {"code": 10}, "results": {"Zero": {"volume": 0}, "Missing": {}}}, country="GB")
    assert result.metadata["partial_notice"]
    assert result.keywords[0].evidence[0].value == 0
    assert result.keywords[1].evidence[0].value is None
    assert all(k.metadata["completeness"] == "partial" for k in result.keywords)
    assert all(e.geography == "GB" for k in result.keywords for e in k.evidence)


def test_keywordtool_capture_context_and_hash(tmp_path):
    result = kt(tmp_path, {"schema": "keywordmoves-keywordtool-capture/v1", "observed_at": "2026-10-11",
                          "request": {"platform": "youtube", "country": "GLB"},
                          "result": {"results": {"Sample": {"volume": 10}}}})
    assert len(result.metadata["source_sha256"]) == 64
    with pytest.raises(InputError, match="conflict"):
        kt(tmp_path, {"schema": "keywordmoves-keywordtool-capture/v1", "observed_at": "2026-10-11",
                      "request": {"platform": "youtube", "country": "US"}, "result": {"results": {"Sample": {}}}})


@pytest.mark.parametrize("row", [
    {"volume": -1}, {"volume": True}, {"volume": "100"}, {"volume": float("inf")},
    {"cmp": 1.1}, {"cpc": -0.1}, {"trend": True}, {"m1": 100},
    {"m1": 100, "m1_month": 13, "m1_year": 2026},
    {"m1_month": 9}, {"m01": 100}, {"m49": 100},
    {"m1": 1, "m1_month": 9, "m1_year": 2026, "m2": 2, "m2_month": 9, "m2_year": 2026},
    {"apikey": "private"},
])
def test_keywordtool_rejects_invalid_or_sensitive_data(tmp_path, row):
    with pytest.raises(InputError):
        kt(tmp_path, {"results": {"Example": row}})


@pytest.mark.parametrize("data", [{"results": {}}, {"results": []}, {"results": {"a": []}},
                                  {"results": {"A": {}, "a": {}}},
                                  {"error": {"code": 10}, "results": {"a": {}}}])
def test_keywordtool_invalid_payloads(tmp_path, data):
    with pytest.raises(InputError):
        kt(tmp_path, data)


@pytest.mark.parametrize("options", [{"platform": "google"}, {"country": "USA"},
                                     {"observed_at": None}, {"currency": "GBP1"}, {"api_key": "secret"}])
def test_keywordtool_offline_scope_required(tmp_path, options):
    with pytest.raises((InputError, ConfigurationError)):
        kt(tmp_path, **options)


def test_monthly_unfinished_and_missing_rows_are_retained(tmp_path):
    result = kt(tmp_path, {"results": {"Sample": {"m1": 0, "m1_month": 10, "m1_year": 2026,
                                                 "m2": None, "m2_month": 9, "m2_year": 2026}}})
    points = result.keywords[0].metadata["monthly_series"]
    assert points[0]["completed"] is False and points[0]["value"] == 0
    assert points[1]["completed"] is True and points[1]["value"] is None


@pytest.mark.parametrize("operation", ["suggestions", "metrics"])
def test_batch_never_treats_live_keywordtool_as_free(tmp_path, operation, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": [
        {"id": "one", "subject": "Example", "plugin": "keywordtool", "operation": operation}]}), encoding="utf-8")
    monkeypatch.setattr("keywordmoves.registry.PluginRegistry.get", lambda *a: pytest.fail("Live route reached"))
    with pytest.raises(InputError, match="only offline"):
        ResearchBatchPlugin().run(PluginRequest("collect", inputs=(path,)), CTX)


def test_batch_composes_offline_keywordtool(tmp_path):
    kt(tmp_path)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": [
        {"id": "one", "subject": "Example", "plugin": "keywordtool", "operation": "import-json",
         "inputs": [str(tmp_path / "keywordtool.json")],
         "options": {"platform": "youtube", "country": "GLB", "observed_at": "2026-10-11"}}]}), encoding="utf-8")
    result = ResearchBatchPlugin().run(PluginRequest("collect", inputs=(path,)), CTX)
    assert len(result.keywords) == 1
    assert result.keywords[0].metadata["subject"] == "Example"
    assert result.metadata["tasks"][0]["result"]["metadata"]["network_requests"] == 0
