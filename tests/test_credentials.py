"""Synthetic keyrings and HTTPX replies; no platform credentials or live requests."""
from __future__ import annotations

import asyncio
import io
import json
import sys
from types import SimpleNamespace

import pytest

from keywordmoves import credentials as creds
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import secret

FAKE = "fictional-credential-test-value"


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    for name in (*creds.KNOWN_CREDENTIALS, "KEYWORDMOVES_CREDENTIAL_STORE",
                 "KEYWORDMOVES_CREDENTIAL_SERVICE"):
        monkeypatch.delenv(name, raising=False)


def install_backend(monkeypatch, module="keyring.backends.Windows", name="WinVaultKeyring"):
    class Backend:
        def __init__(self):
            self.values = {}
            self.reads = []

        def get_password(self, service, key):
            self.reads.append((service, key))
            return self.values.get((service, key))

        def set_password(self, service, key, value):
            self.values[service, key] = value

        def delete_password(self, service, key):
            del self.values[service, key]

    Backend.__module__, Backend.__name__ = module, name
    backend = Backend()
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_keyring=lambda: backend))
    return backend


@pytest.mark.parametrize("module,name", sorted(creds._OS_BACKENDS))
def test_supported_os_backends_store_readback_and_delete(monkeypatch, module, name):
    backend = install_backend(monkeypatch, module, name)
    with creds.credential_context(store="os-keyring", service="demo-account"):
        store = creds.OSCredentialStore("demo-account")
        store.set("YOUTUBE_ACCESS_TOKEN", FAKE)
        assert creds.credential_value("YOUTUBE_ACCESS_TOKEN") == FAKE
        status = creds.credential_status(["YOUTUBE_ACCESS_TOKEN"])
        assert status["credentials"]["YOUTUBE_ACCESS_TOKEN"] == {
            "environment_present": False, "stored_present": True}
        assert FAKE not in json.dumps(status)
        assert status["network_requests"] == 0
        store.delete("YOUTUBE_ACCESS_TOKEN")
        store.delete("YOUTUBE_ACCESS_TOKEN")
        assert creds.credential_value("YOUTUBE_ACCESS_TOKEN") is None
    assert backend.values == {}


def test_cli_and_environment_precede_optional_keyring(monkeypatch):
    backend = install_backend(monkeypatch)
    backend.values["demo", "SEARCH_CONSOLE_ACCESS_TOKEN"] = FAKE
    monkeypatch.setenv("SEARCH_CONSOLE_ACCESS_TOKEN", "fictional-env")
    with creds.credential_context(store="os-keyring", service="demo"):
        assert secret({"access_token": "fictional-explicit"}, "access_token",
                      "SEARCH_CONSOLE_ACCESS_TOKEN") == "fictional-explicit"
        assert secret({}, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN") == "fictional-env"
    assert backend.reads == []


@pytest.mark.parametrize("value", ["", "  ", "bad\ncredential", None, 123])
def test_invalid_explicit_value_never_falls_back(monkeypatch, value):
    backend = install_backend(monkeypatch)
    monkeypatch.setenv("SEARCH_CONSOLE_ACCESS_TOKEN", "fictional-env")
    with creds.credential_context(store="os-keyring"):
        with pytest.raises(ConfigurationError):
            secret({"access_token": value}, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN")
    assert backend.reads == []


def test_blank_environment_blocks_account_fallback(monkeypatch):
    backend = install_backend(monkeypatch)
    backend.values["KeywordMoves", "SEARCH_CONSOLE_ACCESS_TOKEN"] = FAKE
    monkeypatch.setenv("SEARCH_CONSOLE_ACCESS_TOKEN", "")
    with creds.credential_context(store="os-keyring"):
        with pytest.raises(ConfigurationError):
            secret({}, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN")
    assert backend.reads == []


def test_environment_default_and_explicit_options_need_no_keyring(monkeypatch):
    def missing(module):
        raise ImportError("fictional dependency diagnostic")
    monkeypatch.setattr(creds.importlib, "import_module", missing)
    assert creds.credential_value("OPENAI_API_KEY") is None
    monkeypatch.setenv("OPENAI_API_KEY", FAKE)
    assert creds.credential_value("OPENAI_API_KEY") == FAKE
    assert secret({"api_key": FAKE}, "api_key", "OPENAI_API_KEY") == FAKE
    monkeypatch.delenv("OPENAI_API_KEY")
    with creds.credential_context(store="os-keyring"):
        with pytest.raises(ConfigurationError, match="credentials") as error:
            creds.credential_value("OPENAI_API_KEY")
    assert "fictional dependency diagnostic" not in str(error.value)


@pytest.mark.parametrize("module,name", [
    ("keyrings.alt.file", "PlaintextKeyring"), ("keyring.backends.fail", "Keyring"),
    ("keyring.backends.null", "Keyring"), ("unknown", "Keyring"),
])
def test_unsupported_backends_fail_without_plaintext_fallback(monkeypatch, module, name):
    install_backend(monkeypatch, module, name)
    with pytest.raises(ConfigurationError, match="No supported OS"):
        creds.OSCredentialStore()


def test_chainer_uses_supported_os_backend_directly(monkeypatch):
    backend = install_backend(monkeypatch, "keyring.backends.SecretService", "Keyring")
    Chain = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer"})
    chain = Chain()
    chain.backends = [SimpleNamespace(), backend]
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_keyring=lambda: chain))
    store = creds.OSCredentialStore()
    assert store.backend_name == "keyring.backends.SecretService.Keyring"
    store.set("OPENAI_API_KEY", FAKE)
    assert store.get("OPENAI_API_KEY") == FAKE


def test_locked_backend_and_failed_readback_do_not_leak(monkeypatch, capsys):
    backend = install_backend(monkeypatch)
    def fail(*args):
        raise RuntimeError(FAKE)
    backend.get_password = fail
    assert main(["credentials", "status", "OPENAI_API_KEY"]) == 2
    output = capsys.readouterr()
    assert FAKE not in output.out + output.err
    assert "Could not read" in output.err
    with pytest.raises(ConfigurationError, match="Could not store") as error:
        creds.OSCredentialStore().set("OPENAI_API_KEY", FAKE)
    assert FAKE not in str(error.value)
    backend.get_password = lambda *args: None
    with pytest.raises(ConfigurationError, match="did not confirm"):
        creds.OSCredentialStore().set("OPENAI_API_KEY", FAKE)


def test_profile_isolation_and_context_restoration(monkeypatch):
    backend = install_backend(monkeypatch)
    backend.values["account-a", "OPENAI_API_KEY"] = "fictional-account-a"
    backend.values["account-b", "OPENAI_API_KEY"] = "fictional-account-b"
    with creds.credential_context(store="os-keyring", service="account-a"):
        assert creds.credential_value("OPENAI_API_KEY") == "fictional-account-a"
        with pytest.raises(RuntimeError):
            with creds.credential_context(service="account-b"):
                assert creds.credential_value("OPENAI_API_KEY") == "fictional-account-b"
                raise RuntimeError("test")
        assert creds.credential_value("OPENAI_API_KEY") == "fictional-account-a"
    assert creds.credential_settings() == creds.CredentialSettings()


def test_async_accounts_do_not_cross_contexts(monkeypatch):
    install_backend(monkeypatch)
    async def account(service):
        with creds.credential_context(store="os-keyring", service=service):
            await asyncio.sleep(0)
            return creds.credential_settings().service
    async def run():
        return await asyncio.gather(account("a"), account("b"))
    assert asyncio.run(run()) == ["a", "b"]
    assert creds.credential_settings().store == "environment"


def test_cli_config_overrides_environment_config(monkeypatch):
    monkeypatch.setenv("KEYWORDMOVES_CREDENTIAL_STORE", "invalid")
    monkeypatch.setenv("KEYWORDMOVES_CREDENTIAL_SERVICE", "bad\nprofile")
    with creds.credential_context(store="environment", service="valid"):
        assert creds.credential_settings() == creds.CredentialSettings("environment", "valid")


def test_environment_config_selects_profile(monkeypatch):
    backend = install_backend(monkeypatch)
    backend.values["account-b", "OPENAI_API_KEY"] = FAKE
    monkeypatch.setenv("KEYWORDMOVES_CREDENTIAL_STORE", "os-keyring")
    monkeypatch.setenv("KEYWORDMOVES_CREDENTIAL_SERVICE", "account-b")
    assert creds.credential_value("OPENAI_API_KEY") == FAKE


@pytest.mark.parametrize("name", ["bad", "", "OPENAI_API_KEY\n", "A"*129])
def test_invalid_names_rejected_without_echo(monkeypatch, name):
    install_backend(monkeypatch)
    with pytest.raises(ConfigurationError):
        creds.OSCredentialStore().get(name)


@pytest.mark.parametrize("value", ["", " ", "bad\nvalue", "bad\x00value", "bad\x7f", "a"*65537],
                         ids=["empty", "spaces", "newline", "nul", "del", "too-long"])
def test_invalid_values_rejected_without_echo(monkeypatch, value):
    install_backend(monkeypatch)
    with pytest.raises(ConfigurationError) as error:
        creds.OSCredentialStore().set("OPENAI_API_KEY", value)
    assert "bad" not in str(error.value)


def test_cli_hidden_prompt_stdin_status_and_delete(monkeypatch, capsys):
    backend = install_backend(monkeypatch)
    monkeypatch.setattr(creds.getpass, "getpass", lambda prompt: FAKE)
    prefix = ["--credential-service", "example-profile", "credentials"]
    assert main([*prefix, "set", "OPENAI_API_KEY"]) == 0
    assert backend.values["example-profile", "OPENAI_API_KEY"] == FAKE
    assert main([*prefix, "status", "OPENAI_API_KEY"]) == 0
    assert main([*prefix, "delete", "OPENAI_API_KEY"]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE + "\r\n"))
    assert main([*prefix, "set", "OPENAI_API_KEY", "--stdin"]) == 0
    output = capsys.readouterr()
    assert FAKE not in output.out + output.err
    assert "stored_present" in output.out


def test_cli_environment_status_never_imports_keyring(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE)
    monkeypatch.setitem(sys.modules, "keyring", None)
    assert main(["--credential-store", "environment", "credentials", "status", "OPENAI_API_KEY"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["backend"] is None
    assert status["credentials"]["OPENAI_API_KEY"] == {
        "environment_present": True, "stored_present": None}


def test_getpass_fails_closed_without_terminal(monkeypatch):
    def warning(prompt):
        raise creds.getpass.GetPassWarning("fictional diagnostic")
    monkeypatch.setattr(creds.getpass, "getpass", warning)
    with pytest.raises(ConfigurationError, match="Hidden credential input"):
        creds.read_credential()


def test_monitor_dispatch_inherits_store_and_restores_context(monkeypatch):
    seen = []
    monkeypatch.setattr("keywordmoves.monitoring.cli.dispatch",
                        lambda args: seen.append(creds.credential_settings()) or 0)
    assert main(["--credential-store", "os-keyring", "--credential-service", "example",
                 "monitor", "--store", "unused.sqlite", "coverage"]) == 0
    assert seen == [creds.CredentialSettings("os-keyring", "example")]
    assert creds.credential_settings() == creds.CredentialSettings()


def test_bing_native_reader_uses_keyring_without_echo(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from keywordmoves.online.bing_search import BingSearchPlugin
    backend = install_backend(monkeypatch)
    backend.values["demo", "BING_WEBMASTER_API_KEY"] = FAKE
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.params["apikey"] == FAKE
        return httpx.Response(200, json={"d": [{"Url": "https://example.com/", "IsVerified": True}]})
    with creds.credential_context(store="os-keyring", service="demo"):
        result = BingSearchPlugin(transport=httpx.MockTransport(handler)).run(
            PluginRequest("bwt-sites", options={"max_requests": 1}), ExecutionContext(None))
    assert len(calls) == 1
    assert FAKE not in json.dumps(result.to_dict())


def test_google_native_reader_uses_keyring_without_echo(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from keywordmoves.online.google_search import GoogleSearchPlugin
    backend = install_backend(monkeypatch)
    backend.values["demo", "SEARCH_CONSOLE_ACCESS_TOKEN"] = FAKE
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer " + FAKE
        return httpx.Response(200, json={"siteEntry": [{"siteUrl": "https://example.com/",
                                                       "permissionLevel": "siteOwner"}]})
    with creds.credential_context(store="os-keyring", service="demo"):
        result = GoogleSearchPlugin(transport=httpx.MockTransport(handler)).run(
            PluginRequest("gsc-sites", options={"max_requests": 1}), ExecutionContext(None))
    assert len(calls) == 1
    assert FAKE not in json.dumps(result.to_dict())


def test_all_reader_credential_names_in_inventory():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]/"src/keywordmoves"
    names = set()
    for path in (root/"online").glob("*.py"):
        names.update(re.findall(r'secret\([^\n]*?"([A-Z][A-Z0-9_]+)"\)', path.read_text()))
    assert names <= set(creds.KNOWN_CREDENTIALS)


def test_cli_run_applies_os_profile_to_native_reader(monkeypatch, capsys):
    httpx = pytest.importorskip("httpx")
    from keywordmoves.online.bing_search import BingSearchPlugin
    backend = install_backend(monkeypatch)
    backend.values["cli-profile", "BING_WEBMASTER_API_KEY"] = FAKE
    def handler(req):
        assert req.url.params["apikey"] == FAKE
        return httpx.Response(200, json={"d": []})
    plugin = BingSearchPlugin(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("keywordmoves.cli.PluginRegistry", lambda: SimpleNamespace(get=lambda _: plugin))
    assert main(["--credential-store", "os-keyring", "--credential-service", "cli-profile",
                 "run", "bing-search", "--operation", "bwt-sites", "--option", "max_requests=1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["metadata"]["requests_made"] == 1
    assert FAKE not in json.dumps(result)


def test_optional_pagespeed_key_uses_selected_os_store(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from keywordmoves.online.google_search import GoogleSearchPlugin
    backend = install_backend(monkeypatch)
    backend.values["demo", "PAGESPEED_API_KEY"] = FAKE
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.params["key"] == FAKE
        return httpx.Response(200, json={"id": "https://example.com/", "lighthouseResult": {
            "categories": {"performance": {"score": 0.9}}, "audits": {}}})
    with creds.credential_context(store="os-keyring", service="demo"):
        result = GoogleSearchPlugin(transport=httpx.MockTransport(handler)).run(
            PluginRequest("pagespeed", options={"url": "https://example.com/", "max_requests": 1}),
            ExecutionContext(None))
    assert len(calls) == 1
    assert FAKE not in json.dumps(result.to_dict())


@pytest.mark.parametrize("value", ["00123", "12345", "1.23", "true", "false"])
def test_cli_preserves_opaque_credential_text(value):
    from keywordmoves.cli import _option
    for name in ("api_key", "access_token", "password", "login", "developer_token",
                 "serpapi_key", "apify_token", "llm_api_key"):
        key, parsed = _option(name + "=" + value)
        assert parsed == value and isinstance(parsed, str)
        assert secret({key: parsed}, key, "OPENAI_API_KEY") == value
    assert _option("max_requests=1") == ("max_requests", 1)
    assert _option("allow_paid=true") == ("allow_paid", True)
