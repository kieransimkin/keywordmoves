"""Synthetic provider contracts. These tests do not start paid jobs."""
import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.youtube import YouTubePlugin
from keywordmoves.online.youtube_providers import PRESETS

httpx = pytest.importorskip("httpx")
V1, V2 = "AAAAAaaaa01", "BBBBBbbbb02"
CID = "UC" + "a" * 22


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def call(operation, handler, keywords=(), **options):
    seen = []
    def handle(req):
        seen.append(req)
        return handler(req)
    r = YouTubePlugin(transport=httpx.MockTransport(handle)).run(PluginRequest(operation, tuple(keywords), options=options), ExecutionContext(None))
    assert "secret" not in json.dumps(r.to_dict()).casefold()
    return r, seen


@pytest.mark.parametrize("kind", ["hashtags", "suggestions", "questions", "prepositions"])
def test_keywordtool_youtube_all_suggestion_types_and_proxy_labels(kind):
    def handler(req):
        body = json.loads(req.content)
        assert req.url.path == "/v2/search/suggestions/youtube"
        assert body["type"] == kind and body["country"] == "GB" and body["apikey"] == "secret-key"
        return httpx.Response(200, json={"results": {"ukgarage": {"string": "ukgarage", "volume": 100, "cpc": 0.5, "cmp": 0.2}}})
    r, seen = call("suggestions", handler, ["garage"], country="GB", metrics=True, suggestion_type=kind, keywordtool_api_key="secret-key")
    assert len(seen) == 1
    assert r.keywords[0].phrase == ("#ukgarage" if kind == "hashtags" else "ukgarage")
    assert {e.metric for e in r.keywords[0].evidence} == {"returned_order", "estimated_search_volume", "google_ads_cpc_proxy", "google_ads_competition_proxy"}


def test_keywordtool_metrics_missing_zero_and_bad_hashtag():
    r, _ = call("metrics", lambda req: httpx.Response(200, json={"results": {"#ukgarage": {"volume": 0}}}), ["#ukgarage"], country="GB", api_key="secret")
    assert next(e.value for e in r.keywords[0].evidence if e.metric == "estimated_search_volume") == 0
    assert next(e.value for e in r.keywords[0].evidence if e.metric == "google_ads_cpc_proxy") is None
    with pytest.raises(InputError):
        call("suggestions", lambda req: httpx.Response(200, json={"results": {"not a hashtag": {}}}), ["garage"], country="GB", api_key="secret")


def test_serp_organic_shorts_related_queries_and_paid_exclusion():
    def handler(req):
        assert req.url.params["engine"] == "youtube"
        return httpx.Response(200, json={"search_metadata": {"status": "Success"},
            "video_results": [{"title": "#ukgarage track", "link": "https://www.youtube.com/watch?v=" + V1, "views": 1000, "published_date": "2 days ago"}],
            "shorts_results": [{"position_on_page": 3, "shorts": [{"title": "#ukgarage short", "video_id": V2, "views_original": "2M views", "views": 2000000}]}],
            "related_searches": [{"query": "garage music"}], "ads_results": [{"title": "not organic"}],
            "serpapi_pagination": {"next_page_token": "next-token", "next": "https://evil.test/secret"}})
    r, seen = call("serp-search", handler, ["#ukgarage"], serpapi_key="secret")
    assert len(seen) == 1 and r.metadata["more_available"] and r.metadata["excluded_ad_records"] == 1
    assert next(i for i in r.keywords if i.phrase == "garage music").relationship == "youtube-related-search"
    h = next(i for i in r.keywords if i.phrase == "#ukgarage")
    assert next(e.value for e in h.evidence if e.metric == "sample_views_sum") == 1000
    assert any(d["content_type"] == "shorts" and d["approximate_counters"] == ["views"] for d in h.metadata["supporting_records"])


def test_serp_pagination_uses_only_token_and_rejects_loop():
    def handler(req):
        assert req.url.host == "serpapi.com"
        return httpx.Response(200, json={"video_results": [], "serpapi_pagination": {"next_page_token": "repeat", "next": "https://evil.test"}})
    with pytest.raises(OnlineSourceError, match="repeated"):
        call("serp-search", handler, ["music"], serpapi_key="secret", pages=3)


@pytest.mark.parametrize("payload", [{}, {"error": "secret"}, {"search_metadata": {"status": "Processing"}}])
def test_serp_failure_not_false_empty(payload):
    with pytest.raises(OnlineSourceError):
        call("serp-search", lambda _: httpx.Response(200, json=payload), ["music"], serpapi_key="secret")


def test_dataforseo_request_and_task_status():
    def handler(req):
        task = json.loads(req.content)[0]
        assert task["block_depth"] == 20 and task["location_code"] == 2826
        assert req.url.path.endswith("/serp/youtube/organic/live/advanced")
        assert req.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"status_code": 20000, "cost": 0.01, "tasks": [{"status_code": 20000, "result": [{"items": [
            {"type": "youtube_video", "video_id": V1, "title": "#garage track", "views_count": 50, "timestamp": "2026-10-01 12:00:00 +00:00", "is_shorts": True, "rank_absolute": 1},
            {"type": "youtube_video_paid", "video_id": V2}]}]}]})
    r, seen = call("dataforseo-search", handler, ["garage"], location_code=2826, login="secret-login", password="secret-password")
    assert len(seen) == 1 and r.metadata["excluded_record_types"] == {"youtube_video_paid": 1}
    assert r.metadata["provider_cost"] == 0.01
    with pytest.raises(OnlineSourceError):
        call("dataforseo-search", lambda _: httpx.Response(200, json={"status_code": 20000, "tasks": [{"status_code": 40501}]}), ["garage"], location_code=2826, login="secret", password="secret")


def header(tag="#ukgarage"):
    return '<html><script>var ytInitialData = ' + json.dumps({"contents": {"hashtagHeaderRenderer": {"title": {"simpleText": tag}, "numVideos": {"simpleText": "12K videos"}, "numChannels": {"simpleText": "340 channels"}}}}) + ';</script></html>'


@pytest.mark.parametrize("op", ["autocomplete", "public-hashtag"])
def test_experimental_opt_in_before_network(op):
    with pytest.raises(ConfigurationError, match="allow_unofficial"):
        call(op, lambda _: pytest.fail("no request expected"), ["ukgarage"])


@pytest.mark.parametrize("robots", ["User-agent: *\nDisallow: /", "<html>captcha</html>", "unrecognised response"])
def test_robots_denial_prevents_page_access(robots):
    seen = []
    def handler(req):
        seen.append(req)
        assert req.url.path == "/robots.txt"
        return httpx.Response(200, text=robots)
    with pytest.raises(OnlineSourceError):
        call("public-hashtag", handler, ["ukgarage"], allow_unofficial=True)
    assert len(seen) == 1


def test_public_header_matching_counts_and_rounding():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 1")
        assert req.url.path == "/hashtag/ukgarage"
        return httpx.Response(200, text=header())
    r, seen = call("public-hashtag", handler, ["UKGarage"], allow_unofficial=True)
    assert len(seen) == 2
    assert r.keywords[0].evidence[0].value == 12000 and r.keywords[0].metadata["approximate"]
    assert r.keywords[1].evidence[0].value == 340 and not r.keywords[1].metadata["approximate"]


def test_browser_autocomplete_fixed_client_and_youtube_property():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        assert req.url.params["ds"] == "yt" and req.url.params["client"] == "firefox"
        return httpx.Response(200, json=["music", ["music mix", ["#music"]]])
    r, seen = call("autocomplete", handler, ["music"], allow_unofficial=True)
    assert [i.phrase for i in r.keywords] == ["music mix", "#music"]
    assert len(seen) == 2


@pytest.mark.parametrize("preset", PRESETS)
def test_apify_every_preset_exact_fields_no_ai_or_media_downloads(preset):
    seed = "@example" if preset == "channel-videos" else "PLexample" if preset == "playlist-videos" else V1 if preset in {"comments", "video-details"} else "music"
    def handler(req):
        assert req.method == "POST" and req.url.host == "api.apify.com"
        assert req.url.params["maxTotalChargeUsd"] == "1.0"
        assert req.url.params["timeout"] == "120"
        body = json.loads(req.content)
        if preset == "comments":
            assert req.url.path.endswith("streamers~youtube-comments-scraper/runs")
            assert body["maxComments"] == 20 and body["sortCommentsBy"] == "NEWEST_FIRST"
        else:
            assert req.url.path.endswith("streamers~youtube-scraper/runs")
            assert body["transcriptionAndSubtitle"] == "NONE" and body["aiVideoDescription"] is False
            assert body["aiVideoSummary"] is False and body["saveSubsToKVS"] is False
            if preset in {"shorts", "streams"}:
                assert body["maxResults"] == 0
            assert ("searchQueries" in body) != ("startUrls" in body)
        return httpx.Response(201, json={"data": {"id": "run1", "status": "READY"}})
    r, seen = call("apify-start", handler, [seed], actor=preset, apify_token="secret", allow_paid=True, max_charge_usd=1)
    assert len(seen) == 1 and r.metadata["submitted"] and not r.metadata["completed"]


@pytest.mark.parametrize("options", [{}, {"allow_paid": True}, {"allow_paid": True, "max_charge_usd": True}, {"allow_paid": True, "max_charge_usd": 0}, {"allow_paid": True, "max_charge_usd": float("inf")}, {"allow_paid": True, "max_charge_usd": -1}])
def test_apify_charge_guard(options):
    with pytest.raises(ConfigurationError):
        call("apify-start", lambda _: pytest.fail("must not spend"), ["garage"], **options)


@pytest.mark.parametrize("kind", ["videos", "comments", "observations"])
def test_apify_completed_run_fetch_no_resubmit(kind):
    def handler(req):
        assert req.method == "GET"
        if "/actor-runs/" in req.url.path:
            return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds", "actId": "actor1", "finishedAt": "2026-09-30T12:00:00Z"}})
        if not req.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": {"itemCount": 2}})
        if kind == "videos":
            data = [{"id": V1, "title": "#garage", "viewCount": 50}]
        elif kind == "comments":
            data = [{"id": "comment1", "text": "More #garage please"}]
        else:
            data = [{"phrase": "#garage", "metric": "reported_video_count", "unit": "videos", "value": 100}]
        return httpx.Response(200, json=data)
    r, seen = call("apify-fetch", handler, run_id="run1", apify_token="secret", scope="same-settings", dataset_kind=kind)
    assert len(seen) == 3 and r.keywords
    assert r.metadata["observed_at"] == "2026-09-30T12:00:00Z"
    assert r.metadata["more_available"] and not r.metadata["submitted"]


@pytest.mark.parametrize("status", ["READY", "RUNNING", "FAILED", "ABORTED", "TIMED-OUT"])
def test_apify_pending_failed_not_interpreted_as_empty(status):
    with pytest.raises(OnlineSourceError):
        call("apify-fetch", lambda req: httpx.Response(200, json={"data": {"status": status}}), run_id="run1", scope="same", apify_token="secret")
