"""Real HTTPX serialization with synthetic API responses; never live account calls."""
import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.youtube import YouTubePlugin

httpx = pytest.importorskip("httpx")
V1, V2 = "AAAAAaaaa01", "BBBBBbbbb02"
CID = "UC" + "a" * 22


def details(vid=V1, title="#UKGarage tune", **extra):
    return {"id": vid, "snippet": {"title": title, "description": "#Brighton independent music", "tags": ["garage music"],
            "publishedAt": "2026-10-01T12:00:00Z", "channelId": CID}, "statistics": {"viewCount": "1000", "likeCount": "50"}, **extra}


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def call(operation, handler, keywords=(), **options):
    seen = []
    def handle(req):
        seen.append(req)
        return handler(req)
    result = YouTubePlugin(transport=httpx.MockTransport(handle)).run(
        PluginRequest(operation, tuple(keywords), options={"api_key": "SECRET-KEY", "access_token": "SECRET-TOKEN", **options}), ExecutionContext(None))
    assert "SECRET" not in json.dumps(result.to_dict())
    return result, seen


def test_hashtag_queries_hydrates_and_checks_exact_literal_match():
    def handler(req):
        if req.url.path.endswith("/search"):
            assert req.url.params["q"] == "#ukgarage" and req.url.params["type"] == "video"
            return httpx.Response(200, json={"items": [{"id": {"videoId": V1}, "snippet": {"title": "truncated"}}, {"id": {"videoId": V2}, "snippet": {}}], "pageInfo": {"totalResults": 1000000}})
        assert set(req.url.params["id"].split(",")) == {V1, V2}
        return httpx.Response(200, json={"items": [details(), details(V2, "#UKGarages tune")]})
    r, seen = call("hashtag", handler, ["#UKGarage"], country="GB", language="en")
    assert len(seen) == 2
    assert r.metadata["literal_matching_video_count"] == 1
    assert r.metadata["unverified_search_video_ids"] == [V2]
    assert r.metadata["pagination"][0]["approximate_search_result_totals"] == [1000000]
    assert not any(e.metric == "reported_video_count" for i in r.keywords for e in i.evidence)
    assert r.metadata["endpoint_calls"] == {"search": 1, "videos": 1}
    assert r.metadata["derived_statistics_enabled"] is False


def test_empty_hashtag_is_not_banned_or_zero_global_popularity():
    r, seen = call("hashtag", lambda _: httpx.Response(200, json={"items": []}), ["unknown"])
    assert len(seen) == 1 and not r.keywords
    assert r.metadata["hashtag_verification"] == "not-observed-in-this-sample"


def test_video_batch_and_missing_ids_reported():
    r, seen = call("videos", lambda req: httpx.Response(200, json={"items": [details()]}), [V1, V2])
    assert len(seen) == 1 and r.metadata["unavailable_video_ids"] == [V2]


def test_search_channel_and_playlist_names_not_video_measurements():
    for kind, rid in (("channel", CID), ("playlist", "PLabc")):
        r, seen = call("search", lambda req: httpx.Response(200, json={"items": [{"id": {kind + "Id": rid}, "snippet": {"title": "Garage sessions", "description": "Music"}}]}), ["garage"], search_type=kind)
        assert len(seen) == 1 and r.metadata["record_kind"] == kind + "s"
        assert any(i.phrase == "garage sessions" for i in r.keywords)


def test_search_paging_uses_token_not_total_results():
    def handler(req):
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [details()]})
        if req.url.params.get("pageToken") == "NEXT":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": [{"id": {"videoId": V1}, "snippet": {}}], "nextPageToken": "NEXT", "pageInfo": {"totalResults": 0}})
    r, seen = call("search", handler, ["music"], pages=2, duration="short", caption="closedCaption", event_type="completed", definition="high", safe_search="strict", category_id=10, channel_id=CID, published_after="2026-01-01", published_before="2026-10-01")
    assert len(seen) == 3
    assert seen[0].url.params["videoDuration"] == "short"
    assert r.metadata["pagination"][0]["more_available"] is False
    assert r.metadata["unique_records"] == 1


@pytest.mark.parametrize("payload", [{"error": {"message": "SECRET"}}, {}, {"items": None}, {"items": [], "nextPageToken": 123}])
def test_malformed_search_results(payload):
    with pytest.raises((OnlineSourceError, InputError)) as err:
        call("search", lambda _: httpx.Response(200, json=payload), ["music"])
    assert "SECRET" not in str(err.value)


def test_repeated_pagination_and_unrequested_video_id_fail():
    with pytest.raises(OnlineSourceError, match="repeated"):
        call("search", lambda _: httpx.Response(200, json={"items": [], "nextPageToken": "loop"}), ["music"], pages=3)
    with pytest.raises(OnlineSourceError, match="unrequested"):
        call("videos", lambda _: httpx.Response(200, json={"items": [details(V2)]}), [V1])


def test_channel_uses_uploads_playlist_not_channel_search():
    def handler(req):
        if req.url.path.endswith("/channels"):
            assert req.url.params["forHandle"] == "@example"
            return httpx.Response(200, json={"items": [{"id": CID, "snippet": {"title": "Music"}, "statistics": {"subscriberCount": "123000", "hiddenSubscriberCount": False}, "contentDetails": {"relatedPlaylists": {"uploads": "UUuploads"}}}]})
        if req.url.path.endswith("/playlistItems"):
            assert req.url.params["playlistId"] == "UUuploads"
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": V1}}]})
        return httpx.Response(200, json={"items": [details()]})
    r, seen = call("channel", handler, handle="@example")
    assert len(seen) == 3 and r.metadata["channel"]["id"] == CID
    assert "three significant" in r.metadata["channel"]["subscriber_count_precision"]


def test_mine_uses_oauth_and_missing_uploads_fail_cleanly():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer SECRET-TOKEN"
        assert "key" not in req.url.params and req.url.params["mine"] == "true"
        return httpx.Response(200, json={"items": [{"id": CID, "contentDetails": {}}]})
    with pytest.raises(OnlineSourceError):
        call("channel", handler, mine=True)


def test_playlist_and_popular():
    def handler(req):
        if req.url.path.endswith("/playlistItems"):
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": V1}}]})
        return httpx.Response(200, json={"items": [details()]})
    r, _ = call("playlist", handler, playlist_id="PLmusic")
    assert r.metadata["unique_records"] == 1
    r, seen = call("popular", handler, country="GB", category_id=10)
    assert seen[0].url.params["chart"] == "mostPopular" and "Gaming" in r.metadata["chart_scope"]


def test_comments_and_replies_preserve_context_but_not_personal_profile():
    def handler(req):
        c = {"id": "reply" if req.url.path.endswith("/comments") else "parent", "snippet": {"textOriginal": "More #UKGarage please", "likeCount": 3,
            "authorDisplayName": "PRIVATE-NAME", "authorChannelId": {"value": "SECRET-PROFILE"}, "publishedAt": "2026-10-01T12:00:00Z"}}
        if req.url.path.endswith("/comments"):
            c["snippet"]["parentId"] = "parent"
            return httpx.Response(200, json={"items": [c], "nextPageToken": "MORE"})
        return httpx.Response(200, json={"items": [{"snippet": {"topLevelComment": c, "totalReplyCount": 20}}]})
    r, seen = call("comments", handler, video_id=V1, include_replies=True, search_terms="garage")
    assert len(seen) == 2 and r.metadata["unique_records"] == 2
    assert r.metadata["parents_with_uncollected_replies"] == ["parent"]
    assert "PRIVATE-NAME" not in json.dumps(r.to_dict())


def test_caption_permissions_and_cue_boundaries():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer SECRET-TOKEN"
        if req.url.path.endswith("/captions"):
            return httpx.Response(200, json={"items": [{"id": "track", "snippet": {"language": "en", "name": "English"}}]})
        assert req.url.params["tfmt"] == "vtt"
        return httpx.Response(200, text="WEBVTT\n\n00:00.000 --> 00:01.000\nPaper planes\n\n00:01.000 --> 00:02.000\nBrighton sky\n")
    r, seen = call("captions-list", handler, video_id=V1)
    assert len(seen) == 1 and not r.keywords and r.metadata["caption_tracks"][0]["id"] == "track"
    r, seen = call("captions", handler, video_id=V1, caption_id="track")
    assert len(seen) == 2 and len(r.metadata["transcript_cues"]) == 2
    assert "planes brighton" not in {i.phrase for i in r.keywords}
    with pytest.raises(ConfigurationError, match="belong"):
        call("captions", handler, video_id=V1, caption_id="other")


@pytest.mark.parametrize("op,dimension,word", [("analytics-search", "insightTrafficSourceDetail", "uk garage"), ("analytics-hashtags", "insightTrafficSourceDetail", "ukgarage"), ("analytics-traffic", "insightTrafficSourceType", "YT_SEARCH"), ("analytics-videos", "video", V1)])
def test_analytics_column_names_not_positions_and_native_hashtag_attribution(op, dimension, word):
    def handler(req):
        assert req.headers["Authorization"] == "Bearer SECRET-TOKEN"
        p = req.url.params
        assert p["ids"] == "channel==" + CID and p["dimensions"] == dimension
        if op == "analytics-hashtags":
            assert "insightTrafficSourceType==HASHTAGS" in p["filters"]
        if op == "analytics-search":
            assert "insightTrafficSourceType==YT_SEARCH" in p["filters"]
        if op in {"analytics-search", "analytics-hashtags"}:
            assert int(p["maxResults"]) <= 25
            assert "averageViewDuration" not in p["metrics"]
        names = list(reversed(p["metrics"].split(","))) + [dimension]
        return httpx.Response(200, json={"columnHeaders": [{"name": n} for n in names], "rows": [[100 if n != dimension else word for n in names]]})
    r, seen = call(op, handler, channel_id=CID, start_date="2026-09-01", end_date="2026-09-30", video_id=V1, country="GB")
    assert len(seen) == 1 and r.metadata["channel_id"] == CID
    if op == "analytics-hashtags":
        assert r.keywords[0].phrase == "#ukgarage" and r.keywords[0].evidence[-3].metric == "attributed_views"
    if op == "analytics-videos":
        assert not r.keywords and r.metadata["video_reports"][0]["video_id"] == V1


@pytest.mark.parametrize("op,options", [("analytics-search", {"report_limit": 26}), ("analytics-search", {"start_date": "2026-11-01"}), ("videos", {"derive_metrics": True}), ("channel", {"handle": "@one", "channel_id": CID})])
def test_preflight_fails_without_spending_api_quota(op, options):
    with pytest.raises(ConfigurationError):
        call(op, lambda _: pytest.fail("no request expected"), [V1] if op == "videos" else (), **{"channel_id": CID, "start_date": "2026-01-01", "end_date": "2026-10-01", **options})


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
def test_errors_never_replay_or_leak_credentials(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, headers={"Location": "https://evil.test/SECRET"}, text="SECRET")
    with pytest.raises(OnlineSourceError) as e:
        call("videos", handler, [V1])
    assert "SECRET" not in str(e.value) and len(calls) == 1


def test_region_filter_does_not_relabel_global_video_counters():
    r, _ = call("videos", lambda _: httpx.Response(200, json={"items": [details()]}), [V1], country="GB")
    assert r.metadata["query_region"] == "GB" and r.metadata["country"] is None
    assert all(e.geography is None for i in r.keywords for e in i.evidence)


def test_opaque_comment_reply_and_caption_ids():
    from keywordmoves.online.youtube import opaque_id
    assert opaque_id("Ugx123.Abc-123_xyz", "comment_id") == "Ugx123.Abc-123_xyz"
    assert opaque_id("AuG_x-y==", "caption_id") == "AuG_x-y=="
    with pytest.raises(ConfigurationError):
        opaque_id("../malicious", "caption_id")
    assert YouTubePlugin._comment({"id": "Ugx123.Abc", "snippet": {"textOriginal": "music"}}, V1)["id"] == "Ugx123.Abc"
