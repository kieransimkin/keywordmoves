"""Real HTTPX request serialization with synthetic Bing Webmaster replies only."""
from __future__ import annotations

import json

import pytest

from keywordmoves.errors import ConfigurationError, KeywordMovesError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import bing_webmaster as bwt
from keywordmoves.online.bing_search import BingSearchPlugin
from keywordmoves.online.common import OnlineSourceError

httpx = pytest.importorskip("httpx")
SITE = "https://example.com/"
PAGE = SITE + "music/"
SECRET = "synthetic-key-not-a-credential"
DATE = "/Date(1316156400000-0700)/"
ROW = {"Query": "paper planes", "Date": DATE, "Clicks": 15, "Impressions": 100,
       "AvgClickPosition": 18, "AvgImpressionPosition": 17}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def run(op, payload, *, options=None, words=(), handler=None):
    calls = []

    def transport(req):
        calls.append(req)
        return handler(req) if handler else httpx.Response(200, json={"d": payload})

    result = BingSearchPlugin(transport=httpx.MockTransport(transport)).run(
        PluginRequest(op, keywords=words, options={"api_key": SECRET, **(options or {})}),
        ExecutionContext(None),
    )
    return result, calls


@pytest.mark.parametrize("op,opts,words,payload", [
    ("bwt-sites", {}, (), [{"Url": SITE, "IsVerified": True, "AuthenticationCode": SECRET}]),
    ("bwt-queries", {"site_url": SITE}, (), [ROW]),
    ("bwt-pages", {"site_url": SITE}, (), [{**ROW, "Query": PAGE}]),
    ("bwt-page-queries", {"site_url": SITE, "page_url": PAGE}, (), [ROW]),
    ("bwt-query-pages", {"site_url": SITE}, ("paper planes",), [{**ROW, "Query": PAGE}]),
    ("bwt-query-history", {"site_url": SITE}, ("paper planes",), [ROW]),
    ("bwt-query-page-detail", {"site_url": SITE, "page_url": PAGE}, ("paper planes",),
     [{"Date": DATE, "Clicks": 2, "Impressions": 30, "Position": 3}]),
    ("bwt-traffic", {"site_url": SITE}, (), [ROW]),
    ("bwt-keyword", {"country": "GB", "language": "en", "start_date": "2026-09-01", "end_date": "2026-09-30"},
     ("paper planes",), {"Query": "paper planes", "Impressions": 230, "BroadImpressions": 800}),
    ("bwt-related", {"country": "GB", "language": "en", "start_date": "2026-09-01", "end_date": "2026-09-30"},
     ("paper planes",), [{"Query": "paper aircraft", "Impressions": 150, "BroadImpressions": 260}]),
    ("bwt-keyword-history", {"country": "GB", "language": "en"}, ("paper planes",),
     [{"Query": "paper planes", "Date": DATE, "Impressions": 0, "BroadImpressions": None}]),
    ("bwt-links", {"site_url": SITE}, (), {"TotalPages": 1, "Links": [{"Url": PAGE, "Count": 10}]}),
    ("bwt-url-links", {"site_url": SITE, "link": PAGE}, (),
     {"TotalPages": 1, "Details": [{"Url": "https://ref.example/", "AnchorText": "Paper planes"}]}),
    ("bwt-sitemaps", {"site_url": SITE}, (), [{"Url": SITE + "sitemap.xml", "Compressed": False,
       "LastCrawled": DATE, "Submitted": DATE, "FileSize": 1024, "Status": "Ok", "Type": 1, "UrlCount": 6}]),
    ("bwt-url-info", {"site_url": SITE, "url": PAGE}, (), {"Url": PAGE, "IsPage": True,
       "HttpStatus": 200, "DiscoveryDate": DATE, "LastCrawledDate": DATE, "AnchorCount": 2}),
    ("bwt-crawl", {"site_url": SITE}, (), [{"Date": DATE, "Code2xx": 4, "Code301": 1,
       "Code4xx": 0, "InIndex": 20, "CrawlErrors": 0}]),
    ("bwt-crawl-issues", {"site_url": SITE}, (), [{"Url": PAGE, "HttpCode": 404, "Issues": 1, "InLinks": 3}]),
])
def test_all_native_routes(op, opts, words, payload):
    result, calls = run(op, payload, options=opts, words=words)
    assert len(calls) == 1
    req = calls[0]
    assert req.method == "GET"
    assert req.url.host == "ssl.bing.com"
    assert req.url.path == "/webmaster/api.svc/json/" + bwt.METHODS[op][0]
    assert req.url.params["apikey"] == SECRET
    assert "startRow" not in req.url.params and "device" not in req.url.params
    assert result.metadata["requests_made"] == 1
    assert SECRET not in json.dumps(result.to_dict())
    assert result.plugin == "bing-search" and all(c.score is None for c in result.keywords)
    if bwt.METHODS[op][1] in bwt.PERFORMANCE:
        report = result.metadata["report"]
        assert report["context"]["granularity"] == "native-buckets"
        assert report["context"]["start_date"] is None
        assert report["rows"][0]["date"] == "2011-09-16T00:00:00-07:00"
    if op == "bwt-query-page-detail":
        row = result.metadata["report"]["rows"][0]
        assert row["position_bucket"] == 3
        assert row["average_click_position"] is None
        assert row["average_impression_position"] is None
    if op == "bwt-pages":
        assert result.keywords[0].phrase == PAGE
    if op == "bwt-keyword-history":
        metrics = {e.metric: e.value for e in result.keywords[0].evidence}
        assert metrics["keyword_impressions"] == 0 and metrics["broad_keyword_impressions"] is None


def test_quoted_parameters_and_explicit_plain_mode():
    for encoding, expected in [("documented", '"paper planes"'), ("plain", "paper planes")]:
        _, calls = run("bwt-query-pages", [{**ROW, "Query": PAGE}], words=("paper planes",),
                       options={"site_url": SITE, "query_encoding": encoding})
        assert calls[0].url.params["query"] == expected
        assert calls[0].url.params["siteUrl"] == SITE


def test_oauth_and_environment_keys_are_read_per_call(monkeypatch):
    monkeypatch.setenv("BING_WEBMASTER_ACCESS_TOKEN", "synthetic-oauth-token")
    result, calls = run("bwt-sites", [], options={"auth": "oauth"})
    assert calls[0].url.host == "www.bing.com"
    assert calls[0].headers["Authorization"] == "Bearer synthetic-oauth-token"
    assert "apikey" not in calls[0].url.params
    assert "synthetic-oauth-token" not in json.dumps(result.to_dict())
    plugin = BingSearchPlugin(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"d": []})))
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "from-environment")
    assert plugin.run(PluginRequest("bwt-sites"), ExecutionContext(None)).metadata["requests_made"] == 1
    with pytest.raises(ConfigurationError):
        plugin.run(PluginRequest("bwt-sites", options={"api_key": ""}), ExecutionContext(None))


def test_link_pagination_bounded_and_never_complete_inventory():
    def handler(req):
        n = int(req.url.params["page"])
        return httpx.Response(200, json={"d": {"TotalPages": 3,
                               "Links": [{"Url": SITE + str(n), "Count": n}]}})
    result, calls = run("bwt-links", None, options={"site_url": SITE, "pages": 2}, handler=handler)
    assert [r.url.params["page"] for r in calls] == ["0", "1"]
    assert result.metadata["next_page_index"] == 2
    assert result.metadata["complete_inventory"] is False
    assert len(result.metadata["records"]) == 2
    result, calls = run("bwt-links", None, options={"site_url": SITE, "pages": 2, "page_index": 2}, handler=handler)
    assert len(calls) == 1 and result.metadata["next_page_index"] is None


@pytest.mark.parametrize("options", [{"pages": 3, "max_requests": 2}, {"page_index": 32767, "pages": 2},
                                      {"page_index": -1}])
def test_invalid_link_pagination_before_network(options):
    with pytest.raises(ConfigurationError):
        run("bwt-links", None, options={"site_url": SITE, **options},
            handler=lambda _: pytest.fail("no request expected"))


@pytest.mark.parametrize("payload", [{"ErrorCode": 5, "Message": SECRET}, {"d": {"error": SECRET}},
                                      {"bad": []}, {"d": "not-list"}])
def test_native_error_envelopes_not_empty_success(payload):
    with pytest.raises(OnlineSourceError) as exc:
        run("bwt-sites", None, handler=lambda _: httpx.Response(200, json=payload))
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_failure_no_fallback_no_secret_echo(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, headers={"Location": "https://other.example/"}, text=SECRET)
    with pytest.raises(OnlineSourceError) as exc:
        run("bwt-sites", None, handler=handler)
    assert len(calls) == 1 and SECRET not in str(exc.value)


@pytest.mark.parametrize("op,opts", [
    ("bwt-queries", {"start_date": "2026-01-01"}), ("bwt-queries", {"device": "mobile"}),
    ("bwt-sites", {"pages": 2}), ("bwt-keyword-history", {"end_date": "2026-01-01"}),
    ("bwt-overlap", {"max_ctr": 0.1}), ("bwt-sites", {"auth": "cookie"}),
    ("bwt-sites", {"query_encoding": "guess"}),
])
def test_no_invented_or_inapplicable_native_options(op, opts):
    with pytest.raises(KeywordMovesError):
        run(op, [], options=opts, handler=lambda _: pytest.fail("no request expected"))


def test_keyword_period_and_context_are_not_property_traffic():
    result, calls = run("bwt-keyword", {"Query": "paper planes", "Impressions": 5, "BroadImpressions": 10},
                       words=("paper planes",), options={"country": "GB", "language": "en",
                       "start_date": "2026-09-01", "end_date": "2026-09-30"})
    assert dict(calls[0].url.params) == {"q": '"paper planes"', "country": '"GB"',
        "language": '"en"', "startDate": "2026-09-01", "endDate": "2026-09-30", "apikey": SECRET}
    assert result.keywords[0].metadata["start_date"] == "2026-09-01"
    assert result.keywords[0].evidence[0].geography == "GB"
    assert result.keywords[0].evidence[0].unit == "impressions_in_reported_period"


def test_domain_url_info_and_no_record_returned():
    result, calls = run("bwt-url-info", None, options={"site_url": SITE, "url": "domain:example.com"})
    assert calls[0].url.params["url"] == '"domain:example.com"'
    assert result.metadata["availability"] == "no-record-returned"


def test_live_opportunity_and_overlap_share_native_parser():
    result, _ = run("bwt-opportunities", [{**ROW, "AvgImpressionPosition": 7}], options={"site_url": SITE})
    assert len(result.keywords) == 1
    result, _ = run("bwt-overlap", [{**ROW, "Query": PAGE}, {**ROW, "Query": SITE + "other/"}],
                    options={"site_url": SITE}, words=("paper planes",))
    assert result.keywords[0].evidence[0].value == 2


@pytest.mark.parametrize("payload", [[], [{**ROW, "Date": None}], [{**ROW, "Clicks": True}],
                                     [{**ROW, "Query": "not a URL"}]])
def test_performance_empty_missing_and_ambiguous_rows(payload):
    if payload == []:
        assert run("bwt-pages", payload, options={"site_url": SITE})[0].keywords == ()
    else:
        with pytest.raises(KeywordMovesError):
            run("bwt-pages", payload, options={"site_url": SITE})


@pytest.mark.parametrize("kind,row", [("sites", {}), ("sites", {"Url": SITE, "IsVerified": "true"}),
    ("url-links", {"Url": SITE, "AnchorText": {}}), ("crawl-issues", {"Url": SITE, "Issues": True}),
    ("links", {"Url": SITE, "Count": -1}), ("crawl", {"Date": "nonsense"})])
def test_diagnostic_shape_errors(kind, row):
    with pytest.raises(KeywordMovesError):
        bwt._record(row, kind)


def test_output_records_limit_does_not_claim_lower_provider_cost():
    result, _ = run("bwt-sites", [{"Url": SITE, "IsVerified": True}, {"Url": PAGE, "IsVerified": True}],
                    options={"limit": 1})
    assert result.metadata["records_received"] == 2 and result.metadata["records_truncated"]
