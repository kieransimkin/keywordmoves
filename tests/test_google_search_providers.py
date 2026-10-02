"""Provider contracts tested with synthetic responses; no paid or live requests."""
import json
from pathlib import Path

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.google_search import GoogleSearchPlugin

httpx = pytest.importorskip("httpx")
FIXTURES = Path(__file__).parent / "fixtures" / "online"


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def call(operation, handler, keywords=("paper planes",), **options):
    seen = []
    def handle(req):
        seen.append(req)
        return handler(req)
    r = GoogleSearchPlugin(transport=httpx.MockTransport(handle)).run(
        PluginRequest(operation, tuple(keywords), options={"api_key": "SECRET", "login": "SECRET",
                      "password": "SECRET", "access_token": "SECRET", "developer_token": "SECRET", **options}), ExecutionContext(None))
    assert "SECRET" not in json.dumps(r.to_dict())
    return r, seen


def result_payload(**changes):
    return {"search_metadata": {"status": "Success"}, "search_information": {"total_results": 900000},
            "organic_results": [{"position": 1, "title": "Paper planes", "link": "https://example.com/a"},
                                {"position": 2, "title": "A tutorial", "link": "https://second.example/a"}],
            "ads": [{"title": "Advertisement", "link": "https://paid.example/"}],
            "related_searches": [{"query": "paper gliders"}],
            "related_questions": [{"question": "How to fold?", "next_page_token": "PAA-NEXT"}],
            "ai_overview": {"page_token": "NEEDS-EXPLICIT-CALL", "references": [{"link": "https://source.example/", "title": "Example"}]}, **changes}


def dfs(result, **changes):
    return {"status_code": 20000, "cost": .02, "tasks": [{"status_code": 20000, "id": "TASK", "result": [result]}], **changes}


def test_serpapi_organic_rank_not_ad_rank_or_approximate_count():
    def handler(req):
        assert req.url.params["engine"] == "google" and req.url.params["q"] == "paper planes"
        assert req.url.params["device"] == "mobile" and req.url.params["gl"] == "uk"
        assert req.url.params["location"] == "Brighton, England, United Kingdom"
        assert "num" not in req.url.params
        return httpx.Response(200, json=result_payload())
    r, seen = call("serp", handler, country="uk", device="mobile", location="Brighton, England, United Kingdom")
    report = r.metadata["serp_report"]
    assert len(seen) == 1 and len(report["organic"]) == 2
    assert "ads" in report["features_returned"] and report["ai_overview_requires_followup"]
    assert report["ai_overview_sources"][0]["url"] == "https://source.example/"
    assert all(i.score is None for i in r.keywords)
    assert any(i.phrase == "paper gliders" for i in r.keywords)
    assert next(i for i in r.keywords if i.relationship == "people-also-ask").metadata["next_page_token"] == "PAA-NEXT"


def test_serp_pagination_reconstructs_fixed_url_and_deduplicates():
    def handler(req):
        start = int(req.url.params["start"])
        return httpx.Response(200, json=result_payload(
            organic_results=[{"position": 1, "link": "https://example.com/a" if not start else "https://third.example/"}],
            serpapi_pagination={"next": "https://do-not-follow.example/?key=SECRET"} if not start else {}))
    r, seen = call("competition", handler, pages=2, country="uk")
    assert [x["position"] for x in r.metadata["serp_report"]["organic"]] == [1, 11]
    assert all(x.url.host == "serpapi.com" for x in seen)
    assert len(r.keywords) == 1


def test_rank_check_requires_target_before_charge():
    with pytest.raises(ConfigurationError):
        call("rank-check", lambda _: pytest.fail("no charge"), country="uk")
    with pytest.raises(ConfigurationError):
        call("rank-check", lambda _: pytest.fail("no charge"), country="uk", target_host="https://example.com")
    r, _ = call("rank-check", lambda _: httpx.Response(200, json=result_payload()), country="uk", target_host="example.com")
    assert next(e.value for e in r.keywords[0].evidence if e.metric == "target_best_observed_organic_rank") == 1


def test_dataforseo_serp_rank_group_features_cost_and_location():
    def handler(req):
        assert req.url.path == "/v3/serp/google/organic/live/advanced"
        body = json.loads(req.content)
        assert len(body) == 1
        assert body[0]["location_code"] == 2826 and body[0]["max_crawl_pages"] == 2
        assert req.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json=dfs({"se_results_count": 40000, "datetime": "2026-10-01", "items": [
            {"type": "paid", "rank_group": 1, "url": "https://ad.example"},
            {"type": "organic", "rank_group": 1, "rank_absolute": 3, "title": "Planes", "url": "https://example.com/a"},
            {"type": "people_also_ask", "items": [{"title": "Why do planes fly?"}]},
            {"type": "related_searches", "items": ["paper gliders"]},
            {"type": "ai_overview", "references": [{"url": "https://ai-source.example"}]}]}))
    r, seen = call("serp", handler, provider="dataforseo", location_code=2826, pages=2)
    report = r.metadata["serp_report"]
    assert len(seen) == 1 and len(report["organic"]) == 1 and report["organic"][0]["absolute_position"] == 3
    assert report["reported_cost_usd"] == .02 and "paid" in report["features_returned"]


@pytest.mark.parametrize("payload", [{}, {"error": "SECRET"}, {"search_metadata": {"status": "Processing"}},
    result_payload(organic_results=[{"link": "https://example.com/"}]),
    result_payload(organic_results=None)])
def test_unexpected_serp_response_is_not_zero_competition(payload):
    with pytest.raises((OnlineSourceError, InputError)) as error:
        call("serp", lambda _: httpx.Response(200, json=payload), country="uk")
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("payload", [{"status_code": 40000}, {"status_code": 20000, "tasks": []},
    {"status_code": 20000, "tasks": [{"status_code": 40000}]}, dfs({}) | {"tasks": [{"status_code": 20000, "result": []}]}])
def test_failed_dataforseo_task_is_not_partial_success(payload):
    with pytest.raises(OnlineSourceError):
        call("serp", lambda _: httpx.Response(200, json=payload), provider="dataforseo", location_code=2826)


def test_autocomplete_expansion_is_bounded_and_only_returned_terms_are_evidence():
    r, seen = call("autocomplete", lambda req: httpx.Response(200, json={"suggestions": [
        {"value": req.url.params["q"] + " tutorial", "relevance": 1000}]}),
        country="uk", expand="alphabet", max_queries=3)
    assert [req.url.params["q"] for req in seen] == ["paper planes", "paper planes a", "paper planes b"]
    assert r.metadata["next_expansion_offset"] == 3 and r.metadata["unqueried_probes"] == 24
    assert all(i.phrase.endswith("tutorial") for i in r.keywords)
    assert all(len(i.evidence) == 1 for i in r.keywords)


@pytest.mark.parametrize("expand", ["none", "questions", "prepositions"])
def test_autocomplete_modes(expand):
    r, _ = call("autocomplete", lambda req: httpx.Response(200, json={"suggestions": []}), country="uk", expand=expand, max_queries=1)
    assert not r.keywords and r.metadata["probes"] == ["paper planes"]


def test_web_autocomplete_has_optin_robots_and_exact_echo():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert req.url.params["client"] == "firefox" and "gl" not in req.url.params
        return httpx.Response(200, json=[req.url.params["q"], ["paper planes diy"]])
    r, seen = call("autocomplete", handler, provider="web", allow_unofficial=True)
    assert len(seen) == 2 and r.keywords[0].evidence[0].geography is None
    with pytest.raises(ConfigurationError):
        call("autocomplete", lambda _: pytest.fail("no implicit web call"), provider="web")
    with pytest.raises(OnlineSourceError):
        call("autocomplete", lambda req: httpx.Response(200, text="User-agent: *\nDisallow: /"), provider="web", allow_unofficial=True)
    with pytest.raises(ConfigurationError):
        call("autocomplete", lambda _: pytest.fail("budget"), country="uk", expand="alphabet", max_queries=6)


def test_paa_first_page_and_explicit_continuation_no_recursive_requests():
    r, seen = call("questions", lambda _: httpx.Response(200, json=result_payload()), country="uk")
    assert len(r.keywords) == 1 and len(seen) == 1
    def handler(req):
        assert req.url.params["engine"] == "google_related_questions" and req.url.params["next_page_token"] == "NEXT"
        return httpx.Response(200, json={"related_questions": [{"question": "Why?", "next_page_token": "MORE"}]})
    r, seen = call("questions", handler, next_page_token="NEXT")
    assert len(seen) == 1 and r.keywords[0].metadata["next_page_token"] == "MORE"


def test_custom_search_legacy_scope_and_budget():
    with pytest.raises(ConfigurationError):
        call("custom-search", lambda _: pytest.fail("no new customer call"), country="uk", cx="configured")
    def handler(req):
        assert req.url.host == "customsearch.googleapis.com" and req.url.params["cx"] == "configured"
        return httpx.Response(200, json={"items": [{"title": "Planes", "link": "https://example.com/a"}],
                                       "searchInformation": {"totalResults": "40"}})
    r, _ = call("custom-search", handler, country="uk", cx="configured", existing_customer=True)
    assert r.metadata["serp_report"]["engine"] == "custom-search:configured"
    assert "2027-01-01" in r.notes[0]


def test_backlinks_not_google_pagerank_or_domain_equivalence():
    def handler(req):
        assert req.url.path == "/v3/backlinks/summary/live"
        assert json.loads(req.content)[0]["target"] == "https://example.com/a"
        return httpx.Response(200, json=dfs({"backlinks": 100, "referring_domains": 30,
                                           "referring_main_domains": 20, "rank": 200}))
    r, _ = call("backlinks", handler, target="https://example.com/a")
    e = {e.metric: e.value for e in r.keywords[0].evidence}
    assert e["referring_domains"] == 30 and e["referring_main_domains"] == 20
    assert "not_google" in r.keywords[0].evidence[-1].unit


def test_competitor_keywords_excludes_paid_and_keeps_native_difficulty():
    def handler(req):
        body = json.loads(req.content)[0]
        assert body["item_types"] == ["organic"] and body["offset"] == 5
        keyword = {"keyword": "paper planes", "keyword_info": {"search_volume": 500}, "keyword_properties": {"keyword_difficulty": 30}}
        return httpx.Response(200, json=dfs({"items": [
            {"keyword_data": keyword, "ranked_serp_element": {"serp_item": {"type": "organic", "rank_group": 2, "url": "https://example.com/a"}}},
            {"keyword_data": keyword, "ranked_serp_element": {"serp_item": {"type": "paid"}}}]}))
    r, _ = call("competitor-keywords", handler, target="example.com", location_code=2826, offset=5)
    assert len(r.keywords) == 1 and r.keywords[0].metadata["ranking_url"] == "https://example.com/a"
    assert r.keywords[0].evidence[-1].value == 30


@pytest.mark.parametrize("data_type,payload", [
    ("TIMESERIES", {"interest_over_time": {"timeline_data": [{"date": "today", "values": [{"value": "<1", "extracted_value": 0}]}]}}),
    ("RELATED_QUERIES", {"related_queries": {"top": [{"query": "gliders", "value": "100"}], "rising": [{"query": "planes", "value": "Breakout"}]}}),
    ("RELATED_TOPICS", {"related_topics": {"rising": [{"topic": {"title": "Flight"}, "value": "50%"}]}}),
    ("GEO_MAP_0", {"interest_by_region": [{"location": "England", "value": "100", "extracted_value": 100}]})])
def test_trends_preserves_native_relative_units_and_censoring(data_type, payload):
    def handler(req):
        assert req.url.params["engine"] == "google_trends" and req.url.params["data_type"] == data_type
        return httpx.Response(200, json=payload)
    r, _ = call("trends", handler, country="GB", data_type=data_type)
    assert r.metadata["search_property"] == "web" and "native_trends" in r.metadata
    assert not any(e.unit == "searches_per_month" for i in r.keywords for e in i.evidence)


@pytest.mark.parametrize("kind,field,keywords", [("url", "urlSeed", ()), ("site", "siteSeed", ()), ("keyword-url", "keywordAndUrlSeed", ("planes",))])
def test_google_ads_url_seeds_and_micro_currency(kind, field, keywords):
    def handler(req):
        body = json.loads(req.content)
        assert field in body and body["keywordPlanNetwork"] == "GOOGLE_SEARCH"
        assert body["geoTargetConstants"] == ["geoTargetConstants/2826"]
        assert "/v25/" in req.url.path
        return httpx.Response(200, json={"results": [{"text": "paper plane", "keywordIdeaMetrics": {
            "avgMonthlySearches": "100", "competition": "LOW", "lowTopOfPageBidMicros": "250000"}}], "nextPageToken": "NEXT"})
    r, _ = call("ads-url-ideas", handler, keywords, seed_type=kind, url="https://example.com/", customer_id="123-456", api_version="v25", location_codes="2826")
    assert r.metadata["next_page_token"] == "NEXT"
    assert r.keywords[0].evidence[-2].unit == "account_currency_micros"


def test_pagespeed_is_lab_context_not_ranking_score():
    payload = {"lighthouseResult": {"categories": {"performance": {"score": .8}}, "audits": {
        "largest-contentful-paint": {"numericValue": 2500, "numericUnit": "millisecond"}}, "lighthouseVersion": "test"}}
    r, _ = call("pagespeed", lambda _: httpx.Response(200, json=payload), url="https://example.com/a")
    assert "not_ranking" in r.keywords[0].evidence[0].unit
    assert next(e.value for e in r.keywords[0].evidence if e.metric == "lab_largest_contentful_paint") == 2500


ROUTES = [
    ("ideas", "google-ads", "google_ads_ideas.json", {"customer_id": "123", "api_version": "v25", "location_codes": "2826"}),
    ("metrics", "google-ads", "google_ads_metrics.json", {"customer_id": "123", "api_version": "v25", "location_codes": "2826"}),
    ("ideas", "dataforseo", "dataforseo.json", {"location_code": 2826}),
    ("suggestions", "dataforseo", "dataforseo.json", {"location_code": 2826}),
    ("metrics", "dataforseo", "dataforseo.json", {"location_code": 2826}),
    ("suggestions", "keywordtool", "keywordtool.json", {"country": "GB"}),
    ("questions", "keywordtool", "keywordtool.json", {"country": "GB"}),
    ("metrics", "keywordtool", "keywordtool.json", {"location_code": 2826}),
    ("ideas", "semrush", "semrush.csv", {"database": "uk"}),
    ("suggestions", "semrush", "semrush.csv", {"database": "uk"}),
    ("metrics", "semrush", "semrush.csv", {"database": "uk"}),
    ("suggestions", "ahrefs", "ahrefs.json", {"country": "GB"}),
    ("questions", "ahrefs", "ahrefs.json", {"country": "GB"}),
    ("metrics", "keywords-everywhere", "keywords_everywhere.json", {"country": "gb", "currency": "gbp"}),
    ("questions", "alsoasked", "alsoasked.json", {"country": "gb"}),
]


@pytest.mark.parametrize("operation,provider,fixture,options", ROUTES)
def test_composed_provider_routes_preserve_existing_contracts(operation, provider, fixture, options):
    def handler(req):
        if provider == "keywordtool":
            body = json.loads(req.content)
            assert req.url.path.endswith("/google")
            if operation == "questions":
                assert body["type"] == "questions"
            if operation == "metrics":
                assert body["metrics_network"] == "googlesearch"
        return httpx.Response(200, text=(FIXTURES/fixture).read_text())
    r, seen = call(operation, handler, provider=provider, **options)
    assert len(seen) == 1 and r.metadata["provider"] == provider
    assert r.metadata["live_query_performed"] and all(i.score is None for i in r.keywords)


@pytest.mark.parametrize("operation,options", [
    ("serp", {"provider": "missing"}), ("serp", {"provider": "dataforseo", "device": "tablet", "location_code": 2826}),
    ("metrics", {"provider": "ahrefs"}), ("suggestions", {"provider": "openai"}),
    ("autocomplete", {"expand": "invalid"}), ("pagespeed", {"url": "https://example.com", "strategy": "fast"}),
    ("ads-url-ideas", {"seed_type": "site", "url": "https://example.com", "customer_id": "123", "api_version": "v25", "location_codes": "2826"}),
    ("serp", {"country": "uk", "top_n": 0}), ("gsc-opportunities", {"target_ctr": 5}),
    ("serp", {"country": "uk", "sort_order": "weird"})])
def test_invalid_options_fail_without_request(operation, options):
    with pytest.raises(ConfigurationError):
        call(operation, lambda _: pytest.fail("must validate before charge"), **options)


def test_page_audit_public_transport_robots_and_no_followup(monkeypatch):
    from keywordmoves.online import google_search
    monkeypatch.setattr(google_search, "public_url", lambda _: "example.com")
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert req.url.path == "/article"
        return httpx.Response(200, text='<html><head><title>Paper planes</title></head><body><h1>Paper planes</h1><p>Paper planes fly.</p><a href="https://external.example/">More</a></body></html>')
    r, seen = call("page-audit", handler, url="https://example.com/article")
    assert len(seen) == 2 and r.metadata["live_query_performed"]
    assert r.metadata["page_audit"]["visible_word_count"] == 6


def test_unsupported_inputs_do_not_trigger_remote_requests(tmp_path):
    with pytest.raises(ConfigurationError):
        GoogleSearchPlugin(transport=httpx.MockTransport(lambda _: pytest.fail("no network"))).run(
            PluginRequest("serp", ("planes",), (tmp_path/"anything",), {"country": "uk"}), ExecutionContext(None))


def test_web_autocomplete_wrong_query_echo_fails():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, json=["different", ["not relevant"]])
    with pytest.raises(OnlineSourceError, match="did not match"):
        call("autocomplete", handler, provider="web", allow_unofficial=True)


def test_pagespeed_runtime_error_is_not_zero_performance():
    with pytest.raises(OnlineSourceError):
        call("pagespeed", lambda _: httpx.Response(200, json={"lighthouseResult": {"runtimeError": {"code": "FAILED"}}}), url="https://example.com")


def test_custom_search_two_pages_and_explicit_budget():
    def handler(req):
        start = int(req.url.params["start"])
        return httpx.Response(200, json={"items": [{"link": f"https://example.com/{start}", "title": "test"}],
                                       "queries": {"nextPage": [{}]} if start == 1 else {}})
    r, seen = call("custom-search", handler, country="uk", cx="configured", existing_customer=True, pages=2)
    assert [row["position"] for row in r.metadata["serp_report"]["organic"]] == [1, 11]
    assert len(seen) == 2
    with pytest.raises(ConfigurationError):
        call("custom-search", lambda _: pytest.fail("no network"), country="uk", cx="configured", existing_customer=True, pages=6)
