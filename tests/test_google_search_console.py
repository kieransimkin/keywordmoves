"""Search Console contract tests with real HTTPX serialization and no live data."""
import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import google_search_console as c
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.google_search import GoogleSearchPlugin

httpx = pytest.importorskip("httpx")
BASE = {"site_url": "sc-domain:example.com", "start_date": "2026-09-01", "end_date": "2026-09-07"}


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def call(operation, handler, keywords=(), **options):
    seen = []
    def handle(req):
        seen.append(req)
        return handler(req)
    r = GoogleSearchPlugin(transport=httpx.MockTransport(handle)).run(
        PluginRequest(operation, tuple(keywords), options={**BASE, "access_token": "SECRET-TOKEN", **options}), ExecutionContext(None))
    assert "SECRET" not in json.dumps(r.to_dict())
    return r, seen


@pytest.mark.parametrize("options", [{"start_date": "20260901"}, {"end_date": "2026-08-01"},
    {"dimensions": "query,query"}, {"dimensions": ["nonsense"]}, {"dimensions": True},
    {"dimensions": "query,searchAppearance"}, {"dimensions": "hour"}, {"data_state": "hourly_all"},
    {"country": "gb"}, {"device": "mobile"}, {"search_type": "universal"}, {"aggregation": "invalid"},
    {"aggregation": "byProperty", "dimensions": "page"}, {"aggregation": "byProperty", "page": "https://example.com/"},
    {"aggregation": "byProperty", "search_type": "discover"}, {"aggregation": "byNewsShowcasePanel"},
    {"filters_json": "[invalid"}, {"filters_json": {}}, {"filters_json": [{"dimension": "query", "operator": "equals"}]},
    {"filters_json": [{"dimension": "query", "operator": "unknown", "expression": "x"}]},
    {"filters_json": [{"dimension": "query", "operator": "equals", "expression": "x"}]*21},
    {"site_url": "invalid"}, {"site_url": "https://example.com"}, {"site_url": "sc-domain:x"},
    {"site_url": "https://user:SECRET@example.com/"}])
def test_configuration_fails_before_request(options):
    with pytest.raises(ConfigurationError):
        call("gsc-query", lambda _: pytest.fail("no request allowed"), **options)


def test_query_filters_preserve_native_dimensions_regex_and_units():
    def handler(req):
        assert req.method == "POST" and req.headers["Authorization"] == "Bearer SECRET-TOKEN"
        assert "sc-domain%3Aexample.com" in str(req.url)
        body = json.loads(req.content)
        assert body["dimensions"] == ["query", "page", "country", "device"]
        assert body["rowLimit"] == 1000 and body["startRow"] == 0
        filters = body["dimensionFilterGroups"][0]["filters"]
        assert filters[0] == {"dimension": "query", "operator": "includingRegex", "expression": "(?i)(plane|music)"}
        assert filters[1]["expression"] == "gbr" and filters[2]["expression"] == "MOBILE"
        return httpx.Response(200, json={"rows": [{"keys": ["Music", "https://example.com/a", "gbr", "MOBILE"],
            "clicks": 5, "impressions": 100, "ctr": .05, "position": 5}], "responseAggregationType": "byPage"})
    r, seen = call("gsc-query", handler, ["(?i)(plane|music)"], dimensions="query,page,country,device",
                   country="GBR", device="MOBILE", query_operator="includingRegex")
    assert len(seen) == 1 and r.keywords[0].phrase == "Music"
    assert r.metadata["gsc_report"]["complete_query_inventory"] is False
    assert r.metadata["gsc_report"]["aggregation_type"] == "byPage"


def test_pagination_retains_continuation_and_partial_metadata():
    def handler(req):
        body = json.loads(req.content)
        offset = body["startRow"]
        return httpx.Response(200, json={"rows": [{"keys": [f"q{offset}"], "clicks": 1, "impressions": 10}],
            "metadata": {"first_incomplete_date": "2026-09-07"}, "responseAggregationType": "byProperty"})
    r, seen = call("gsc-query", handler, page_size=1, pages=2, max_rows=2, data_state="all", limit=1)
    assert [json.loads(x.content)["startRow"] for x in seen] == [0, 1]
    report = r.metadata["gsc_report"]
    assert report["next_start_row"] == 2 and report["pagination_may_have_more"]
    assert len(report["rows"]) == 2 and len(r.keywords) == 1 and r.metadata["output_truncated"]
    assert report["incomplete_data"]["first_incomplete_date"] == "2026-09-07"


def test_empty_page_stops_without_total_inventory_claim():
    r, seen = call("gsc-query", lambda _: httpx.Response(200, json={}), pages=3)
    assert len(seen) == 1 and not r.keywords and r.metadata["gsc_report"]["next_start_row"] is None
    assert not r.metadata["gsc_report"]["complete_query_inventory"]


def test_hourly_state_and_appearance():
    _site, body = c.request_body(PluginRequest("gsc-query", options={**BASE, "data_state": "hourly_all", "dimensions": "hour"}))
    assert body["dataState"] == "hourly_all"
    _, body = c.request_body(PluginRequest("gsc-query", options={**BASE, "search_type": "discover", "appearance": "NEWS_SHOWCASE", "aggregation": "byNewsShowcasePanel"}))
    assert body["aggregationType"] == "byNewsShowcasePanel"
    assert c.dimensions({"dimensions": ""}) == []


@pytest.mark.parametrize("payload", [{"rows": None}, {"rows": [{"keys": []}]}, {"error": {"message": "SECRET"}},
    {"rows": [{"keys": ["q"], "clicks": True}]}, {"rows": [{"keys": ["q"]}, {"keys": ["q"]}]}])
def test_bad_reports(payload):
    with pytest.raises((InputError, OnlineSourceError)) as error:
        call("gsc-query", lambda _: httpx.Response(200, json=payload))
    assert "SECRET" not in str(error.value)


def test_page_budget_preflight():
    with pytest.raises(ConfigurationError):
        call("gsc-query", lambda _: pytest.fail("not requested"), pages=6)


def test_paged_aggregation_change_fails():
    def handler(req):
        pos = json.loads(req.content)["startRow"]
        return httpx.Response(200, json={"rows": [{"keys": [str(pos)]}], "responseAggregationType": "byPage" if pos else "byProperty"})
    with pytest.raises(OnlineSourceError, match="aggregation changed"):
        call("gsc-query", handler, pages=2, page_size=1)


def test_pages_and_query_overlap_default_dimensions():
    for operation, dims in [("gsc-pages", ["page"]), ("gsc-query-pages", ["query", "page"]), ("gsc-overlap", ["query", "page"])]:
        def handler(req):
            assert json.loads(req.content)["dimensions"] == dims
            return httpx.Response(200, json={"rows": []})
        call(operation, handler)


def test_opportunity_can_run_from_api():
    r, _ = call("gsc-opportunities", lambda _: httpx.Response(200, json={"rows": [
        {"keys": ["music"], "clicks": 1, "impressions": 200, "ctr": .005, "position": 7}]}), target_ctr=.025)
    assert r.keywords[0].relationship == "review-candidate"
    assert r.keywords[0].evidence[-1].value == 4


def test_sites_and_sitemaps_are_read_only():
    r, seen = call("gsc-sites", lambda _: httpx.Response(200, json={"siteEntry": [
        {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}]}))
    assert seen[0].method == "GET" and r.keywords[0].metadata["permission_level"] == "siteOwner"
    r, seen = call("gsc-sitemaps", lambda _: httpx.Response(200, json={"sitemap": [
        {"path": "https://example.com/sitemap.xml", "errors": "0", "warnings": "1"}]}))
    assert seen[0].method == "GET" and r.keywords[0].evidence[0].value == 0


def test_inspection_is_indexed_version_only_and_property_bounded():
    def handler(req):
        assert req.url.path == "/v1/urlInspection/index:inspect"
        body = json.loads(req.content)
        assert body["siteUrl"] == "sc-domain:example.com"
        return httpx.Response(200, json={"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Indexed"}}})
    r, seen = call("gsc-inspect", handler, ["https://sub.example.com/a"])
    assert r.keywords[0].metadata["index_status"]["verdict"] == "PASS" and len(seen) == 1
    with pytest.raises(ConfigurationError):
        call("gsc-inspect", lambda _: pytest.fail("no leak"), ["https://example.com.evil.test/a"])
    with pytest.raises(ConfigurationError):
        call("gsc-inspect", lambda _: pytest.fail("no leak"), ["https://example.com/b"], site_url="https://example.com/a/")
    call("gsc-inspect", handler, ["https://example.com/a/"])


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_errors_never_follow_retry_or_echo_tokens(status):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(status, text="SECRET-TOKEN", headers={"Location": "https://evil.example"})
    with pytest.raises(OnlineSourceError) as error:
        call("gsc-query", handler)
    assert "SECRET" not in str(error.value) and len(seen) == 1


def test_bulk_sql_correct_aggregation_parameterization_and_no_execution():
    for table, pos in [("searchdata_site_impression", "sum_top_position"), ("searchdata_url_impression", "sum_position")]:
        sql, meta = c.bulk_sql({**BASE, "table": "my-project.searchconsole."+table, "dimensions": "query,country"})
        assert f"SUM({pos})" in sql and "+ 1 AS position" in sql
        assert "SAFE_DIVIDE(SUM(clicks), SUM(impressions))" in sql
        assert "@site_url" in sql and "NOT is_anonymized_query" in sql
        assert not meta["query_executed"]
    r, seen = call("gsc-bulk-sql", lambda _: pytest.fail("no network"), table="my-project.ds.searchdata_site_impression")
    assert not seen and r.metadata["query_executed"] is False and "sql" in r.metadata


@pytest.mark.parametrize("options", [{"table": "bad`;DROP TABLE x"},
    {"table": "p.d.searchdata_site_impression", "dimensions": "page"},
    {"table": "p.d.searchdata_site_impression", "dimensions": "hour"},
    {"table": "p.d.searchdata_site_impression", "dimensions": ""}])
def test_bulk_sql_validation(options):
    with pytest.raises(ConfigurationError):
        c.bulk_sql({**BASE, **options})
