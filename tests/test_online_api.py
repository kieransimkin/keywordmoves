"""Provider contract tests with real HTTPX serialization and synthetic responses."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import factories
from keywordmoves.online.common import ConfigurationError, OnlineSourceError

httpx = pytest.importorskip("httpx")

FIXTURES = Path(__file__).parent / "fixtures" / "online"
CREDENTIALS = {
    "GOOGLE_ADS_ACCESS_TOKEN": "secret-access", "GOOGLE_ADS_DEVELOPER_TOKEN": "secret-developer",
    "SEARCH_CONSOLE_ACCESS_TOKEN": "secret-console", "DATAFORSEO_LOGIN": "secret-login",
    "DATAFORSEO_PASSWORD": "secret-password", "SEMRUSH_API_KEY": "secret-semrush",
    "AHREFS_API_KEY": "secret-ahrefs", "KEYWORDTOOL_API_KEY": "secret-keywordtool",
    "KEYWORDS_EVERYWHERE_API_KEY": "secret-everywhere", "ALSOASKED_API_KEY": "secret-alsoasked",
    "SERPAPI_API_KEY": "secret-serpapi", "BRAVE_SEARCH_API_KEY": "secret-brave",
}

# Every official API operation is covered, including the different payload key names.
CASES = [
    ("google-ads", "ideas", "google_ads_ideas", {"customer_id":"123-456-7890", "location_codes":"2826"}, "googleads.googleapis.com", "/v25/customers/1234567890:generateKeywordIdeas"),
    ("google-ads", "metrics", "google_ads_metrics", {"customer_id":"1234567890", "location_codes":"2826"}, "googleads.googleapis.com", "/v25/customers/1234567890:generateKeywordHistoricalMetrics"),
    ("search-console", "queries", "search_console", {"site_url":"https://example.org/", "start_date":"2026-09-01", "end_date":"2026-09-30"}, "www.googleapis.com", "/webmasters/v3/sites/https://example.org//searchAnalytics/query"),
    *[("dataforseo", op, "dataforseo", {"location_code":2826}, "api.dataforseo.com", f"/v3/dataforseo_labs/google/{route}/live") for op,route in [("ideas","keyword_ideas"),("suggestions","keyword_suggestions"),("metrics","keyword_overview")]],
    *[("semrush", op, "semrush", {"database":"uk"}, "api.semrush.com", "/") for op in ("related","broad-match","metrics")],
    *[("ahrefs", op, "ahrefs", {"country":"gb"}, "api.ahrefs.com", "/v3/keywords-explorer/matching-terms") for op in ("matching-terms","questions")],
    ("keywordtool", "suggestions", "keywordtool", {"country":"GB"}, "api.keywordtool.io", "/v2/search/suggestions/google"),
    ("keywordtool", "metrics", "keywordtool", {"location_code":2826}, "api.keywordtool.io", "/v2/search/volume/google"),
    ("keywords-everywhere", "metrics", "keywords_everywhere", {"country":"gb"}, "api.keywordseverywhere.com", "/v1/get_keyword_data"),
    ("alsoasked", "questions", "alsoasked", {"country":"gb"}, "alsoaskedapi.com", "/v1/search"),
    ("serpapi", "autocomplete", "serpapi_autocomplete", {"country":"uk"}, "serpapi.com", "/search.json"),
    ("serpapi", "questions", "serpapi_questions", {"country":"uk"}, "serpapi.com", "/search.json"),
    ("serpapi", "related-searches", "serpapi_related", {"country":"uk"}, "serpapi.com", "/search.json"),
    ("brave-suggest", "suggestions", "brave", {"country":"GB"}, "api.search.brave.com", "/res/v1/suggest/search"),
    *[("datamuse", op, "datamuse", {}, "api.datamuse.com", "/sug" if op == "suggestions" else "/words") for op in ("related","synonyms","suggestions")],
    ("wikipedia", "suggestions", "wikipedia", {}, "en.wikipedia.org", "/w/api.php"),
]


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for key, value in CREDENTIALS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def response_for(name):
    path = FIXTURES / (name + (".csv" if name == "semrush" else ".json"))
    return httpx.Response(200, content=path.read_bytes())


def call(name, operation, options, handler, keywords=("paper planes",)):
    plugin = factories()[name](transport=httpx.MockTransport(handler))
    return plugin.run(PluginRequest(operation, keywords=keywords, options=options), ExecutionContext(None))


@pytest.mark.parametrize("name,operation,fixture,options,host,path", CASES)
def test_all_documented_api_operations(name, operation, fixture, options, host, path):
    sent = []

    def handler(request):
        sent.append(request)
        assert request.url.host == host
        assert request.url.path == path
        assert request.url.scheme == "https"
        assert "KeywordMoves" in request.headers["User-Agent"]
        return response_for(fixture)

    result = call(name, operation, options, handler)
    assert result.keywords
    assert result.metadata["access"] == "official-api"
    assert result.metadata["request_count"] == 1
    assert all(k.score is None for k in result.keywords)
    serialized = json.dumps(result.to_dict())
    assert all(value not in serialized for value in CREDENTIALS.values())
    assert all(e.observed_at for k in result.keywords for e in k.evidence)
    assert len(sent) == 1
    req = sent[0]
    payload = json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") else None
    if name == "google-ads":
        assert req.headers["developer-token"] == "secret-developer"
        assert req.headers["Authorization"] == "Bearer secret-access"
        assert payload["geoTargetConstants"] == ["geoTargetConstants/2826"]
        assert (payload["keywordSeed"]["keywords"] if operation == "ideas" else payload["keywords"]) == ["paper planes"]
    elif name == "search-console":
        assert "%3A%2F%2F" in str(req.url)
        assert payload["dimensions"] == ["query"]
        assert payload["dimensionFilterGroups"][0]["filters"][0]["operator"] == "contains"
    elif name == "dataforseo":
        assert req.headers["Authorization"].startswith("Basic ")
        assert isinstance(payload, list)
        assert ("keyword" if operation == "suggestions" else "keywords") in payload[0]
    elif name == "semrush":
        assert req.url.params["key"] == "secret-semrush"
        assert req.url.params["export_columns"] == "Ph,Nq,Cp,Co"
    elif name == "ahrefs":
        assert req.headers["Authorization"] == "Bearer secret-ahrefs"
        assert req.url.params["terms"] == ("questions" if operation == "questions" else "all")
        assert next(e for e in result.keywords[0].evidence if e.metric == "cpc").unit == "USD_cents"
    elif name == "keywordtool":
        assert payload["apikey"] == "secret-keywordtool"
        assert "secret-keywordtool" not in str(req.url)
        assert isinstance(payload["keyword"], list) == (operation == "metrics")
    elif name == "keywords-everywhere":
        assert parse_qs(req.content.decode())["kw[]"] == ["paper planes"]
        assert req.headers["Authorization"] == "Bearer secret-everywhere"
    elif name == "alsoasked":
        assert req.headers["X-Api-Key"] == "secret-alsoasked"
        assert payload["async"] is False
        assert payload["notify_webhooks"] is False
        assert result.keywords[1].metadata["parent_question"] == "How do paper planes fly?"
    elif name == "serpapi":
        assert req.url.params["api_key"] == "secret-serpapi"
        assert req.url.params["engine"] == ("google_autocomplete" if operation == "autocomplete" else "google")
    elif name == "brave-suggest":
        assert req.headers["X-Subscription-Token"] == "secret-brave"
        assert req.url.params["count"] == "20"
    elif name == "datamuse":
        assert {"related":"ml", "synonyms":"rel_syn", "suggestions":"s"}[operation] in req.url.params


@pytest.mark.parametrize("name,operation,fixture,options,host,path", CASES)
def test_provider_http_failure_does_not_leak_credentials(name, operation, fixture, options, host, path):
    with pytest.raises(OnlineSourceError, match="HTTP 401") as exc:
        call(name, operation, options, lambda _: httpx.Response(401, text="secret-body " + " ".join(CREDENTIALS.values())))
    assert "secret-" not in str(exc.value)
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


@pytest.mark.parametrize("name,operation,fixture,options,host,path", CASES)
def test_html_challenge_is_not_silently_empty(name, operation, fixture, options, host, path):
    with pytest.raises(OnlineSourceError):
        call(name, operation, options, lambda _: httpx.Response(200, text="<html>challenge</html>"))


def test_metrics_preserve_zero_and_null():
    result = call("google-ads", "ideas", {"customer_id":"123", "location_codes":"2826"}, lambda _: response_for("google_ads_ideas"))
    evidence = {e.metric:e.value for e in result.keywords[0].evidence}
    assert evidence["avg_monthly_searches"] == 0
    assert evidence["high_top_of_page_bid"] is None


def test_key_precedence_and_blank_does_not_fall_back():
    def handler(req):
        assert req.url.params["key"] == "explicit-key"
        return response_for("semrush")
    call("semrush", "related", {"database":"uk", "api_key":"explicit-key"}, handler)
    with pytest.raises(ConfigurationError):
        call("semrush", "related", {"database":"uk", "api_key":""}, lambda _: pytest.fail("must not call"))


def test_google_pagination_and_request_budget():
    calls = []
    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        data = json.loads((FIXTURES / "google_ads_ideas.json").read_text())
        data["nextPageToken"] = "next-page"
        return httpx.Response(200, json=data)
    options = {"customer_id":"123", "location_codes":"2826", "page_size":1, "limit":3, "max_pages":2}
    result = call("google-ads", "ideas", options, handler)
    assert len(calls) == 2
    assert calls[1]["pageToken"] == "next-page"
    assert result.metadata["next_page_token"] == "next-page"
    with pytest.raises(OnlineSourceError, match="budget"):
        call("google-ads", "ideas", {**options, "max_requests":1}, handler)


def test_search_console_page_boundaries_and_scope():
    calls = []
    def handler(req):
        calls.append(json.loads(req.content))
        return response_for("search_console") if len(calls) == 1 else httpx.Response(200, json={})
    options = {"site_url":"sc-domain:example.org", "start_date":"2026-09-01", "end_date":"2026-09-30",
               "country":"gbr", "page_size":1, "max_pages":2}
    result = call("search-console", "queries", options, handler, keywords=())
    assert calls[1]["startRow"] == 1
    assert result.metadata["next_start_row"] is None
    assert result.keywords[0].metadata["date_timezone"] == "America/Los_Angeles"
    assert result.keywords[0].evidence[0].geography == "gbr"


def test_task_failure_is_not_ignored():
    payload = {"status_code":20000, "tasks":[{"status_code":40200, "status_message":"secret-password"}]}
    with pytest.raises(OnlineSourceError, match="task"):
        call("dataforseo", "ideas", {"location_code":2826}, lambda _: httpx.Response(200,json=payload))


def test_keywordtool_partial_notice_and_limit_are_visible():
    payload = json.loads((FIXTURES/"keywordtool.json").read_text())
    payload["notice"] = {"code":10, "message":"partial"}
    payload["results"]["paper planes"]["volume"] = None
    result = call("keywordtool", "suggestions", {"country":"GB", "metrics":True, "location_code":2826}, lambda _: httpx.Response(200,json=payload))
    assert result.metadata["partial_notice"] is True
    assert "partial" in result.metadata["completeness"]
    assert next(e for e in result.keywords[0].evidence if e.metric=="estimated_search_volume").value is None


def test_semrush_no_data_differs_from_error():
    result = call("semrush", "related", {"database":"uk"}, lambda _: httpx.Response(200,text="ERROR 50 :: NOTHING FOUND"))
    assert not result.keywords
    with pytest.raises(OnlineSourceError):
        call("semrush", "related", {"database":"uk"}, lambda _: httpx.Response(200,text="ERROR 120 :: KEY secret-semrush"))


def test_alsoasked_pending_does_not_resubmit_or_emit_empty_success():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={"status":"pending"})
    with pytest.raises(OnlineSourceError, match="not complete"):
        call("alsoasked", "questions", {"country":"gb"}, handler)
    assert len(calls) == 1


@pytest.mark.parametrize("name,operation,options", [
    ("google-ads", "ideas", {"customer_id":"../", "location_codes":"2826"}),
    ("google-ads", "ideas", {"customer_id":"123", "location_codes":"wrong"}),
    ("dataforseo", "ideas", {}),
    ("ahrefs", "matching-terms", {"country":"../../"}),
    ("keywordtool", "metrics", {}),
    ("wikipedia", "suggestions", {"language":"en.attacker.test"}),
    ("search-console", "queries", {"site_url":"sc-domain:example.org", "start_date":"2026-09-30", "end_date":"2026-09-01"}),
])
def test_invalid_options_never_make_network_requests(name,operation,options):
    with pytest.raises(ConfigurationError):
        call(name, operation, options, lambda _: pytest.fail("unexpected request"))


def test_multi_keyword_batch_is_not_silently_truncated():
    captured = []
    def handler(req):
        captured.append(parse_qs(req.content.decode()))
        return response_for("keywords_everywhere")
    call("keywords-everywhere", "metrics", {"country":"gb"}, handler, keywords=("paper planes","paper folding"))
    assert captured[0]["kw[]"] == ["paper planes","paper folding"]
    with pytest.raises(ConfigurationError):
        call("semrush", "related", {"database":"uk"}, handler, keywords=("one","two"))


@pytest.mark.parametrize("platform", ["google","bing","youtube","perplexity","amazon","ebay",
                                     "app-store","play-store","instagram","twitter","reddit",
                                     "pinterest","etsy","tiktok","naver","google-trends"])
def test_keywordtool_all_platform_suggestion_routes(platform):
    def handler(req):
        payload = json.loads(req.content)
        assert req.url.path == f"/v2/search/suggestions/{platform}"
        assert payload["country"] == "GB"
        assert payload["metrics"] is False
        assert payload["type"] == {"instagram":"hashtags","google-trends":"top"}.get(platform,"suggestions")
        return response_for("keywordtool")
    call("keywordtool","suggestions",{"platform":platform,"country":"GB"},handler)


@pytest.mark.parametrize("platform", ["google","bing","youtube","perplexity","amazon","ebay",
                                     "app-store","play-store","instagram","twitter","reddit",
                                     "pinterest","etsy","tiktok","naver"])
def test_keywordtool_metrics_use_platform_specific_localization(platform):
    def handler(req):
        payload = json.loads(req.content)
        assert req.url.path == f"/v2/search/volume/{platform}"
        if platform in {"google","bing"}:
            assert payload["metrics_location"] == [188 if platform == "bing" else 2826]
            assert "country" not in payload
            assert "metrics_network" in payload
        else:
            assert payload["country"] == "GB"
            assert "metrics_location" not in payload
            assert "metrics_network" not in payload
        return response_for("keywordtool")
    call("keywordtool","metrics",{"platform":platform,"country":"GB", "location_code":188 if platform=="bing" else 2826},handler)


def test_keywordtool_different_suggestion_and_volume_geographies():
    result = call("keywordtool","suggestions",{"country":"US","metrics":True,"location_code":2826},lambda _: response_for("keywordtool"))
    scopes={e.metric:e.geography for e in result.keywords[0].evidence}
    assert scopes["returned_order"] == "US"
    assert scopes["estimated_search_volume"] == "google:location:2826"


@pytest.mark.parametrize("options", [{"platform":"instagram","suggestion_type":"questions","country":"GB"},
                                     {"platform":"google-trends","metrics":True,"country":"GB"}])
def test_keywordtool_unsupported_platform_combinations_fail_before_request(options):
    with pytest.raises(ConfigurationError):
        call("keywordtool","suggestions",options,lambda _: pytest.fail("must not request"))
