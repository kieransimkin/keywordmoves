from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keywordmoves.cli import main
from keywordmoves.online import factories
from keywordmoves.online.common import (
    HTTP,
    ConfigurationError,
    OnlineSourceError,
    boolean,
    integer,
    iso_date,
    number,
    secret,
)


httpx = pytest.importorskip("httpx")


def test_all_plugins_are_registered_without_optional_imports():
    root = Path(__file__).resolve().parents[1]
    code = '''
import builtins
real_import = builtins.__import__
def checked(name, *args, **kwargs):
    if name.split('.')[0] in {'httpx','bs4','requests','torch','spacy','nltk','openai','keybert','sentence_transformers'}:
        raise ImportError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = checked
from keywordmoves.cli import main
raise SystemExit(main(['plugins','--json']))
'''
    result = subprocess.run([sys.executable,"-c",code],cwd=root,
                            env={**os.environ,"PYTHONPATH":str(root/"src")},capture_output=True,text=True,check=True)
    names = {v["name"] for v in json.loads(result.stdout) if v["kind"] == "keyword"}
    assert set(factories()).issubset(names)
    assert len(factories()) == 21
    assert "instagram" in names
    assert "tiktok" in names
    assert "youtube" in names


@pytest.mark.parametrize("status", [301,302,307,308,401,402,403,404,429,500,503])
def test_no_redirects_no_retries_no_secret_errors(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status,headers={"Location":"https://attacker.example/secret"},text="secret-value")
    with HTTP({},("provider.example",),httpx.MockTransport(handler)) as client:
        with pytest.raises(OnlineSourceError) as exc:
            client.request("GET","https://provider.example/api",params={"api_key":"secret-value"})
        assert "secret-value" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout,httpx.ConnectError,httpx.ReadError])
def test_network_errors_hide_request_details(error):
    def handler(req):
        raise error("URL includes secret-value",request=req)
    with HTTP({},("provider.example",),httpx.MockTransport(handler)) as client:
        with pytest.raises(OnlineSourceError) as exc:
            client.json("GET","https://provider.example/?api_key=secret-value")
        assert "secret-value" not in str(exc.value)
        assert exc.value.__suppress_context__


def test_decoded_size_limit():
    with HTTP({"max_response_bytes":1024},("provider.example",),httpx.MockTransport(lambda _: httpx.Response(200,content=b'x'*1025))) as client:
        with pytest.raises(OnlineSourceError,match="exceeded"):
            client.request("GET","https://provider.example/api")


@pytest.mark.parametrize("url", ["http://provider.example/api","https://attacker.example/api", "https://user:pass@provider.example/api", "https://provider.example:8443/api"])
def test_fixed_hosts_and_https_are_enforced(url):
    with HTTP({},("provider.example",),httpx.MockTransport(lambda _: pytest.fail("must not request"))) as client:
        with pytest.raises(ConfigurationError):
            client.request("GET",url)


@pytest.mark.parametrize("payload", ['NaN','Infinity','{"value":NaN}', '<html>captcha</html>'])
def test_invalid_json_fails_explicitly(payload):
    with HTTP({},("provider.example",),httpx.MockTransport(lambda _: httpx.Response(200,text=payload))) as client:
        with pytest.raises(OnlineSourceError):
            client.json("GET","https://provider.example/")


@pytest.mark.parametrize("value", [True,False,"1.2",-1,1001,"hello",float('inf')])
def test_integer_options_validate(value):
    with pytest.raises(ConfigurationError):
        integer({"limit":value},"limit",50,1,1000)


@pytest.mark.parametrize("value", [True,float('nan'),float('inf'),"unknown",[],{}])
def test_numbers_reject_invalid_metrics(value):
    with pytest.raises(OnlineSourceError):
        number(value)


@pytest.mark.parametrize("value,expected", [(None,None),("",None),(0,0),("0",0),("2.5",2.5)])
def test_numbers_do_not_conflate_null_and_zero(value,expected):
    assert number(value) == expected


def test_credentials_are_resolved_at_run_time_and_do_not_fall_back(monkeypatch):
    monkeypatch.setenv("TEST_KEY","first")
    assert secret({},"api_key","TEST_KEY") == "first"
    monkeypatch.setenv("TEST_KEY","second")
    assert secret({},"api_key","TEST_KEY") == "second"
    assert secret({"api_key":"explicit"},"api_key","TEST_KEY") == "explicit"
    for value in ("",None,True,"key\nvalue"):
        with pytest.raises(ConfigurationError):
            secret({"api_key":value},"api_key","TEST_KEY")


def test_strict_boolean_and_date():
    assert boolean({"x":"false"},"x") is False
    assert boolean({"x":True},"x") is True
    with pytest.raises(ConfigurationError):
        boolean({"x":"yes"},"x")
    assert iso_date({"when":"2026-10-02"},"when") == "2026-10-02"
    with pytest.raises(ConfigurationError):
        iso_date({"when":"20261002"},"when")


def test_cli_import_and_missing_key_are_clean(capsys,monkeypatch):
    fixture = Path(__file__).parent/"fixtures"/"online"/"export.csv"
    assert main(["run","ubersuggest","--operation","import-csv","--input",str(fixture),
                 "--option","phrase_column=Keyword","--option","observed_at=2026-10-01",
                 "--option","platform=Google"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metadata"]["live_query_performed"] is False
    monkeypatch.delenv("SEMRUSH_API_KEY",raising=False)
    assert main(["run","semrush","--operation","related","--keyword","paper planes","--option","database=uk"]) == 2
    assert "SEMRUSH_API_KEY" in capsys.readouterr().err


def test_local_output_truncation_is_reported():
    from keywordmoves.models import ExecutionContext, PluginRequest
    result = factories()["datamuse"](transport=httpx.MockTransport(lambda _: httpx.Response(200,json=[
        {"word":"first","score":2},{"word":"second","score":1}]))) .run(
            PluginRequest("related",keywords=("paper",),options={"limit":1}),ExecutionContext(None))
    assert len(result.keywords) == 1
    assert result.metadata["output_truncated"] is True
    assert result.metadata["rows_received"] == 2
