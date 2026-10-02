from __future__ import annotations

import socket
from pathlib import Path

import pytest

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import factories
from keywordmoves.online.common import ConfigurationError, OnlineSourceError
from keywordmoves.online.websites import parse_html, public_url

httpx = pytest.importorskip("httpx")
pytest.importorskip("bs4")

FIXTURES = Path(__file__).parent / "fixtures" / "online"


@pytest.fixture(autouse=True)
def no_wait_and_public_dns(monkeypatch):
    monkeypatch.setattr("keywordmoves.online.common.time.sleep", lambda _: None)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])


def network(name, operation, options, handler):
    plugin = factories()[name](transport=httpx.MockTransport(handler))
    return plugin.run(PluginRequest(operation, keywords=("paper & planes",), options=options), ExecutionContext(None))


@pytest.mark.parametrize("name,host,query_param", [
    ("google-autocomplete", "suggestqueries.google.com", "q"),
    ("bing-autocomplete", "api.bing.com", "query"),
    ("duckduckgo-autocomplete", "ac.duckduckgo.com", "q"),
])
def test_browser_endpoint_queries_and_robots(name, host, query_param):
    requests = []
    def handler(req):
        requests.append(req)
        assert req.url.host == host
        if req.url.path == "/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /\n")
        assert req.url.params[query_param] == "paper & planes"
        return httpx.Response(200,content=(FIXTURES/"autocomplete.json").read_bytes())
    result = network(name, "suggestions", {"allow_unofficial":True}, handler)
    assert len(requests) == 2
    assert result.metadata["access"] == "unofficial-web-endpoint"
    assert result.metadata["request_count"] == 2
    assert len(result.keywords) == 2


@pytest.mark.parametrize("name", ["google-autocomplete","bing-autocomplete","duckduckgo-autocomplete"])
def test_unofficial_endpoints_require_explicit_opt_in(name):
    with pytest.raises(ConfigurationError, match="allow_unofficial"):
        network(name,"suggestions",{},lambda _: pytest.fail("must not request"))


@pytest.mark.parametrize("policy", ["User-agent: *\nDisallow: /\n", "<html>challenge</html>",
                                    "User-agent: *\nCrawl-delay: 120\n"])
def test_robots_failures_stop_before_search(policy):
    requests = []
    def handler(req):
        requests.append(req)
        assert req.url.path == "/robots.txt"
        return httpx.Response(200,text=policy)
    with pytest.raises(OnlineSourceError):
        network("google-autocomplete","suggestions",{"allow_unofficial":True},handler)
    assert len(requests) == 1


@pytest.mark.parametrize("status", [301,302,401,403,429,500])
def test_robots_failure_status_is_not_allow_all(status):
    with pytest.raises(OnlineSourceError):
        network("bing-autocomplete","suggestions",{"allow_unofficial":True},lambda _: httpx.Response(status))


@pytest.mark.parametrize("status", [404,410])
def test_missing_robots_allows_only_the_bounded_search(status):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(status)
        return httpx.Response(200,json=["paper",[]])
    result = network("duckduckgo-autocomplete","suggestions",{"allow_unofficial":True},handler)
    assert result.keywords == ()


def test_direct_endpoint_malformed_success_is_not_empty():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200,json={"error":"challenge"})
    with pytest.raises(OnlineSourceError):
        network("google-autocomplete","suggestions",{"allow_unofficial":True},handler)


def test_public_html_query_and_extraction():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /\nCrawl-delay: 3\n")
        assert req.url.params["q"] == "paper & planes"
        assert "Authorization" not in req.headers
        assert "api_key" not in req.url.params
        return httpx.Response(200,content=(FIXTURES/"results.html").read_bytes())
    result = network("website-keywords","query",{"url_template":"https://example.org/search?q={query}","selector":".result"},handler)
    assert [k.phrase for k in result.keywords] == ["Paper planes","Paper folding"]
    assert result.metadata["access"] == "public-html"
    assert len(calls) == 2
    assert result.keywords[0].score is None


@pytest.mark.parametrize("url", [
    "http://example.org/", "https://localhost/", "https://host.local/", "https://user:pass@example.org/",
    "https://example.org:8443/", "https://example.org/#fragment", "file:///etc/passwd",
])
def test_public_url_validation(url):
    with pytest.raises(ConfigurationError):
        public_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1","10.0.0.1","169.254.169.254","::1","fe80::1"])
def test_private_network_addresses_are_rejected(address, monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a, **kw: [(2,1,6,"",(address,443))])
    with pytest.raises(ConfigurationError):
        public_url("https://attacker.example/")


@pytest.mark.parametrize("template", ["https://{query}.example.org/search", "https://example.org/{query}",
                                      "https://example.org/?q={query}&x={query}","https://example.org/?q={other}"])
def test_template_substitution_cannot_change_hostname_or_path(template):
    with pytest.raises(ConfigurationError):
        network("website-keywords","query",{"url_template":template,"selector":".result"},lambda _: pytest.fail("must not request"))


@pytest.mark.parametrize("body", [
    '<html><title>Just a moment...</title></html>',
    '<input type="password"><div class="result">Sign in</div>',
    '<div class="g-recaptcha"></div><div class="result">Verify</div>',
    '<html><title>Access denied</title></html>',
])
def test_challenge_pages_are_not_keywords(body):
    with pytest.raises(OnlineSourceError,match="challenge"):
        parse_html(body,{"selector":".result"},"test","2026-10-02",None)


def test_empty_results_marker_is_explicit():
    body = '<div id="no-results">No matching keywords</div>'
    with pytest.raises(OnlineSourceError,match="No keyword"):
        parse_html(body,{"selector":".result"},"test","2026-10-02",None)
    items, notes = parse_html(body,{"selector":".result","empty_selector":"#no-results"},"test","2026-10-02",None)
    assert not items and notes


def test_invalid_css_and_hidden_dom():
    with pytest.raises(ConfigurationError):
        parse_html('<div>Hi</div>',{"selector":"["},"test","2026-10-02",None)
    with pytest.raises(OnlineSourceError,match="no visible"):
        parse_html('<div class="result" aria-hidden="true">Hidden</div>',{"selector":".result"},"test","2026-10-02",None)


@pytest.mark.parametrize("name", ["website-keywords","ubersuggest","answerthepublic"])
def test_saved_html_import_is_not_claimed_as_live(name):
    options={"selector":".result","observed_at":"2026-10-01","platform":"Google"}
    result = factories()[name]().run(PluginRequest("import-html",inputs=(FIXTURES/"results.html",),options=options),ExecutionContext(None))
    assert result.metadata["live_query_performed"] is False
    assert len(result.keywords) == 2
    assert result.keywords[0].evidence[0].observed_at == "2026-10-01"


@pytest.mark.parametrize("name", ["ubersuggest","answerthepublic"])
def test_explicit_report_column_mapping_and_nulls(name):
    options={"phrase_column":"Keyword","volume_column":"Volume","cpc_column":"CPC",
             "difficulty_column":"Difficulty","currency":"USD","observed_at":"2026-10-01","platform":"Google"}
    result = factories()[name]().run(PluginRequest("import-csv",inputs=(FIXTURES/"export.csv",),options=options),ExecutionContext(None))
    first = {e.metric:e.value for e in result.keywords[0].evidence}
    second = {e.metric:e.value for e in result.keywords[1].evidence}
    assert first["reported_search_volume"] == 0
    assert second["reported_search_volume"] is None
    assert first["reported_cpc"] == 1.25
    assert result.metadata["live_query_performed"] is False


@pytest.mark.parametrize("options", [
    {"phrase_column":"Missing","observed_at":"2026-10-01","platform":"Google"},
    {"phrase_column":"Keyword","observed_at":"yesterday","platform":"Google"},
    {"phrase_column":"Keyword","observed_at":"2026-10-01"},
])
def test_report_import_requires_meaningful_provenance(options):
    from keywordmoves.errors import KeywordMovesError
    with pytest.raises(KeywordMovesError):
        factories()["ubersuggest"]().run(PluginRequest("import-csv",inputs=(FIXTURES/"export.csv",),options=options),ExecutionContext(None))


def test_import_only_tools_do_not_pretend_to_query():
    with pytest.raises(ConfigurationError,match="not live"):
        factories()["ubersuggest"]().run(PluginRequest("query",keywords=("paper",)),ExecutionContext(None))


def test_import_html_requires_observation_date():
    with pytest.raises(ConfigurationError):
        factories()["website-keywords"]().run(PluginRequest("import-html",inputs=(FIXTURES/"results.html",),options={"selector":".result"}),ExecutionContext(None))


@pytest.mark.parametrize("raw,expected", [("1,250",1250),("1,250.50",1250.5),("0",0),("N/A",None)])
def test_report_numeric_thousands_groups(raw, expected):
    plugin = factories()["ubersuggest"]()
    body = f'Keyword,Volume\nphrase,"{raw}"\n'
    items, _ = plugin._csv(body, {"phrase_column":"Keyword","volume_column":"Volume"}, "2026-10-02", "Google")
    assert next(e.value for e in items[0].evidence if e.metric == "reported_search_volume") == expected


@pytest.mark.parametrize("raw", ["1,25","1.250,50","1,2,3","<10"])
def test_report_ambiguous_numbers_are_not_guessed(raw):
    from keywordmoves.errors import KeywordMovesError
    body = f'Keyword,Volume\nphrase,"{raw}"\n'
    with pytest.raises(KeywordMovesError):
        factories()["ubersuggest"]()._csv(body, {"phrase_column":"Keyword","volume_column":"Volume"}, "2026-10-02", "Google")
