"""Actual HTTPX serialization against synthetic responses, never live accounts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.tiktok import ACTORS, TikTokPlugin

httpx = pytest.importorskip("httpx")
SECRET = "synthetic-do-not-log"
AUTH = {"access_token": SECRET}
RESEARCH = {**AUTH, "research_approved": True}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def run(op, handler, *, keywords=(), inputs=(), **options):
    return TikTokPlugin(transport=httpx.MockTransport(handler)).run(
        PluginRequest(op, keywords=keywords, inputs=inputs, options=options), ExecutionContext(None))


def ok(data):
    return httpx.Response(200, json={"error": {"code": "ok"}, "data": data})


def val(item):
    return {e.metric: e.value for e in item.evidence}


def test_display_list_pagination_missing_metrics_and_no_secret_export():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.path == "/v2/video/list/"
        assert req.headers["Authorization"] == "Bearer " + SECRET
        body = json.loads(req.content)
        assert body["max_count"] == 2
        if len(calls) == 1:
            assert "cursor" not in body
            return ok({"videos": [{"id": "1", "video_description": "#music", "like_count": 0}], "has_more": True, "cursor": 1666600000000})
        assert body["cursor"] == 1666600000000
        return ok({"videos": [{"id": "1", "video_description": "#music"}, {"id": "2", "video_description": "#music #ukg", "view_count": 10}], "has_more": False, "cursor": 1})
    result = run("display-videos", handler, **AUTH, pages=2, page_size=2)
    items = {i.phrase: i for i in result.keywords}
    assert result.metadata["duplicate_records"] == 1 and result.metadata["collection_complete"]
    assert val(items["#music"])["sampled_video_count"] == 2
    assert val(items["#music"])["sample_video_views_sum"] == 10
    assert val(items["#music"])["sample_video_likes_sum"] == 0
    assert SECRET not in json.dumps(result.to_dict())


def test_display_query_keeps_string_video_ids_and_only_authorised_videos():
    def handler(req):
        assert req.url.path == "/v2/video/query/"
        assert json.loads(req.content)["filters"]["video_ids"] == ["9223372036854775807", "2"]
        return ok({"videos": [{"id": "2", "video_description": "#music"}]})
    result = run("display-query", handler, keywords=("9223372036854775807", "2"), **AUTH)
    assert result.metadata["unreturned_ids"] == ["9223372036854775807"]


def test_display_user_explicit_scopes_and_bio_hashtags():
    def handler(req):
        assert req.method == "GET" and req.url.path == "/v2/user/info/"
        assert req.url.params["fields"] == "display_name,bio_description,follower_count"
        return ok({"user": {"display_name": "Synthetic Musician", "bio_description": "#ukg music", "follower_count": 321}})
    result = run("display-user", handler, **AUTH, profile_fields="display_name,bio_description,follower_count")
    assert result.metadata["profile"]["follower_count"] == 321
    assert any(i.phrase == "#ukg" for i in result.keywords)
    assert all("reported_post_count" not in val(i) for i in result.keywords)


def test_research_exact_hashtag_query_date_range_and_resume():
    calls = []
    def handler(req):
        calls.append(req)
        body = json.loads(req.content)
        assert req.url.path == "/v2/research/video/query/"
        assert body["start_date"] == "20260901" and body["end_date"] == "20260930"
        assert body["query"]["or"] == [{"field_name": "hashtag_name", "operation": "EQ", "field_values": ["music"]}]
        assert body["query"]["and"][0]["field_values"] == ["GB"]
        assert body["is_random"] is True
        assert "voice_to_text" in req.url.params["fields"]
        if len(calls) == 1:
            return ok({"videos": [], "has_more": True, "cursor": 100, "search_id": "opaque-search"})
        assert body["cursor"] == 100 and body["search_id"] == "opaque-search"
        return ok({"videos": [{"id": 9223372036854775807, "hashtag_names": ["music"], "video_description": "#music", "view_count": 30,
                                "favorites_count": 2, "voice_to_text": "warm rain"}], "has_more": False, "cursor": 200, "search_id": "opaque-search"})
    result = run("research-videos", handler, keywords=("#Music",), **RESEARCH, start_date="2026-09-01", end_date="2026-09-30",
                 pages=2, region="GB", is_random=True, include_transcript=True, include_keywords=True)
    assert result.metadata["region_code_meaning"].startswith("creator registration")
    assert "archived" in result.metadata["research_caveat"]
    assert any(i.phrase == "warm rain" for i in result.keywords)


@pytest.mark.parametrize("field,term", [("keyword", "paper planes"), ("username", "synthetic_user"), ("music_id", "123"), ("effect_id", "456"), ("video_id", "789")])
def test_research_other_query_routes(field, term):
    def handler(req):
        condition = json.loads(req.content)["query"]["or"][0]
        assert condition["field_name"] == field and condition["field_values"] == [term]
        assert "voice_to_text" not in req.url.params["fields"]
        return ok({"videos": [], "has_more": False})
    run("research-videos", handler, keywords=(term,), **RESEARCH, query_field=field, start_date="2026-09-01", end_date="2026-09-02")


@pytest.mark.parametrize("key", ["video_id", "comment_id"])
def test_comment_and_reply_routes_do_not_attribute_video_engagement(key):
    def handler(req):
        body = json.loads(req.content)
        assert body[key] == 9223372036854775807 and isinstance(body[key], int)
        assert req.url.path == "/v2/research/video/comment/list/"
        return ok({"comments": [{"id": 1, "video_id": 2, "text": "nice #music", "like_count": 4},
                                 {"id": 2, "video_id": 2, "text": "#music again", "like_count": 5}], "has_more": False})
    result = run("research-comments", handler, **RESEARCH, **{key: "9223372036854775807"})
    tag = next(i for i in result.keywords if i.phrase == "#music")
    assert val(tag)["sampled_comment_count"] == 2 and val(tag)["sample_comment_likes_sum"] == 9
    assert "sampled_video_count" not in val(tag)


def test_research_profile_response_is_not_nested_like_display():
    def handler(req):
        assert req.url.path == "/v2/research/user/info/"
        assert json.loads(req.content) == {"username": "synthetic"}
        assert "username" not in req.url.params["fields"].split(",")
        return ok({"display_name": "Synthetic", "bio_description": "#Music", "follower_count": 0})
    result = run("research-user", handler, **RESEARCH, username="synthetic")
    assert result.metadata["profile"]["follower_count"] == 0


def test_commercial_ad_library_is_not_organic_search_volume():
    def handler(req):
        body = json.loads(req.content)
        assert req.url.path == "/v2/research/adlib/ad/query/"
        assert body["filters"]["country_code_list"] == ["FR"]
        assert body["search_term"] == "music" and body["max_count"] <= 10
        return ok({"ads": [{"ad": {"id": 1, "reach": {"unique_users_seen": "11K"}}}], "has_more": False})
    result = run("commercial-ads", handler, keywords=("music",), **AUTH, commercial_approved=True,
                 country="FR", start_date="2026-09-01", end_date="2026-09-30")
    assert val(result.keywords[0]) == {"sample_matching_ad_count": 1}
    assert result.keywords[0].metadata["ads"][0]["reach_display"] == "11K"


@pytest.mark.parametrize("op,options,keywords", [
    ("research-videos", AUTH, ("music",)),
    ("research-user", AUTH, ()),
    ("commercial-ads", AUTH, ("music",)),
    ("research-videos", {**RESEARCH, "start_date": "2026-01-01", "end_date": "2026-03-01"}, ("music",)),
    ("research-comments", {**RESEARCH, "video_id": "1", "comment_id": "2"}, ()),
    ("display-videos", AUTH, ("music",)),
    ("display-query", AUTH, ("bad-id",)),
    ("display-user", {**AUTH, "profile_fields": "password"}, ()),
    ("public-hashtag", {}, ("music",)),
    ("apify-start", {"apify_token": SECRET}, ("music",)),
    ("apify-start", {"apify_token": SECRET, "allow_paid": True}, ("music",)),
    ("display-videos", {**AUTH, "llm": "openai"}, ()),
    ("display-videos", {**AUTH, "limit": 0}, ()),
])
def test_configuration_errors_before_requests(op, options, keywords):
    with pytest.raises(ConfigurationError):
        run(op, lambda _: pytest.fail("must not send a request"), keywords=keywords, **options)


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500])
def test_http_errors_do_not_retry_follow_redirects_or_echo_secrets(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text=SECRET, headers={"Location": "https://evil.example/"})
    with pytest.raises(OnlineSourceError) as e:
        run("display-videos", handler, **AUTH)
    assert SECRET not in str(e.value) and len(calls) == 1


@pytest.mark.parametrize("error", [{"code": "access_token_invalid", "message": SECRET}, {"code": "scope_not_authorized"}, {}, None])
def test_official_http_200_errors_and_changed_schemas_are_not_empty_success(error):
    with pytest.raises(OnlineSourceError) as e:
        run("display-videos", lambda _: httpx.Response(200, json={"error": error, "data": {"videos": []}}), **AUTH)
    assert SECRET not in str(e.value)


def test_pagination_request_budget_and_repeated_cursor():
    def handler(req):
        return ok({"videos": [{"id": "1", "video_description": "#music"}], "has_more": True, "cursor": 20})
    result = run("display-videos", handler, **AUTH, pages=10, max_requests=1)
    assert result.metadata["more_available"] and result.metadata["request_count"] == 1
    assert result.metadata["next_cursor"] == 20 and not result.metadata["collection_complete"]
    with pytest.raises(OnlineSourceError, match="Repeated"):
        run("display-videos", handler, **AUTH, pages=3)


def test_provider_must_respect_page_limit_and_pagination_shape():
    with pytest.raises(InputError):
        run("display-videos", lambda _: ok({"videos": [{}, {}], "has_more": False}), **AUTH, page_size=1)
    with pytest.raises(OnlineSourceError):
        run("display-videos", lambda _: ok({"videos": []}), **AUTH)


def test_environment_token_and_invalid_explicit_key_do_not_fall_back(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "environment-value")
    def handler(req):
        assert req.headers["Authorization"] == "Bearer environment-value"
        return ok({"videos": [], "has_more": False})
    run("display-videos", handler)
    with pytest.raises(ConfigurationError):
        run("display-videos", lambda _: pytest.fail("must not fall back"), access_token="")


def test_keywordtool_tiktok_uses_keywords_not_instagram_hashtag_type():
    def handler(req):
        assert req.url.path == "/v2/search/suggestions/tiktok"
        body = json.loads(req.content)
        assert body["type"] == "suggestions" and body["keyword"] == "garage music" and body["metrics"] is True
        assert body["country"] == "GB"
        return httpx.Response(200, json={"results": {"garage music": {"string": "garage music", "volume": None, "cpc": 2, "cmp": .5, "m1": 50}},
                                           "notice": {"code": 10}, "total_keywords": 1})
    result = run("suggestions", handler, keywords=("garage music",), api_key=SECRET, country="GB", metrics=True)
    assert result.keywords[0].phrase == "garage music"
    assert result.keywords[0].metadata["verification"] == "not-a-confirmed-hashtag"
    assert val(result.keywords[0])["estimated_search_volume"] is None
    assert val(result.keywords[0])["google_ads_cpc_proxy"] == 2
    assert result.metadata["partial_notice"] is True
    assert SECRET not in json.dumps(result.to_dict())


def test_keywordtool_metrics_batches_without_hashtag_confirmation():
    def handler(req):
        assert req.url.path == "/v2/search/volume/tiktok"
        assert json.loads(req.content)["keyword"] == ["music", "#ukg"]
        return httpx.Response(200, json={"results": {"music": {"volume": 100}}})
    result = run("metrics", handler, keywords=("music", "#ukg"), api_key=SECRET, country="GB")
    assert result.keywords[0].metadata["kind"] == "keyword"


def test_oembed_uses_caption_without_executing_html_or_inventing_counts():
    def handler(req):
        assert req.url.path == "/oembed" and req.url.params["url"] == "https://www.tiktok.com/@synthetic/video/1"
        return httpx.Response(200, json={"type": "video", "title": "#Music hello", "html": "<script>bad()</script>"})
    result = run("oembed", handler, keywords=("https://www.tiktok.com/@synthetic/video/1",))
    assert result.keywords[0].phrase == "#music"
    assert val(result.keywords[0])["sample_video_views_sum"] is None
    assert not result.metadata["engagement_metrics_available"]
    assert "bad()" not in json.dumps(result.to_dict())


def test_public_page_checks_robots_and_matches_exact_hashtag():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert req.url.path == "/tag/music"
        state = {"__DEFAULT_SCOPE__": {"webapp.challenge-detail": {"challengeInfo": {"challenge": {"title": "music", "id": "42"},
                                                                                       "stats": {"videoCount": 0, "viewCount": 100}}}}}
        return httpx.Response(200, text='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">' + json.dumps(state) + '</script>')
    result = run("public-hashtag", handler, keywords=("#Music",), allow_unofficial=True)
    assert len(calls) == 2 and val(result.keywords[0])["reported_post_count"] == 0


def test_public_page_robots_denial_stops_before_tag_request():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, text="User-agent: *\nDisallow: /tag/\n")
    with pytest.raises(OnlineSourceError, match="robots"):
        run("public-hashtag", handler, keywords=("music",), allow_unofficial=True)
    assert len(calls) == 1


@pytest.mark.parametrize("actor", list(ACTORS))
def test_apify_presets_submit_once_no_downloads_no_ai_and_explicit_budget(actor):
    captured = []
    terms = ("https://www.tiktok.com/@synthetic/video/1",) if actor in {"related-videos", "video-details"} else ("music",)
    def handler(req):
        captured.append(req)
        assert req.url.path == f"/v2/actors/{ACTORS[actor]}/runs"
        assert req.url.params["maxTotalChargeUsd"] == "0.5"
        assert req.url.params["restartOnError"] == "false"
        body = json.loads(req.content)
        if actor == "hashtag-analytics":
            assert body == {"hashtags": ["music"], "adsCountryCode": "gb", "adsTimeRange": "7"}
        else:
            assert body["shouldDownloadVideos"] is False and body["aiVideoSummary"] is False
            assert body["downloadSubtitlesOptions"] == "NEVER_DOWNLOAD_SUBTITLES"
            assert body["resultsPerPage"] == 5
            if actor == "keyword-videos":
                assert body["searchQueries"] == ["music"] and body["searchSection"] == "/video"
            if actor == "related-videos":
                assert body["scrapeRelatedVideos"] is True
        return httpx.Response(201, json={"data": {"id": "run123", "status": "READY"}})
    options = {"country": "GB", "period": "7"} if actor == "hashtag-analytics" else {}
    result = run("apify-start", handler, keywords=terms, apify_token=SECRET, allow_paid=True, max_charge_usd=.5,
                 actor=actor, **({"results_per_seed": 5} if actor != "hashtag-analytics" else {}), **options)
    assert len(captured) == 1 and result.metadata["status"] == "submitted" and not result.keywords
    assert SECRET not in json.dumps(result.to_dict())


@pytest.mark.parametrize("amount", [None, 0, -1, True, float("nan"), float("inf"), 101, "oops"])
def test_apify_invalid_charge_never_starts_job(amount):
    with pytest.raises(ConfigurationError):
        run("apify-start", lambda _: pytest.fail("must not start"), keywords=("music",),
            apify_token=SECRET, allow_paid=True, max_charge_usd=amount)


@pytest.mark.parametrize("status", ["READY", "RUNNING", "TIMING-OUT", "ABORTING"])
def test_pending_apify_run_is_not_completed_or_restarted(status):
    result = run("apify-fetch", lambda _: httpx.Response(200, json={"data": {"status": status}}),
                 apify_token=SECRET, run_id="run123", scope="synthetic")
    assert result.metadata["collection_complete"] is False and result.metadata["request_count"] == 1
    assert not result.keywords


@pytest.mark.parametrize("status", ["FAILED", "TIMED-OUT", "ABORTED", None])
def test_failed_apify_runs_are_not_empty_success(status):
    with pytest.raises(OnlineSourceError):
        run("apify-fetch", lambda _: httpx.Response(200, json={"data": {"status": status}}),
            apify_token=SECRET, run_id="run123", scope="synthetic")


def test_apify_fetch_hashtag_analytics_uses_run_collection_time_and_exact_scope():
    def handler(req):
        if "/actor-runs/" in req.url.path:
            return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "data123", "actId": "actor123", "finishedAt": "2026-09-30T15:00:00Z"}})
        assert req.url.path == "/v2/datasets/data123/items"
        return httpx.Response(200, json=[{"hashtagName": "music", "countryCode": "GB", "period": "7", "publishCntAll": 123,
                                          "publishCnt": 3, "videoViewsAll": 345, "audienceAges": [{"ageLevel": 3, "score": 67}]}])
    result = run("apify-fetch", handler, apify_token=SECRET, run_id="run123", scope="same-filter", dataset_kind="hashtags")
    assert result.metadata["observed_at"] == "2026-09-30T15:00:00+00:00"
    assert val(result.keywords[0])["reported_post_count"] == 123
    assert result.keywords[0].metadata["geography"] == "GB"
    assert result.keywords[0].metadata["scope"] == "same-filter"


def test_apify_dataset_pagination_offset_and_unknown_completion_at_full_page():
    offsets = []
    def handler(req):
        offsets.append(int(req.url.params["offset"]))
        return httpx.Response(200, json=[{"id": str(offsets[-1]), "text": "#music", "playCount": 10}])
    result = run("apify-fetch", handler, apify_token=SECRET, dataset_id="data123", scope="same-filter", observed_at="2026-10-01",
                 pages=2, page_size=1, offset=10)
    assert offsets == [10, 11] and result.metadata["next_offset"] == 12
    assert result.metadata["more_available"] and val(result.keywords[0])["sampled_video_count"] == 2


def test_inputs_are_rejected_on_network_operations():
    with pytest.raises(ConfigurationError):
        run("display-videos", lambda _: pytest.fail("no request"), inputs=(Path("x"),), **AUTH)


@pytest.mark.parametrize("actor,extra", [
    ("hashtag-videos", {"country": "GB"}),
    ("hashtag-videos", {"related_searches": True}),
    ("profile-videos", {"video_sort": "LATEST"}),
    ("hashtag-analytics", {"results_per_seed": 5}),
    ("hashtag-analytics", {"comments_per_post": 5}),
    ("video-details", {"replies_per_comment": 5}),
])
def test_apify_ignored_or_conflicting_options_fail_before_paid_job(actor, extra):
    with pytest.raises(ConfigurationError):
        run("apify-start", lambda _: pytest.fail("must not start paid job"), keywords=("music",),
            apify_token=SECRET, allow_paid=True, max_charge_usd=1, actor=actor, **extra)


@pytest.mark.parametrize("op", ["display-videos", "research-comments"])
def test_unsupported_resume_field_is_not_sent(op):
    with pytest.raises(ConfigurationError):
        run(op, lambda _: pytest.fail("no request"), search_id="not-supported", **AUTH)


def test_keyword_video_actor_related_queries_options_and_comments_are_explicit():
    def handler(req):
        body = json.loads(req.content)
        assert body["scrapeRelatedSearchWords"] is True
        assert body["videoSearchSorting"] == "LATEST"
        assert body["videoSearchDateFilter"] == "PAST_WEEK"
        assert body["commentsPerPost"] == 3 and body["maxRepliesPerComment"] == 2
        assert req.url.params["build"] == "1.0.0"
        return httpx.Response(201, json={"data": {"id": "testRun", "status": "READY"}})
    result = run("apify-start", handler, keywords=("uk garage",), apify_token=SECRET, actor="keyword-videos",
                 allow_paid=True, max_charge_usd=1, related_searches=True, video_sort="LATEST",
                 video_date_filter="PAST_WEEK", comments_per_post=3, replies_per_comment=2, actor_build="1.0.0")
    assert "raw video dataset" in result.metadata["related_search_words_note"]
