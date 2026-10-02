"""Exercise serialized HTTP requests with synthetic responses; never call live APIs."""
from __future__ import annotations

import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.instagram import ACTORS, MEDIA_FIELDS, OWN_FIELDS, InstagramPlugin

httpx = pytest.importorskip("httpx")
AUTH = {"access_token":"synthetic-secret", "user_id":"123", "graph_version":"v26.0"}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def run(op, handler, *, keywords=(), **options):
    return InstagramPlugin(transport=httpx.MockTransport(handler)).run(
        PluginRequest(op, keywords=keywords, options=options), ExecutionContext(None))


def meta(op, handler, *, keywords=(), **options):
    return run(op, handler, keywords=keywords, **{**AUTH, **options})


def val(item):
    return {e.metric:e.value for e in item.evidence}


def test_exact_hashtag_lookup_both_samples_dedup_and_no_global_count():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer synthetic-secret"
        assert "synthetic-secret" not in str(req.url)
        assert req.url.host == "graph.facebook.com"
        if req.url.path.endswith("ig_hashtag_search"):
            assert req.url.params["q"] == "music" and req.url.params["user_id"] == "123"
            return httpx.Response(200,json={"data":[{"id":"456"}]})
        assert req.url.params["fields"] == MEDIA_FIELDS
        assert "timestamp" not in req.url.params["fields"]
        assert "username" not in req.url.params["fields"]
        return httpx.Response(200,json={"data":[{"id":"1", "caption":"#music #ukg", "like_count":0,"comments_count":None}]})
    result = meta("hashtag", handler, keywords=("#Music",))
    assert len(calls) == 3
    assert result.metadata["duplicates_removed"] == 1
    items = {i.phrase:i for i in result.keywords}
    assert items["#music"].metadata["verification"] == "meta-resolved"
    assert items["#ukg"].metadata["verification"] == "observed-in-sample"
    assert val(items["#music"])["sampled_post_count"] == 1
    assert val(items["#music"])["sample_likes_mean"] == 0
    assert "reported_post_count" not in val(items["#music"])
    assert val(items["#music"])["sample_posts_last_24h"] is None
    assert "synthetic-secret" not in json.dumps(result.to_dict())


def test_no_hashtag_id_does_not_mean_banned_or_zero_population():
    result = meta("hashtag",lambda _:httpx.Response(200,json={"data":[]}),keywords=("unknown",))
    assert result.metadata["request_count"] == 1
    assert result.keywords[0].metadata["verification"] == "unresolved-by-api"
    assert "reported_post_count" not in val(result.keywords[0])


@pytest.mark.parametrize("error_code", [4,10,100,190,200,613])
def test_graph_errors_are_clean_not_ban_classifications(error_code):
    def handler(req):
        return httpx.Response(400,json={"error":{"code":error_code,"message":"synthetic-secret","fbtrace_id":"private"}})
    with pytest.raises(OnlineSourceError) as exc:
        meta("hashtag",handler,keywords=("music",))
    assert "synthetic-secret" not in str(exc.value)
    assert "does not prove" in str(exc.value)


@pytest.mark.parametrize("status", [301,302,307,401,403,429,500])
def test_no_redirects_retries_or_secret_echo(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status,headers={"Location":"https://evil.example/"},text="synthetic-secret")
    with pytest.raises(OnlineSourceError) as exc:
        meta("quota",handler)
    assert len(calls) == 1
    assert "synthetic-secret" not in str(exc.value)


def test_pagination_uses_only_trusted_cursor_and_keeps_sampling_metadata():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.host == "graph.facebook.com"
        if req.url.path.endswith("ig_hashtag_search"):
            return httpx.Response(200,json={"data":[{"id":"456"}]})
        after = req.url.params.get("after")
        if after is None:
            return httpx.Response(200,json={"data":[{"id":"1","caption":"#a"}],
                "paging":{"next":"https://evil.example/steal?access_token=synthetic-secret","cursors":{"after":"TOKEN1"}}})
        assert after == "TOKEN1"
        return httpx.Response(200,json={"data":[{"id":"2","caption":"#b"}]})
    result = meta("hashtag",handler,keywords=("music",),edge="recent",pages=2,page_size=1)
    assert len(calls) == 3
    assert result.metadata["sample_size"] == 2
    assert result.metadata["collections"][0]["more_available"] is False


def test_more_available_flag_when_page_budget_reached():
    def handler(req):
        if req.url.path.endswith("ig_hashtag_search"):
            return httpx.Response(200,json={"data":[{"id":"456"}]})
        return httpx.Response(200,json={"data":[{"id":"1"}],"paging":{"next":"unsafe","cursors":{"after":"next"}}})
    result = meta("hashtag",handler,keywords=("music",),edge="top")
    assert result.metadata["collections"][0]["more_available"]


@pytest.mark.parametrize("cursor", ["a){id}", "https://evil.example", "x\nAuthorization:y"])
def test_unsafe_cursors_rejected(cursor):
    def handler(req):
        return httpx.Response(200,json={"data":[{"id":"1"}],"paging":{"next":"x","cursors":{"after":cursor}}})
    with pytest.raises(OnlineSourceError,match="cursor"):
        meta("quota",handler,pages=3)


def test_repeated_cursor_stops_loop():
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={"data":[],"paging":{"next":"x","cursors":{"after":"same"}}})
    with pytest.raises(OnlineSourceError,match="repeated"):
        meta("quota",handler,pages=3)
    assert len(calls) == 2


@pytest.mark.parametrize("op,options", [("hashtag",{}),("quota",{}),("account",{"username":"artist"}),
    ("account",{"media_edge":"tags"})])
def test_facebook_only_capabilities_not_silently_rerouted(op,options):
    with pytest.raises(ConfigurationError,match="facebook"):
        meta(op,lambda _:pytest.fail("must not query"),keywords=("x",) if op=="hashtag" else (),
             login="instagram",**options)


def test_hashtag_batch_budget_checked_before_requests():
    with pytest.raises(ConfigurationError,match="max_requests"):
        meta("hashtag",lambda _:pytest.fail("must not query"), keywords=("a","b"),max_requests=3)


def test_quota_is_returned_with_partial_flag_not_false_remaining_count():
    result=meta("quota",lambda _:httpx.Response(200,json={"data":[{"id":"5","name":"music"}],
        "paging":{"next":"next","cursors":{"after":"token"}}}))
    assert result.metadata["queried_hashtag_ids"] == ["5"]
    assert result.metadata["queried_hashtags"] == ["#music"]
    assert result.metadata["quota_list_truncated"]
    assert "remaining" not in result.metadata


@pytest.mark.parametrize("login,host", [("facebook","graph.facebook.com"),("instagram","graph.instagram.com")])
def test_owned_account_profile_and_media(login,host):
    def handler(req):
        assert req.url.host == host
        if req.url.path.endswith("/123"):
            return httpx.Response(200,json={"id":"123","username":"artist","followers_count":100,"media_count":8})
        assert req.url.path.endswith("/123/media")
        assert req.url.params["fields"] == OWN_FIELDS
        return httpx.Response(200,json={"data":[{"id":"1","caption":"#music","like_count":8,"comments_count":2,
             "timestamp":"2026-10-01T12:00:00Z","media_type":"VIDEO","media_product_type":"REELS"}]})
    result=meta("account",handler,login=login,include_media=True)
    assert result.metadata["profile"]["followers_count"] == 100
    assert val(result.keywords[0])["sample_follower_engagement_percent_mean"] == 10
    assert len(result.metadata["media"]) == 1


def test_tagged_media_not_divided_by_profile_owners_followers():
    def handler(req):
        if req.url.path.endswith("/123"):
            return httpx.Response(200,json={"id":"123","username":"artist","followers_count":100,"media_count":8})
        assert req.url.path.endswith("/tags")
        return httpx.Response(200,json={"data":[{"id":"1","caption":"#music","like_count":8,"comments_count":2}]})
    result=meta("account",handler,media_edge="tags")
    assert val(result.keywords[0])["sample_follower_engagement_percent_mean"] is None


def test_business_discovery_paging_nested_fields():
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.path.endswith("/123")
        fields=req.url.params["fields"]
        assert "business_discovery.username(artist)" in fields
        page={"data":[{"id":str(len(calls)),"caption":"#music"}]}
        if len(calls)==1:
            page["paging"]={"next":"https://evil.example/","cursors":{"after":"CURSOR"}}
        else:
            assert ".after(CURSOR)" in fields
        return httpx.Response(200,json={"business_discovery":{"username":"artist","followers_count":100,
                                    "media_count":2,"media":page}})
    result=meta("account",handler,username="artist",pages=2,page_size=1)
    assert result.metadata["sample_size"]==2
    assert len(calls)==2


def test_media_insights_queries_independently_and_preserves_unavailable():
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/42"):
            return httpx.Response(200,json={"id":"42","caption":"#music #ukg","like_count":4,"comments_count":0})
        metric=req.url.params["metric"]
        if metric=="views":
            return httpx.Response(200,json={"data":[{"name":metric,"period":"lifetime","values":[{"value":0}]}]})
        if metric=="reach":
            return httpx.Response(200,json={"data":[{"name":metric,"period":"lifetime","total_value":{"value":20}}]})
        if metric=="saved":
            return httpx.Response(400,json={"error":{"code":100,"message":"not available"}})
        return httpx.Response(200,json={"data":[]})
    result=meta("media-insights",handler,media_id="42")
    assert len(calls)==5
    ins=result.metadata["media_insights"]
    assert ins["views"]["value"]==0 and ins["reach"]["value"]==20
    assert ins["saved"]["value"] is None and ins["shares"]["value"] is None
    assert "whole media item" in result.metadata["insight_note"]


def test_insight_permission_error_not_treated_as_optional_metric():
    def handler(req):
        if req.url.path.endswith("insights"):
            return httpx.Response(400,json={"error":{"code":190,"message":"expired"}})
        return httpx.Response(200,json={"id":"1","caption":"#x"})
    with pytest.raises(OnlineSourceError):
        meta("media-insights",handler,media_id="1",metrics="views")


def test_comments_are_text_samples_not_hashtag_engagement():
    result=meta("comments",lambda req:httpx.Response(200,json={"data":[{"id":"1","text":"Love #music","timestamp":"2026-10-01"}]}),media_id="42")
    assert val(result.keywords[0]) == {"sampled_text_count":1}
    assert result.metadata["sample_kind"] == "comments"


@pytest.mark.parametrize("actor", list(ACTORS))
def test_apify_start_actor_contract_cap_and_single_request(actor):
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.method == "POST"
        assert req.url.path == "/v2/actors/" + ACTORS[actor] + "/runs"
        assert req.headers["Authorization"] == "Bearer synthetic-secret"
        assert req.url.params["maxTotalChargeUsd"] == "1"
        assert req.url.params["waitForFinish"] == "0"
        assert req.url.params["restartOnError"] == "false"
        body=json.loads(req.content)
        if actor=="hashtag-stats":
            assert body=={"hashtags":["music"],"includeTopPosts":False,"includeLatestPosts":False}
        elif actor in {"hashtag-posts","keyword-posts"}:
            assert body["keywordSearch"] is (actor=="keyword-posts")
            assert body["resultsLimit"]==50
        else:
            assert body["searchType"]==("hashtag" if actor=="hashtag-search" else "popular")
        return httpx.Response(201,json={"data":{"id":"run1","status":"READY"}})
    result=run("apify-start",handler,keywords=("music",),actor=actor,apify_token="synthetic-secret",allow_paid=True)
    assert len(calls)==1
    assert result.metadata["status"]=="submitted"
    assert "synthetic-secret" not in json.dumps(result.to_dict())


def test_apify_explicit_spending_optin_required():
    with pytest.raises(ConfigurationError,match="allow_paid"):
        run("apify-start",lambda _:pytest.fail("no paid request"),keywords=("music",),apify_token="key")


@pytest.mark.parametrize("status", ["READY","RUNNING","TIMING-OUT","ABORTING"])
def test_running_jobs_not_reported_as_empty_success(status):
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.path=="/v2/actor-runs/run1"
        return httpx.Response(200,json={"data":{"id":"run1","status":status}})
    result=run("apify-fetch",handler,run_id="run1",apify_token="key",scope="music:recent")
    assert len(calls)==1
    assert result.metadata["collection_complete"] is False
    assert not result.keywords


@pytest.mark.parametrize("status", ["FAILED","TIMED-OUT","ABORTED","unknown"])
def test_failed_jobs_not_imported(status):
    with pytest.raises(OnlineSourceError):
        run("apify-fetch",lambda _:httpx.Response(200,json={"data":{"status":status}}),
            run_id="run1",apify_token="key",scope="music")


def test_apify_finished_run_imports_stats_related_and_nested_media():
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("run1"):
            return httpx.Response(200,json={"data":{"status":"SUCCEEDED","defaultDatasetId":"set1","actId":"actor1",
                                                      "finishedAt":"2026-10-02T10:00:00Z"}})
        assert req.url.path=="/v2/datasets/set1/items"
        assert req.url.params["skipEmpty"]=="false"
        assert "clean" not in req.url.params
        return httpx.Response(200,json=[{"name":"music","postsCount":10000,"posts":"10 K",
            "related":[{"hash":"#ukg","info":"2k"}],
            "topPosts":[{"id":"1","caption":"#music #local","likesCount":-1}],
            "latestPosts":[{"id":"1","caption":"#music #local","likesCount":-1}]}])
    result=run("apify-fetch",handler,run_id="run1",apify_token="key",scope="music",dataset_kind="hashtags")
    assert result.metadata["observed_at"]=="2026-10-02T10:00:00+00:00"
    assert result.metadata["duplicates_removed"]==1
    tags={i.phrase:i for i in result.keywords}
    assert val(tags["#music"])["reported_post_count"]==10000
    assert val(tags["#music"])["sample_likes_mean"] is None
    assert "#ukg" in tags and "#local" in tags
    assert result.metadata["collection_complete"]
    assert len(calls)==2


def test_apify_offset_pages_and_explicit_partial_flag():
    calls=[]
    def handler(req):
        calls.append(req)
        offset=int(req.url.params["offset"])
        assert offset==len(calls)-1
        return httpx.Response(200,json=[{"id":str(offset),"caption":"#music"}])
    result=run("apify-fetch",handler,dataset_id="set1",scope="music",observed_at="2026-10-02",
               page_size=1,pages=2,apify_token="key")
    assert result.metadata["more_may_be_available"]
    assert result.metadata["sample_size"]==2
    assert not result.metadata["collection_complete"]


def test_apify_fetch_does_not_accept_both_ids():
    with pytest.raises(ConfigurationError,match="exactly one"):
        run("apify-fetch",lambda _:pytest.fail("no query"),dataset_id="set1",run_id="run1",scope="music",
            observed_at="2026-10-02",apify_token="key")


@pytest.mark.parametrize("nested", [None,{},[None],[1]])
def test_apify_invalid_nested_media_is_clean_error(nested):
    with pytest.raises(InputError):
        run("apify-fetch",lambda _:httpx.Response(200,json=[{"name":"music","topPosts":nested}]),
            dataset_id="set1",scope="music",observed_at="2026-10-02",dataset_kind="hashtags",apify_token="key")


def test_public_html_robots_denial_stops_before_page():
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.path=="/robots.txt"
        return httpx.Response(200,text="User-agent: *\nDisallow: /\n")
    with pytest.raises(OnlineSourceError):
        run("public-page",handler,keywords=("music",),allow_web=True)
    assert len(calls)==1


def test_public_html_is_optin_and_metadata_parser_is_explicit():
    def handler(req):
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /\n")
        assert req.url.path=="/explore/tags/music/"
        return httpx.Response(200,text='<meta property="og:url" content="https://www.instagram.com/explore/tags/music/">'
                             '<meta property="og:description" content="12.5M posts - see photos">')
    with pytest.raises(ConfigurationError,match="allow_web"):
        run("public-page",handler,keywords=("music",))
    result=run("public-page",handler,keywords=("music",),allow_web=True)
    assert val(result.keywords[0])["reported_post_count"]==12500000
    assert result.keywords[0].metadata["count_is_approximate"]


@pytest.mark.parametrize("business", [False, True])
def test_optional_profile_text_is_not_counted_as_a_media_post(business):
    def handler(req):
        fields=req.url.params.get("fields", "")
        if "business_discovery" in fields:
            assert "biography" in fields
            return httpx.Response(200,json={"business_discovery":{"username":"artist","biography":"Music #bio",
                "name":"Indie Singer","followers_count":10,"media_count":0,"media":{"data":[]}}})
        if req.url.path.endswith("/123"):
            assert "biography" in fields
            return httpx.Response(200,json={"username":"artist","biography":"Music #bio","name":"Indie Singer","followers_count":10,"media_count":0})
        return httpx.Response(200,json={"data":[]})
    result=meta("account",handler,include_profile=True,include_keywords=True,**({"username":"artist"} if business else {}))
    items={i.phrase:i for i in result.keywords}
    assert "indie singer" in items
    assert val(items["#bio"])=={"sampled_text_count":1}
    assert result.metadata["sample_size"]==0
    assert "biography" not in result.metadata["profile"]
