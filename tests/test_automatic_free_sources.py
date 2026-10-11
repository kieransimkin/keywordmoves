"""Synthetic public contracts; no live requests, account data or real captures."""
import json
from dataclasses import replace

import httpx
import pytest
from test_free_public_sources import KE_URL, YT_URL, ke_html, yt_html
from test_vidiq_public import widget as vidiq_html

from keywordmoves.browser_capture import analyse_capture
from keywordmoves.builtin.research_batch import ResearchBatchPlugin
from keywordmoves.builtin.vidiq import VidiqPlugin
from keywordmoves.builtin.ytkeywordtool import YTKeywordToolPlugin
from keywordmoves.errors import ConfigurationError, InputError, KeywordMovesError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.commercial import KeywordsEverywherePlugin
from keywordmoves.online.common import OnlineSourceError


def payload(**values):
    return {"input": "example song", "seed": "example song", "count": 1,
        "keywords": [{"term": "example song lyrics", "tier": "High", "category": "Related", "score": 2.5}], **values}


def http_run(data=None, robots="User-agent: *\nAllow: /", status=200, **options):
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(status, json=payload() if data is None else data)
    plugin = YTKeywordToolPlugin(transport=httpx.MockTransport(handle))
    return plugin.run(PluginRequest("collect-public", ("example song",), options=options), ExecutionContext(None)), seen


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)


def test_automatic_query_checks_policy_and_retains_native_meaning():
    result, seen = http_run()
    assert len(seen) == 2 and seen[1].url.path == "/api/keywords"
    assert seen[1].url.params["q"] == "example song"
    assert not any("authorization" in x.headers or "cookie" in x.headers for x in seen)
    row = result.keywords[0]
    assert row.score is None and row.metadata["provider_batch_score"] == 2.5
    assert row.evidence[0].value == "High" and row.evidence[0].geography is None
    assert result.metadata["network_requests"] == 2 and result.metadata["paid_calls"] == 0
    assert result.metadata["provider_updated_at"] is None


def test_provider_effective_seed_is_separate_and_empty_result_is_explicit():
    result, _ = http_run(payload(seed="song"))
    assert result.metadata["requested_seed"] == "example song" and result.metadata["seed"] == "song"
    result, _ = http_run(payload(count=0, keywords=[]))
    assert not result.keywords and result.metadata["rows_received"] == 0


@pytest.mark.parametrize("data", [payload(input="different song"), payload(count=2), payload(count=True),
    payload(keywords="wrong"), payload(seed=None), payload(error="upstream failed"),
    payload(keywords=[{"term": "phrase", "category": "Related", "tier": "Very high"}]),
    payload(keywords=[{"term": "phrase", "category": "Generated", "tier": "High"}]),
    payload(keywords=[{"term": "phrase", "category": "Related", "tier": "High", "score": -1}]),
    payload(keywords=[{"term": "phrase", "category": "Related", "tier": "High", "score": True}]),
    payload(count=2, keywords=payload()["keywords"] * 2)])
def test_changed_stale_or_ambiguous_query_rejected(data):
    with pytest.raises(KeywordMovesError):
        http_run(data)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 502])
def test_query_access_failure_does_not_retry(status):
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nAllow: /") if len(seen) == 1 else httpx.Response(status)
    with pytest.raises(OnlineSourceError):
        YTKeywordToolPlugin(transport=httpx.MockTransport(handler)).run(PluginRequest("collect-public", ("example song",)), ExecutionContext(None))
    assert seen == ["/robots.txt", "/api/keywords"]


@pytest.mark.parametrize("robots", ["User-agent: *\nDisallow: /api/", "<html>challenge</html>"])
def test_robots_denial_stops_before_query(robots):
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text=robots)
    with pytest.raises(OnlineSourceError):
        YTKeywordToolPlugin(transport=httpx.MockTransport(handler)).run(PluginRequest("collect-public", ("example song",)), ExecutionContext(None))
    assert seen == ["/robots.txt"]


@pytest.mark.parametrize("options", [{"api_key": "unused"}, {"source_url": "https://elsewhere.invalid/"},
    {"max_requests": 1}, {"limit": False}, {"country": "US"}])
def test_query_bad_options_fail_before_network(options):
    with pytest.raises(ConfigurationError):
        http_run(**options)


def test_limit_does_not_promote_order_to_demand():
    data = payload(count=2, keywords=[*payload()["keywords"], {"term": "example song live", "category": "Related", "tier": "Low"}])
    result, _ = http_run(data, limit=1)
    assert len(result.keywords) == 1 and result.metadata["output_truncated"]
    assert result.metadata["rows_received"] == 2


def test_vidiq_robots_429_has_no_page_fetch_or_browser_fallback():
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(429)
    with pytest.raises(OnlineSourceError, match="429"):
        VidiqPlugin(transport=httpx.MockTransport(handler)).run(PluginRequest("collect-public"), ExecutionContext(None))
    assert seen == ["/robots.txt"]


def test_vidiq_public_fetch_reuses_parser_in_memory():
    def handler(request):
        return httpx.Response(200, text="User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else vidiq_html())
    result = VidiqPlugin(transport=httpx.MockTransport(handler)).run(PluginRequest("collect-public"), ExecutionContext(None))
    assert result.operation == "collect-public" and result.metadata["network_requests"] == 2
    assert result.metadata["input_path"] is None and result.metadata["independent_of_vidiq"] is False
    assert result.keywords[0].score is None


def capture(source="keywords-everywhere", **values):
    return {"schema": "keywordmoves-public-browser/v1", "source": source,
        "source_url": KE_URL if source == "keywords-everywhere" else YT_URL,
        "observed_at": "2026-10-11", "seed": "example song",
        "html": ke_html() if source == "keywords-everywhere" else yt_html(),
        "collection": {"stopped_early": True, "displayed_total": 1,
            **({"displayed_country": ""} if source == "keywords-everywhere" else {})},
        **({"country": "GLB", "currency": "USD"} if source == "keywords-everywhere" else {}), **values}


@pytest.mark.parametrize("source", ["ytkeywordtool", "keywords-everywhere"])
def test_browser_handoff_is_in_memory_and_source_specific(source):
    result = analyse_capture(capture(source))
    assert result.operation == "collect-browser" and result.metadata["manual_html_required"] is False
    assert result.metadata["input_path"] is None and result.metadata["network_requests"] is None
    assert result.keywords[0].score is None
    assert result.keywords[0].metadata["requested_seed"] == "example song"
    if source == "keywords-everywhere":
        assert result.keywords[0].metadata["platform"] == "Google"
        assert result.metadata["youtube_search_volume"] is False


@pytest.mark.parametrize("values", [{"source_url": "https://bad.invalid/"}, {"page_url": KE_URL + "?job=no"},
    {"page_url": KE_URL + "?job=00000000-0000-0000-0000-000000000000&country=us"}, {"html": "<html>wrong</html>"},
    {"country": "US"}, {"currency": "GBP"}, {"observed_at": "2099-01-01"},
    {"collection": {"stopped_early": True, "displayed_country": "", "displayed_total": 0}},
    {"collection": {"stopped_early": "yes", "displayed_total": 1}}, {"cookie": "forbidden"}])
def test_browser_handoff_guards(values):
    with pytest.raises(KeywordMovesError):
        analyse_capture(capture(**values))


def test_source_job_url_allowed_but_original_url_remains_canonical():
    result = analyse_capture(capture(page_url=KE_URL + "?job=00000000-0000-0000-0000-000000000000"))
    assert result.metadata["source_url"] == KE_URL


def test_hidden_old_success_does_not_become_browser_evidence():
    data = capture("ytkeywordtool", html='<div class="hidden">' + yt_html() + '</div>')
    with pytest.raises(InputError):
        analyse_capture(data)


def test_browser_provider_rewritten_seed_keeps_requested_input_separate():
    result = analyse_capture(capture("ytkeywordtool", effective_seed="provider core topic"))
    assert result.metadata["requested_seed"] == "example song"
    assert result.keywords[0].metadata["seed"] == "provider core topic"


def test_browser_driver_composes_without_manual_files():
    class Driver:
        def collect(self, source, request):
            return capture(source)
    context = ExecutionContext(None, public_browser=Driver())
    result = KeywordsEverywherePlugin().run(PluginRequest("collect-browser", ("example song",)), context)
    assert len(result.keywords) == 1
    with pytest.raises(InputError):
        KeywordsEverywherePlugin().run(PluginRequest("collect-browser", ("wrong song",)), context)
    with pytest.raises(ConfigurationError, match="driver"):
        KeywordsEverywherePlugin().run(PluginRequest("collect-browser", ("example song",)), ExecutionContext(None))


def test_batch_free_collection_options_and_unavailable_tasks(tmp_path, monkeypatch):
    from keywordmoves.registry import PluginRegistry
    original = PluginRegistry.get
    def get(self, name):
        if name == "ytkeywordtool":
            class FreeSource:
                def run(self, request, context):
                    assert request.options["max_requests"] == 2
                    return replace(analyse_capture(capture("ytkeywordtool")), operation="collect-public")
            return FreeSource()
        return original(self, name)
    monkeypatch.setattr(PluginRegistry, "get", get)
    tasks = [{"id": "one", "subject": "synthetic", "plugin": "ytkeywordtool", "operation": "collect-public", "keywords": ["example song"]},
        {"id": "two", "subject": "synthetic", "plugin": "keywords-everywhere", "operation": "collect-browser", "keywords": ["example song"]}]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": tasks}), "utf-8")
    result = ResearchBatchPlugin().run(PluginRequest("collect", inputs=(path,)), ExecutionContext(None))
    assert len(result.keywords) == 1 and result.metadata["unavailable_tasks"] == 1
    tasks[1]["operation"] = "metrics"
    path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": tasks}), "utf-8")
    with pytest.raises(InputError, match="metered"):
        ResearchBatchPlugin().run(PluginRequest("validate-plan", inputs=(path,)), ExecutionContext(None))
