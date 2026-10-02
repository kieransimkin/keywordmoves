"""Third-party contracts are explicit and never a fallback for denied native access."""
from __future__ import annotations

import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import common
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.reddit import RedditPlugin
from keywordmoves.online.reddit_providers import apify_rows

httpx = pytest.importorskip("httpx")


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda _: None)
    def forbidden(*args, **kwargs):
        pytest.fail("No real provider calls are allowed in unit tests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def invoke(operation, response, *, keywords=("paper planes",), **options):
    calls = []
    def handler(req):
        calls.append(req)
        payload = response(req) if callable(response) else response
        return payload if isinstance(payload, httpx.Response) else httpx.Response(200, json=payload)
    result = RedditPlugin(transport=httpx.MockTransport(handler)).run(
        PluginRequest(operation, tuple(keywords), options={"api_key": "provider-secret", **options}), ExecutionContext(None))
    return result, calls


def test_keywordtool_reddit_specific_request_and_distinct_units():
    payload = {"results": {"paper planes": {"string": "Paper planes", "volume": 50,
                                           "cpc": 2.1, "cmp": 0.2, "m1": 20}}}
    result, calls = invoke("suggestions", payload, country="GLB", language="en-GB", metrics=True)
    body = json.loads(calls[0].content)
    assert calls[0].url.path == "/v2/search/suggestions/reddit"
    assert calls[0].method == "POST" and body["country"] == "GLB"
    assert body["type"] == "suggestions" and body["language"] == "en-GB"
    assert body["metrics_currency"] == "USD"
    metrics = {e.metric: (e.value, e.unit) for e in result.keywords[0].evidence}
    assert metrics["estimated_search_volume"] == (50, "searches_per_month")
    assert metrics["google_ads_cpc_proxy"] == (2.1, "USD")
    assert result.keywords[0].metadata["monthly_metrics"] == {"m1": 20}
    assert "provider-secret" not in json.dumps(result.to_dict())
    assert result.keywords[0].score is None


def test_suggestion_types_and_sandbox_no_automatic_metric_call():
    result, calls = invoke("suggestions", {"results": {"music": {"string": "r/Music"}}},
                           country="GB", suggestion_type="communities", sandbox=True)
    body = json.loads(calls[0].content)
    assert calls[0].url.path.startswith("/v2-sandbox/")
    assert body["type"] == "communities" and body["metrics"] is False
    assert len(calls) == 1
    assert len(result.keywords[0].evidence) == 1
    assert result.keywords[0].metadata["known_status"] == "provider-suggested-not-native-verified"


def test_metrics_batch_and_provider_sort_null_zero_and_positions():
    payload = {"results": {"zero": {"volume": 0}, "missing": {"volume": None}, "high": {"volume": 9}}}
    result, calls = invoke("metrics", payload, keywords=("zero", "missing", "high"), country="GB",
                           sort_by="estimated_search_volume")
    assert json.loads(calls[0].content)["keyword"] == ["zero", "missing", "high"]
    assert calls[0].url.path == "/v2/search/volume/reddit"
    assert [k.phrase for k in result.keywords] == ["high", "zero", "missing"]
    result, _ = invoke("suggestions", payload, country="GB", sort_by="suggestion_position")
    assert [k.phrase for k in result.keywords] == ["zero", "missing", "high"]


def test_provider_scope_includes_query_country_language_currency():
    first, _ = invoke("metrics", {"results": {}}, country="GB")
    other, _ = invoke("metrics", {"results": {}}, keywords=("another",), country="GB")
    assert first.metadata["scope"] != other.metadata["scope"]
    third, _ = invoke("metrics", {"results": {}}, country="GB", currency="GBP")
    assert third.metadata["scope"] != first.metadata["scope"]


@pytest.mark.parametrize("options", [{"country": "gb"}, {"country": "invalid"},
                                      {"country": "GB", "suggestion_type": "profiles"},
                                      {"country": "GB", "suggestion_type": "hashtags"},
                                      {"country": "GB", "currency": "pounds"}])
def test_provider_bad_settings_before_request(options):
    with pytest.raises(ConfigurationError):
        invoke("suggestions", lambda _: pytest.fail("Must validate before paying"), **options)


def test_provider_query_length_and_response_failures():
    with pytest.raises(ConfigurationError):
        invoke("suggestions", {}, keywords=("a" * 81,), country="GB")
    for payload in ({"error": "provider-secret"}, {"results": []}):
        with pytest.raises(OnlineSourceError) as exc:
            invoke("suggestions", payload, country="GB")
        assert "provider-secret" not in str(exc.value)
    with pytest.raises(InputError):
        invoke("metrics", {"results": {"x": {"volume": "5k"}}}, country="GB")


def test_google_reddit_search_remains_google_position_not_native_popularity():
    result, calls = invoke("web-search", {"organic_results": [
        {"title": "Paper planes", "link": "https://www.reddit.com/r/Music/comments/a/?tracking=x", "position": 1},
        {"title": "Other", "link": "https://reddit.com.attacker.invalid", "position": 2},
        {"title": "Nothing", "link": "javascript:alert(1)", "position": 3},
    ]})
    assert calls[0].url.host == "serpapi.com"
    assert calls[0].url.params["q"] == "site:reddit.com paper planes"
    assert len(result.keywords) == 1
    assert result.keywords[0].evidence[0].metric == "google_result_position"
    assert result.metadata["search_engine"] == "Google-not-Reddit"
    assert "tracking" not in result.keywords[0].metadata["url"]


@pytest.mark.parametrize("mode,expected", [("search-posts", "searchPosts"), ("search-comments", "searchComments"),
                                          ("search-communities", "searchCommunities"), ("subreddit-posts", None)])
def test_apify_start_presets_explicit_approval_budget_and_non_profile_collection(mode, expected):
    keywords = ("Music",) if mode == "subreddit-posts" else ("paper planes",)
    result, calls = invoke("apify-start", {"data": {"id": "RUN123", "status": "READY"}},
                           keywords=keywords, actor=mode, allow_paid=True, collection_authorized=True,
                           max_charge_usd=1)
    req = calls[0]
    assert req.method == "POST" and req.url.path == "/v2/acts/trudax~reddit-scraper-lite/runs"
    assert req.url.params["maxTotalChargeUsd"] == "1" and req.url.params["timeout"] == "300"
    assert req.headers["authorization"] == "Bearer provider-secret"
    body = json.loads(req.content)
    assert body["searchUsers"] is False and body["searchMedia"] is False
    assert body["skipUserPosts"] is True and body["includeNSFW"] is False
    assert body["maxItems"] == 30 and body["maxUserCount"] == 0
    assert body["includeMediaLinks"] is True
    if expected:
        assert body[expected] is True
    else:
        assert body["startUrls"] == [{"url": "https://www.reddit.com/r/music/"}]
    if mode == "search-comments":
        assert body["skipComments"] is False
        assert body["maxComments"] > 0
    assert result.metadata["run_id"] == "RUN123"
    assert result.metadata["status"] == "READY"
    assert "provider-secret" not in result.metadata["scope"]


@pytest.mark.parametrize("changes", [{"allow_paid": False}, {"collection_authorized": False},
    {"max_charge_usd": 0}, {"max_charge_usd": 101}, {"actor": "user-history"}, {"sort": "bad"},
    {"time": "forever"}, {"results_per_seed": 0}, {"actor": "subreddit-posts", "sort": "top"}])
def test_apify_start_invalid_config_never_sends(changes):
    options = {"allow_paid": True, "collection_authorized": True, "max_charge_usd": 1, **changes}
    with pytest.raises((ConfigurationError, InputError)):
        invoke("apify-start", lambda _: pytest.fail("No job may be started"), **options)


def apify_response(req, status="SUCCEEDED", act_id="ACT123", rows=None):
    path = req.url.path
    if path.endswith("trudax~reddit-scraper-lite"):
        return {"data": {"id": "ACT123"}}
    if "actor-runs" in path:
        return {"data": {"id": "RUN123", "actId": act_id, "status": status, "defaultDatasetId": "DATA123",
                         "startedAt": "2026-10-01T11:00:00Z", "finishedAt": "2026-10-01T12:00:00Z"}}
    assert path == "/v2/datasets/DATA123/items"
    return rows or [{"id": "t3_abc", "dataType": "post", "title": "Paper planes", "body": "Music",
                    "communityName": "r/Music", "upVotes": 20, "upVoteRatio": 0.9, "numberOfComments": 0,
                    "createdAt": "2026-09-30T12:00:00Z", "userName": "privateperson"}]


def test_apify_fetch_run_identity_finish_time_normalisation_and_pagination():
    result, calls = invoke("apify-fetch", apify_response, run_id="RUN123", scope="same-query",
                           dataset_kind="posts", max_records=1, include_records=True)
    assert len(calls) == 3 and all(r.method == "GET" for r in calls)
    assert result.metadata["observed_at"] == "2026-10-01T12:00:00Z"
    assert result.metadata["next_dataset_offset"] == 1
    assert result.metadata["records"][0]["provider_reported_votes"] == 20
    assert result.metadata["records"][0]["score"] is None
    assert "privateperson" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("status", ["READY", "RUNNING", "FAILED", "ABORTED", "TIMED-OUT"])
def test_apify_noncompleted_run_never_restarts_or_fetches_dataset(status):
    requests = []
    def response(req):
        requests.append(req)
        return apify_response(req, status=status)
    with pytest.raises(OnlineSourceError, match="not successfully completed"):
        invoke("apify-fetch", response, run_id="RUN123", scope="fixed")
    assert len(requests) == 2


def test_apify_run_wrong_actor_and_required_provenance():
    with pytest.raises(OnlineSourceError, match="actor"):
        invoke("apify-fetch", lambda r: apify_response(r, act_id="WRONG"), run_id="RUN123", scope="fixed")
    for opts in [{"run_id": "../bad", "scope": "fixed"}, {"run_id": "RUN123"},
                 {"run_id": "RUN123", "scope": "fixed", "dataset_kind": "users"}]:
        with pytest.raises(ConfigurationError):
            invoke("apify-fetch", lambda _: pytest.fail("Validate before requesting"), **opts)


def test_apify_comment_and_community_mappings_drop_users_and_ads():
    rows, skipped = apify_rows([
        {"id": "t1_abc", "dataType": "comment", "body": "Paper planes", "parentId": "t3_def", "upVotes": -1},
        {"dataType": "user", "userName": "private"}, {"dataType": "comment", "isAd": True},
    ], "comments")
    assert rows[0]["parent_id"] == "t3_def"
    assert skipped == 2
    output, skipped = apify_rows([{"dataType": "community", "url": "https://www.reddit.com/r/Music/",
                                  "title": "Music", "description": "Discussion", "numberOfMembers": 50}], "communities")
    assert output[0]["data"]["display_name"] == "Music"
    with pytest.raises(InputError):
        apify_rows([{"dataType": "community", "url": "https://example.com"}], "communities")
