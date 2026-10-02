"""Provider wire contracts exercised with synthetic responses, never paid calls."""
from __future__ import annotations

import base64
import json
from copy import deepcopy
from urllib.parse import urlencode

import pytest

from keywordmoves.errors import ConfigurationError, KeywordMovesError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.bing_search import BingSearchPlugin
from keywordmoves.online.bing_search_providers import _monthly, _next_first
from keywordmoves.online.common import OnlineSourceError

httpx = pytest.importorskip("httpx")
Q = "paper plane song"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def run(op, payload=None, *, options=None, words=(Q,), handler=None):
    requests = []
    def record(req):
        requests.append(req)
        return handler(req) if handler else httpx.Response(200, json=payload)
    result = BingSearchPlugin(transport=httpx.MockTransport(record)).run(
        PluginRequest(op, keywords=words, options=options or {}), ExecutionContext(None))
    return result, requests


def page(first=1, next_first=None):
    data = {"search_metadata": {"status": "Success"}, "search_parameters": {
        "engine": "bing", "q": Q, "mkt": "en-GB", "first": first, "device": "desktop"},
        "organic_results": [{"position": 1, "link": f"https://example.com/{first}",
                             "title": Q, "snippet": "A song", "tracking_link": "https://bing.com/ck/test"}],
        "related_searches": [{"query": "paper plane lyrics"}],
        "related_questions": [{"question": "Who wrote paper plane song?"}],
        "ads": [{"position": 1, "link": "https://ads.example/"}],
        "search_information": {"total_results": 10000000}}
    if next_first:
        data["serpapi_pagination"] = {"next_link": "https://serpapi.com/search.json?" + urlencode(
            {"engine": "bing", "q": Q, "first": next_first, "api_key": "untrusted-returned-key"})}
    return data


def dfs(rows):
    return {"status_code": 20000, "tasks_error": 0, "cost": 0.015,
            "tasks": [{"status_code": 20000, "id": "synthetic-task", "result": rows}]}


@pytest.mark.parametrize("op", ["serp", "competition", "rank-check", "related", "questions"])
def test_serpapi_operations(op):
    result, requests = run(op, page(), options={"api_key": "synthetic", "market": "en-GB",
                          "target_host": "example.com", "location": "Brighton", "no_cache": True})
    req = requests[0]
    assert req.url.params["engine"] == "bing" and req.url.params["q"] == Q
    assert req.url.params["safeSearch"] == "Moderate"
    assert req.url.params["no_cache"] == "true" and "cc" not in req.url.params
    snap = result.metadata["snapshot"]
    assert len(snap["organic"]) == 1 and snap["organic"][0]["url"] == "https://example.com/1"
    assert snap["features"] == ["ads", "related_questions", "related_searches"]
    assert snap["total_results_estimate"] == 10000000 and not snap["complete_inventory"]
    if op == "serp":
        assert len(result.keywords) == 3
    elif op in {"related", "questions"}:
        assert len(result.keywords) == 1 and result.keywords[0].phrase != Q
    else:
        assert {e.metric: e.value for e in result.keywords[0].evidence}["target_best_observed_organic_rank"] == 1
    assert all(x.score is None for x in result.keywords)


def test_pagination_uses_actual_returned_offsets_not_incremented_tens():
    def handler(req):
        first = int(req.url.params["first"])
        return httpx.Response(200, json=page(first, {1: 5, 5: 15, 15: 34}[first]))
    result, requests = run("serp", options={"api_key": "original", "market": "en-GB", "pages": 3}, handler=handler)
    assert [r.url.params["first"] for r in requests] == ["1", "5", "15"]
    assert all(r.url.params["api_key"] == "original" for r in requests)
    assert "untrusted-returned-key" not in json.dumps(result.to_dict())
    assert [r["rank"] for r in result.metadata["snapshot"]["organic"]] == [1, 5, 15]
    assert result.metadata["snapshot"]["next_first"] == 34


@pytest.mark.parametrize("next_url", [
    "https://evil.example/search?q=x&first=5", "https://serpapi.com/account?q=x&first=5",
    "https://serpapi.com/search?q=other&first=5", "https://serpapi.com/search?q=x&first=5&engine=google",
    "https://serpapi.com/search?q=x&first=0", "https://serpapi.com/search?q=x&first=1",
    "https://serpapi.com/search?q=x&first=a", "https://serpapi.com/search?q=x&first=5&first=10",
    "https://serpapi.com/search?q=x", "https://serpapi.com:8443/search?q=x&first=5",
])
def test_untrusted_continuations_rejected(next_url):
    with pytest.raises(KeywordMovesError):
        _next_first({"serpapi_pagination": {"next": next_url}}, 1, "x")


def test_native_pagination_url_offset_accepted_but_not_followed():
    assert _next_first({"pagination": {"next": "https://www.bing.com/search?q=x&first=15"}}, 5, "x") == 15
    assert _next_first({}, 1, "x") is None


@pytest.mark.parametrize("field,value", [("engine", "google"), ("q", "other"), ("mkt", "en-US"),
                                        ("first", 6), ("device", "mobile")])
def test_serpapi_echo_mismatch_is_not_silently_labelled_bing(field, value):
    data = page()
    data["search_parameters"][field] = value
    with pytest.raises(OnlineSourceError):
        run("serp", data, options={"api_key": "synthetic", "market": "en-GB"})


@pytest.mark.parametrize("payload", [{"error": "synthetic-secret"}, {"search_metadata": {"status": "Processing"}},
    {**page(), "organic_results": None}, {**page(), "organic_results": [{"position": 0}]},
    {**page(), "related_searches": ["not the contract"]}])
def test_invalid_serp_responses(payload):
    with pytest.raises(OnlineSourceError) as exc:
        run("serp", payload, options={"api_key": "synthetic-secret", "market": "en-GB"})
    assert "synthetic-secret" not in str(exc.value)


def test_serp_explicit_empty_is_different_from_missing_schema():
    data = page()
    del data["organic_results"]
    data["search_information"] = {"total_results": 0}
    result, _ = run("serp", data, options={"api_key": "synthetic", "market": "en-GB"})
    assert result.metadata["snapshot"]["organic"] == []
    data["search_information"] = {}
    with pytest.raises(OnlineSourceError):
        run("serp", data, options={"api_key": "synthetic", "market": "en-GB"})


@pytest.mark.parametrize("options", [{"market": "GB"}, {"market": "en-GB", "pages": 3, "max_requests": 2},
    {"market": "en-GB", "device": "phone"}, {"market": "en-GB", "safe_search": "false"},
    {"market": "en-GB", "cc": "gb"}, {"provider": "bing-v7"}])
def test_serp_invalid_settings_before_network(options):
    with pytest.raises(KeywordMovesError):
        run("serp", options={"api_key": "synthetic", **options}, handler=lambda _: pytest.fail("no request"))


def test_dataforseo_serp_rank_group_separate_from_feature_position():
    data = {"keyword": Q, "se_type": "bing", "location_code": 2826, "language_code": "en",
            "items": [{"type": "paid", "rank_group": 1, "url": "https://ad.example/"},
                      {"type": "organic", "rank_group": 1, "rank_absolute": 4, "url": "https://example.com/a", "title": Q},
                      {"type": "related_searches", "items": ["paper planes"]},
                      {"type": "people_also_ask", "items": [{"title": "What is a paper plane?"}]}]}
    result, calls = run("serp", dfs([data]), options={"provider": "dataforseo", "login": "name", "password": "secret",
                        "location_code": 2826, "depth": 20})
    req = calls[0]
    body = json.loads(req.content)[0]
    assert req.url.path == "/v3/serp/bing/organic/live/advanced"
    assert body["depth"] == 20 and body["location_code"] == 2826
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"name:secret").decode()
    rows = result.metadata["snapshot"]["organic"]
    assert len(rows) == 1 and rows[0]["rank"] == 1 and rows[0]["absolute_feature_rank"] == 4
    assert result.metadata["provider_reported_cost_usd"] == 0.015
    assert "name:secret" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("change", [
    {"keyword": "other"}, {"se_type": "google"}, {"location_code": 2840}, {"language_code": "fr"},
    {"device": "mobile"}, {"items": None},
])
def test_dataforseo_context_and_null_not_treated_as_valid(change):
    data = {"keyword": Q, "items": [], **change}
    with pytest.raises(KeywordMovesError):
        run("serp", dfs([data]), options={"provider": "dataforseo", "login": "u", "password": "p", "location_code": 2826})


@pytest.mark.parametrize("payload", [{"status_code": 40000}, dfs([]), {**dfs([]), "tasks_error": 1},
    {"status_code": 20000, "tasks": [{"status_code": 20100}]},
    {"status_code": 20000, "tasks": [{"status_code": 20000, "result": None}]}])
def test_dataforseo_errors_not_empty_serp(payload):
    with pytest.raises(KeywordMovesError):
        run("serp", payload, options={"provider": "dataforseo", "login": "u", "password": "p", "location_code": 2826})


@pytest.mark.parametrize("op,route", [("metrics", "search_volume"), ("ideas", "keywords_for_keywords")])
def test_bing_planning_metrics_are_last_month_not_google_averages(op, route):
    data = [{"keyword": Q, "search_volume": 150, "cpc": 0.5, "competition": 0.9,
             "monthly_searches": [{"year": 2026, "month": 9, "search_volume": 150}]}]
    result, calls = run(op, dfs(data), options={"login": "u", "password": "p", "location_code": 2826,
                                             "search_partners": True, "device": "desktop"})
    assert calls[0].url.path == f"/v3/keywords_data/bing/{route}/live"
    assert json.loads(calls[0].content)[0]["search_partners"] is True
    e = {x.metric: (x.value, x.unit) for x in result.keywords[0].evidence}
    assert e["last_month_search_volume"] == (150, "searches_last_month")
    assert e["paid_competition"] == (0.9, "provider_index_0_1") and "organic_difficulty" not in e
    assert result.keywords[0].metadata["monthly_searches"][0]["month"] == 9


def test_missing_metrics_remain_unknown_and_unreturned_terms_reported():
    result, _ = run("metrics", dfs([{"keyword": Q, "search_volume": 0, "cpc": None}]), words=(Q, "missing"),
                     options={"login": "u", "password": "p", "location_code": 2826})
    values = {e.metric: e.value for e in result.keywords[0].evidence}
    assert values["cpc"] is None and values["last_month_search_volume"] == 0
    assert result.metadata["unreturned_input_terms"] == ["missing"]


@pytest.mark.parametrize("bucket", [None, {}, [{"year": 2026, "month": 13}],
    [{"year": 2026, "month": 9}, {"year": 2026, "month": 9}], [{"year": True, "month": 9}]])
def test_monthly_buckets_validate(bucket):
    if bucket is None:
        assert _monthly(bucket) == []
    else:
        with pytest.raises(KeywordMovesError):
            _monthly(bucket)


def test_url_ideas_and_no_implicit_volume():
    result, requests = run("url-ideas", dfs([{"keyword": Q, "confidence_score": 0.8}]), words=(),
                options={"login": "u", "password": "p", "target": "https://example.com/music", "exclude_brands": True})
    body = json.loads(requests[0].content)[0]
    assert body["target"] == "https://example.com/music" and body["exclude_brands"] is True
    assert [e.metric for e in result.keywords[0].evidence] == ["provider_match_confidence"]


def test_competitor_keywords_use_bing_labs_not_generic_google_database():
    organic = {"keyword_data": {"keyword": Q, "keyword_info": {"search_volume": 300, "competition": 0.1},
                "keyword_properties": {"keyword_difficulty": 20}},
               "ranked_serp_element": {"serp_item": {"type": "organic", "rank_group": 3, "url": "https://example.com/a"}}}
    paid = deepcopy(organic)
    paid["ranked_serp_element"]["serp_item"]["type"] = "paid"
    result, requests = run("competitor-keywords", dfs([{"se_type": "bing", "target": "example.com",
                "items": [organic, paid], "total_count": 2}]), words=(),
                options={"login": "u", "password": "p", "location_code": 2840, "target": "example.com"})
    assert requests[0].url.path == "/v3/dataforseo_labs/bing/ranked_keywords/live"
    assert json.loads(requests[0].content)[0]["filters"][-1] == "organic"
    assert len(result.keywords) == 1
    assert {e.metric: e.value for e in result.keywords[0].evidence}["organic_difficulty"] == 20
    assert result.metadata["next_offset"] is None
    with pytest.raises(ConfigurationError, match="US"):
        run("competitor-keywords", words=(), options={"login": "u", "password": "p", "location_code": 2826,
                     "target": "example.com"}, handler=lambda _: pytest.fail("no request"))


def test_keywordtool_always_targets_bing_and_preserves_own_network(monkeypatch):
    from keywordmoves.online.commercial import KeywordToolPlugin
    captures = []
    def fetch(self, request, http, observed):
        captures.append(request)
        return [], {"test": "synthetic fetch adapter"}, []
    monkeypatch.setattr(KeywordToolPlugin, "fetch", fetch)
    for op in ("metrics", "suggestions"):
        run(op, options={"provider": "keywordtool", "network": "BingOwnedAndOperatedOnly"})
    assert all(r.options["platform"] == "bing" for r in captures)
    assert captures[0].operation == "metrics"


def test_experimental_autocomplete_expansion_and_provenance():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        q = req.url.params["query"]
        return httpx.Response(200, json=[q, [q + " lyrics", q + " chords"]])
    result, requests = run("autocomplete", options={"market": "en-GB", "allow_unofficial": True,
            "expand": "alphabet", "max_queries": 2, "max_requests": 4}, handler=handler)
    assert len(requests) == 4 and all(r.url.host == "api.bing.com" for r in requests)
    assert result.metadata["queried_probes"] == [Q, Q + " a"]
    assert result.metadata["next_probe_offset"] == 2
    assert result.keywords[0].metadata["probe"] == Q
    assert result.keywords[0].evidence[0].metric == "suggestion_position"


@pytest.mark.parametrize("expand", ["none", "questions", "prepositions"])
def test_expansion_modes_are_bounded(expand):
    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, json=[req.url.params["query"], []])
    result, _ = run("autocomplete", options={"market": "en-GB", "allow_unofficial": True, "expand": expand}, handler=handler)
    assert result.metadata["queried_probes"] == [Q]
    assert result.metadata["next_probe_offset"] == (None if expand == "none" else 1)


@pytest.mark.parametrize("o", [{}, {"allow_unofficial": False}, {"allow_unofficial": True, "max_queries": 3, "expand": "alphabet"},
                               {"allow_unofficial": True, "probe_offset": 50}])
def test_autocomplete_optin_and_budget_before_request(o):
    with pytest.raises(ConfigurationError):
        run("autocomplete", options={"market": "en-GB", **o}, handler=lambda _: pytest.fail("no request"))


def test_autocomplete_robots_denial_and_query_echo():
    with pytest.raises(OnlineSourceError):
        run("autocomplete", options={"market": "en-GB", "allow_unofficial": True},
            handler=lambda _: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, json=["wrong", ["term"]])
    with pytest.raises(OnlineSourceError, match="echo"):
        run("autocomplete", options={"market": "en-GB", "allow_unofficial": True}, handler=handler)


@pytest.mark.parametrize("op", ["suggestions", "metrics"])
def test_real_keywordtool_bing_wire(op):
    payload = {"results": {Q: {"string": Q, "volume": 100, "cpc": 0.4, "cmp": 0.1}}, "total_keywords": 1}
    result, calls = run(op, payload, options={"provider": "keywordtool", "api_key": "kt-synthetic", "country": "GB",
                   "language": "en", "location_code": 2826, "metrics": True, "network": "ownedandoperatedonly"})
    body = json.loads(calls[0].content)
    route = "volume" if op == "metrics" else "suggestions"
    assert calls[0].url.path == f"/v2/search/{route}/bing"
    assert body["metrics_network"] == "ownedandoperatedonly"
    assert body["metrics_location"] == [2826] and body["metrics_source"] == "keyword_planner"
    assert result.keywords[0].metadata["platform"] == "bing"
    assert result.keywords[0].metadata["metrics_geography"] == "bing:location:2826"
    assert "kt-synthetic" not in json.dumps(result.to_dict())


def test_keywordtool_request_level_device_breakdown_not_per_keyword():
    payload = {"results": {Q: {"string": Q, "volume": 100, "trend": -0.2, "close_variants": ["paper planes song"],
                              "close_variants_source": Q}},
               "device_breakdown": {"desktop": {"percentage": 40, "count": 40},
                                    "mobile": {"percentage": 60, "count": 60}}}
    result, _ = run("metrics", payload, options={"provider": "keywordtool", "api_key": "synthetic",
                                                "location_code": 2826, "metrics_source": "historical_data"})
    assert result.metadata["aggregate_device_breakdown"]["mobile"]["count"] == 60
    assert "aggregate_device_breakdown" not in result.keywords[0].metadata
    assert result.keywords[0].metadata["provider_trend"] == -0.2
    assert result.keywords[0].metadata["close_variants_source"] == Q


@pytest.mark.parametrize("breakdown", [{"unknown": {}}, {"mobile": {"percentage": 120}}, {"desktop": {"count": -2}}])
def test_invalid_device_breakdown_not_accepted(breakdown):
    with pytest.raises(OnlineSourceError):
        run("metrics", {"results": {}, "device_breakdown": breakdown}, options={"provider": "keywordtool",
                                                   "api_key": "synthetic", "location_code": 2826})
