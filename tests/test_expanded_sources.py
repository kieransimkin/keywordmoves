"""Synthetic provider contracts; no billable calls or song evidence."""
import json

import pytest

from keywordmoves.errors import ConfigurationError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.expanded_sources import (
    ITunesCatalogPlugin,
    SERankingPlugin,
    WikimediaPageviewsPlugin,
)
from keywordmoves.registry import PluginRegistry

httpx = pytest.importorskip("httpx")


def run(plugin, operation, handler, options=None, keywords=("example music",)):
    return plugin(transport=httpx.MockTransport(handler)).run(
        PluginRequest(operation, keywords=keywords, options=options or {}), ExecutionContext(None))


def test_discovery():
    assert {"seranking", "wikimedia-pageviews"} <= set(PluginRegistry().names())


@pytest.mark.parametrize("operation", ["metrics", "related", "similar", "questions", "longtail"])
def test_seranking_request_and_metric_identity(operation):
    sent = []
    def handler(request):
        sent.append(request)
        assert request.headers["Authorization"] == "Token synthetic-secret"
        assert request.url.params["source"] == "uk"
        if operation == "longtail":
            assert set(request.url.params) == {"source", "keyword", "limit", "offset"}
            return httpx.Response(200, json={"total": 300, "keywords": ["example music for adults"]})
        row = {"keyword": "example music", "volume": 120, "competition": .2, "difficulty": 17, "cpc": .5}
        return httpx.Response(200, json=[row] if operation == "metrics" else {"total": 300, "keywords": [row]})
    result = run(SERankingPlugin, operation, handler, {"source": "uk", "limit": 2, "api_key": "synthetic-secret",
                   "paid_requests_approved": True, "approved_max_credits": 100})
    assert len(sent) == 1
    assert "synthetic-secret" not in json.dumps(result.to_dict())
    expected = 100 if operation == "metrics" else 2 if operation == "longtail" else 20
    assert result.metadata["documented_request_credit_bound"] == expected
    if operation == "metrics":
        assert json.loads(sent[0].content) == {"keywords": ["example music"]}
    elif operation == "longtail":
        assert not result.keywords[0].evidence
    else:
        assert {e.metric for e in result.keywords[0].evidence} >= {"paid_competition", "organic_difficulty"}


@pytest.mark.parametrize("options", [{}, {"paid_requests_approved": True, "approved_max_credits": 1, "limit": 2}])
def test_seranking_approval_and_cost_guard_make_no_request(options):
    def handler(_request):
        pytest.fail("Guard must fail before network request")
    with pytest.raises(ConfigurationError):
        run(SERankingPlugin, "related", handler, options)


def test_seranking_unavailable_not_zero():
    result = run(SERankingPlugin, "metrics", lambda _: httpx.Response(200, json=[
        {"keyword": "example music", "is_data_found": False, "volume": 0}]),
        {"source": "uk", "paid_requests_approved": True, "approved_max_credits": 100, "api_key": "synthetic-secret"})
    assert all(e.value is None for e in result.keywords[0].evidence)
    assert result.keywords[0].metadata["availability"] == "unavailable"


def test_seranking_rejects_invalid_metric():
    with pytest.raises(OnlineSourceError):
        run(SERankingPlugin, "metrics", lambda _: httpx.Response(200, json=[
            {"keyword": "example", "competition": 2}]),
            {"source": "uk", "paid_requests_approved": True, "approved_max_credits": 100, "api_key": "synthetic-secret"})


def test_pageviews_keeps_explicit_zero_and_missing_days_distinct():
    def handler(request):
        assert str(request.url).endswith("/Example_topic/daily/2026090100/2026090300")
        return httpx.Response(200, json={"items": [
            {"timestamp": "2026090100", "views": 0}, {"timestamp": "2026090300", "views": 9}]})
    result = run(WikimediaPageviewsPlugin, "article", handler,
                 {"start_date": "2026-09-01", "end_date": "2026-09-03"}, ("Example topic",))
    item = result.keywords[0]
    assert item.evidence[0].value == 9 and item.evidence[0].unit == "pageviews"
    assert item.metadata["days_returned"] == 2 and not item.metadata["complete_window"]
    assert item.metadata["daily_series"][0]["views"] == 0
    assert "not search volume" in WikimediaPageviewsPlugin.descriptor.summary


@pytest.mark.parametrize("rows", [
    [{"timestamp": "2026090100", "views": 1}, {"timestamp": "2026090100", "views": 2}],
    [{"timestamp": "2026090100", "views": -1}],
    [{"timestamp": "2026090100", "views": 1, "agent": "bot"}],
    [{"timestamp": "2026090100", "views": True}],
])
def test_pageviews_rejects_scope_and_value_errors(rows):
    with pytest.raises(OnlineSourceError):
        run(WikimediaPageviewsPlugin, "article", lambda _: httpx.Response(200, json={"items": rows}),
            {"start_date": "2026-09-01", "end_date": "2026-09-03"})


def test_pageviews_empty_is_unavailable():
    result = run(WikimediaPageviewsPlugin, "article", lambda _: httpx.Response(200, json={"items": []}),
                 {"start_date": "2026-09-01", "end_date": "2026-09-03"})
    assert result.keywords[0].evidence[0].value is None
    assert result.keywords[0].metadata["availability"] == "unavailable"


def test_pageviews_live_project_identifier_without_domain_suffix():
    result = run(WikimediaPageviewsPlugin, "article", lambda _: httpx.Response(200, json={"items": [
        {"project": "en.wikipedia", "article": "Example_topic", "access": "all-access", "agent": "user",
         "granularity": "daily", "timestamp": "2026090100", "views": 223}]}),
        {"start_date": "2026-09-01", "end_date": "2026-09-01"}, ("Example topic",))
    assert result.keywords[0].metadata["complete_window"]


def test_itunes_catalogue_keeps_titles_genres_without_popularity_or_media():
    def handler(request):
        assert dict(request.url.params) == {"term": "example music", "country": "GB", "media": "music",
                                           "entity": "song", "attribute": "artistTerm", "limit": "20"}
        return httpx.Response(200, json={"resultCount": 1, "results": [{"wrapperType": "track", "kind": "song",
            "trackName": "Example", "artistName": "Example Artist", "primaryGenreName": "Pop",
            "previewUrl": "https://media.example/preview", "artworkUrl100": "https://media.example/cover"}]})
    result = run(ITunesCatalogPlugin, "songs", handler, {"country": "GB", "attribute": "artistTerm"})
    assert result.keywords[0].phrase == "Example"
    assert result.keywords[0].metadata["genre"] == "Pop"
    assert not result.keywords[0].evidence
    assert "previewUrl" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("payload", [{"resultCount": 1, "results": []},
    {"resultCount": 1, "results": [{"wrapperType": "track", "kind": "music-video", "trackName": "Example"}]}])
def test_itunes_rejects_wrong_entity_and_inconsistent_count(payload):
    with pytest.raises(OnlineSourceError):
        run(ITunesCatalogPlugin, "songs", lambda _: httpx.Response(200, json=payload), {"country": "GB"})
