"""Native API contract tests with real HTTPX serialization and synthetic responses."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import common
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.reddit import RedditHTTP, RedditPlugin, _scope

httpx = pytest.importorskip("httpx")
AUTH = {"api_access_approved": True, "access_token": "test-secret",
        "user_agent": "script:keywordmoves:test (by /u/example)", "limit": 1000}


def thing(identity="abc", kind="t3", **extra):
    return {"kind": kind, "data": {"id": identity, "name": kind + "_" + identity,
            "title": "Paper planes #UKGarage", "selftext": "Midnight music.", "body": "Paper planes",
            "subreddit": "Music", "score": 4, "num_comments": 0, **extra}}


def listing(*rows, after=None):
    return {"kind": "Listing", "data": {"children": list(rows), "after": after}}


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda _: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Unit tests must not open real network connections")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def invoke(operation, response, *, keywords=(), options=None):
    calls = []
    def handler(req):
        calls.append(req)
        payload = response(req) if callable(response) else response
        return payload if isinstance(payload, httpx.Response) else httpx.Response(200, json=payload)
    result = RedditPlugin(transport=httpx.MockTransport(handler)).run(
        PluginRequest(operation, tuple(keywords), options={**AUTH, **(options or {})}), ExecutionContext(None))
    return result, calls


def test_native_search_encoding_headers_fullnames_and_scope():
    result, calls = invoke("search", listing(thing()), keywords=("paper planes & music",),
                           options={"subreddit": "r/Music", "sort": "new", "time": "week"})
    req = calls[0]
    assert req.url.host == "oauth.reddit.com" and req.url.path == "/r/music/search"
    assert req.url.params["q"] == "paper planes & music"
    assert req.url.params["type"] == "link" and req.url.params["raw_json"] == "1"
    assert req.url.params["restrict_sr"] == "true"
    assert req.headers["Authorization"] == "Bearer test-secret"
    assert req.headers["User-Agent"] == AUTH["user_agent"]
    assert result.metadata["record_ids"] == ["t3_abc"]
    assert "test-secret" not in json.dumps(result.to_dict())
    assert result.metadata["collection_complete"] is False
    assert result.metadata["listing_exhausted"] is True


def test_literal_and_native_search_operators_preserved():
    _, calls = invoke("search", listing(), keywords=('flair:"Discussion" title:planes',))
    assert calls[0].url.params["q"] == 'flair:"Discussion" title:planes'
    _, calls = invoke("search", listing(), keywords=("paper planes",), options={"literal": True})
    assert calls[0].url.params["q"] == '"paper planes"'


def test_search_pagination_bounded_and_deduplicated():
    def responses(req):
        if "after" not in req.url.params:
            return listing(thing(), after="t3_abc")
        return listing(thing(), thing("def"), after="t3_def")
    result, calls = invoke("search", responses, keywords=("music",), options={"pages": 2})
    assert len(calls) == 2 and calls[1].url.params["after"] == "t3_abc"
    assert result.metadata["record_ids"] == ["t3_abc", "t3_def"]
    assert result.metadata["next_after"] == "t3_def"
    assert not result.metadata["listing_exhausted"]


def test_repeated_cursor_including_resume_is_error():
    with pytest.raises(OnlineSourceError, match="repeated"):
        invoke("search", listing(thing(), after="t3_abc"), keywords=("music",),
               options={"after": "t3_abc", "pages": 2})
    with pytest.raises(OnlineSourceError, match="repeated"):
        invoke("search", listing(thing(), after="t3_abc"), keywords=("music",), options={"pages": 3})


def test_hashtag_is_verified_literally_not_from_search_match():
    result, calls = invoke("hashtag", listing(thing(), thing("def", title="UKGarage music", selftext="")),
                           keywords=("ukgarage",))
    assert calls[0].url.params["q"] == '"#ukgarage"'
    assert result.metadata["record_ids"] == ["t3_abc"]
    assert result.metadata["search_hits_without_literal_hashtag"] == 1
    assert next(k for k in result.keywords if k.phrase == "#ukgarage").score is None
    with pytest.raises(ConfigurationError):
        invoke("hashtag", listing(), keywords=("not a single tag",))


@pytest.mark.parametrize("sort", ["new", "hot", "top", "rising", "controversial"])
def test_subreddit_feeds(sort):
    result, calls = invoke("subreddit-feed", listing(thing()), options={"subreddit": "Music", "sort": sort})
    assert calls[0].url.path == "/r/music/" + sort
    assert ("t" in calls[0].url.params) == (sort in {"top", "controversial"})
    assert result.keywords


def test_native_competition_uses_same_response_not_more_requests():
    result, calls = invoke("competition", listing(thing()), keywords=("paper planes",))
    assert len(calls) == 1
    assert result.keywords[0].relationship == "reddit-attention-context"


@pytest.mark.parametrize("operation,keywords", [("posts", ("abc", "def")),
                                                ("refresh", ("t3_abc", "t1_def"))])
def test_batch_info_missing_objects_not_zero_or_deleted(operation, keywords):
    result, calls = invoke(operation, listing(thing()), keywords=keywords)
    assert calls[0].url.path == "/api/info"
    assert calls[0].url.params["id"] == ("t3_abc,t3_def" if operation == "posts" else "t3_abc,t1_def")
    assert result.metadata["not_returned_ids"] == ["t3_def" if operation == "posts" else "t1_def"]
    assert result.metadata["removed_ids"] == []


def test_link_lookup_does_not_follow_supplied_url():
    result, calls = invoke("link-posts", listing(thing()), keywords=("https://example.com/article",))
    assert len(calls) == 1
    assert calls[0].url.host == "oauth.reddit.com"
    assert calls[0].url.params["url"] == "https://example.com/article"
    assert result.keywords


def test_comment_tree_and_explicit_morechildren():
    tree = [listing(thing()), listing(thing("def", "t1", replies=listing(thing("ghi", "t1"))),
                  {"kind": "more", "data": {"children": ["jkl"]}})]
    result, calls = invoke("comments", tree, options={"post_id": "abc"})
    assert calls[0].url.path == "/comments/abc"
    assert result.metadata["record_ids"] == ["t1_def", "t1_ghi"]
    assert result.metadata["more_comment_ids"] == ["jkl"]
    assert result.metadata["comment_tree_complete"] is False
    response = {"json": {"errors": [], "data": {"things": [thing("jkl", "t1")]}}}
    result, calls = invoke("more-comments", response, options={"post_id": "abc", "comment_ids": "jkl"})
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/api/morechildren"
    assert calls[0].url.params["link_id"] == "t3_abc"
    assert calls[0].url.params["api_type"] == "json"
    assert result.metadata["record_ids"] == ["t1_jkl"]


def test_morechildren_errors_are_clean():
    with pytest.raises(OnlineSourceError):
        invoke("more-comments", {"json": {"errors": ["private"]}},
               options={"post_id": "abc", "comment_ids": "jkl"})
    for ids in ("", "t1_jkl", "../", ",".join(["abc"] * 101)):
        with pytest.raises(ConfigurationError):
            invoke("more-comments", {}, options={"post_id": "abc", "comment_ids": ids})


def test_duplicate_submissions_exclude_parent():
    result, calls = invoke("duplicates", [listing(thing()), listing(thing("def"))], options={"post_id": "abc"})
    assert calls[0].url.path == "/duplicates/abc"
    assert result.metadata["record_ids"] == ["t3_def"]
    with pytest.raises(ConfigurationError):
        invoke("duplicates", [], options={"post_id": "abc", "pages": 2})


@pytest.mark.parametrize("op,path", [("communities", "/subreddits/search"),
                                     ("community-autocomplete", "/api/subreddit_autocomplete_v2")])
def test_community_discovery_not_user_profiles(op, path):
    row = {"kind": "t5", "data": {"display_name": "Music", "title": "Music discussion", "subscribers": 42}}
    result, calls = invoke(op, listing(row), keywords=("mus",))
    assert calls[0].url.path == path
    if op == "community-autocomplete":
        assert calls[0].url.params["include_profiles"] == "false"
    assert result.keywords[0].phrase == "r/music"


def test_community_detail_and_rules():
    result, calls = invoke("community", {"kind": "t5", "data": {"display_name": "Music", "subscribers": 100}},
                           options={"subreddit": "music"})
    assert calls[0].url.path == "/r/music/about"
    assert result.keywords[0].evidence[0].value == 100
    result, calls = invoke("community-rules", {"rules": [{"short_name": "No spam", "description": "", "kind": "link"}]},
                           options={"subreddit": "music"})
    assert calls[0].url.path == "/r/music/about/rules"
    assert result.keywords[0].metadata["description"] == ""


def test_approval_useragent_and_secret_validation(monkeypatch):
    monkeypatch.delenv("REDDIT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    for changes in [{"api_access_approved": False}, {"access_token": ""}, {"access_token": "bad\nkey"},
                    {"user_agent": "python"}, {"user_agent": "Mozilla/5.0"}]:
        with pytest.raises(ConfigurationError):
            RedditHTTP({**AUTH, **changes})
    monkeypatch.setenv("REDDIT_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("REDDIT_USER_AGENT", AUTH["user_agent"])
    client = RedditHTTP({"api_access_approved": True})
    assert client.token == "env-token"
    with pytest.raises(ConfigurationError):
        RedditHTTP({"api_access_approved": True, "access_token": ""})


def test_rate_limit_headers_stop_next_page_and_hide_secrets():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=listing(thing(), after="t3_abc"),
                              headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "99", "x-ratelimit-used": "100"})
    with pytest.raises(OnlineSourceError, match="rate-limit"):
        invoke("search", handler, keywords=("music",), options={"pages": 2})
    assert len(calls) == 1
    result, _ = invoke("search", handler, keywords=("music",))
    assert result.metadata["rate_limit"] == {"remaining": 0, "reset": 99, "used": 100}


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "invalid", "-2"])
def test_malformed_rate_header_ignored(raw):
    response = httpx.Response(200, json=listing(), headers={"x-ratelimit-remaining": raw})
    result, _ = invoke("search", response, keywords=("music",))
    assert result.metadata["rate_limit"] == {}


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 429, 500])
def test_access_errors_no_redirect_no_retry_or_response_body(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="test-secret", headers={"Location": "https://untrusted.example/"})
    with pytest.raises(OnlineSourceError) as exc:
        invoke("search", handler, keywords=("music",))
    assert len(calls) == 1
    assert "test-secret" not in str(exc.value)


@pytest.mark.parametrize("response", [{"error": 403}, {"errors": ["secret"]}, [], {"kind": "Listing", "data": {}}])
def test_malformed_and_error_payload_not_empty_success(response):
    with pytest.raises(OnlineSourceError):
        invoke("search", response, keywords=("music",))


def test_budget_and_size_limits():
    with pytest.raises(OnlineSourceError, match="budget"):
        invoke("search", listing(thing(), after="t3_abc"), keywords=("music",), options={"pages": 2, "max_requests": 1})
    with pytest.raises(OnlineSourceError, match="exceeded"):
        invoke("search", httpx.Response(200, content=b"a" * 1025), keywords=("music",), options={"max_response_bytes": 1024})
    with pytest.raises(InputError, match="max_records"):
        invoke("search", listing(thing(), thing("def")), keywords=("music",), options={"max_records": 1})


@pytest.mark.parametrize("op,options,keywords", [
    ("search", {"country": "GB"}, ("music",)), ("search", {"pages": 11}, ("music",)),
    ("search", {"sort": "invalid"}, ("music",)), ("subreddit-feed", {}, ()),
    ("community", {}, ()), ("community-rules", {}, ()), ("posts", {}, ("t1_xyz",)),
    ("refresh", {}, ("t5_xyz",)), ("community-autocomplete", {}, ("x" * 26,)),
    ("link-posts", {}, ("https://a.example/?token=secret",)),
])
def test_invalid_native_config(op, options, keywords):
    with pytest.raises(ConfigurationError):
        invoke(op, listing(), keywords=keywords, options=options)


def test_no_network_with_file_on_network_operation(tmp_path):
    with pytest.raises(ConfigurationError):
        RedditPlugin().run(PluginRequest("search", ("music",), (tmp_path / "x",), AUTH), ExecutionContext(None))


def test_scope_changes_with_collection_settings_and_no_secrets():
    req = PluginRequest("search", ("music",), options=AUTH)
    assert _scope("search", req) != _scope("search", PluginRequest("search", ("other",), options=AUTH))
    assert "test-secret" not in _scope("search", req)
    assert _scope("subreddit-feed", req) == _scope("subreddit-feed", PluginRequest("search", ("music",), options={**AUTH, "sort": "new"}))


def test_discovery_and_local_cli_without_any_optional_libraries():
    root = Path(__file__).resolve().parents[1]
    code = '''
import builtins
real_import = builtins.__import__
def checked(name, *args, **kwargs):
    if name.split('.')[0] in {'httpx','bs4','torch','spacy','nltk','openai','keybert','sentence_transformers'}:
        raise ImportError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = checked
from keywordmoves.cli import main
raise SystemExit(main(['plugins','--json']))
'''
    process = subprocess.run([sys.executable, "-c", code], cwd=root,
                             env={**os.environ, "PYTHONPATH": str(root / "src")},
                             text=True, capture_output=True, check=True)
    rows = json.loads(process.stdout)
    plugin = next(r for r in rows if r["name"] == "reddit")
    assert plugin["kind"] == "keyword" and len(plugin["operations"]) == 27
