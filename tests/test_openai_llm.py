import builtins
import json
import sys
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from keywordmoves.builtin.openai_llm import OpenAILLM
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, KeywordMovesError
from keywordmoves.models import LLMRequest
from keywordmoves.registry import LLMRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "lyrics.txt"


@pytest.fixture
def sdk(monkeypatch):
    """No SDK installation, network connection or genuine key is required."""
    module = ModuleType("openai")
    for name, parent in (
        ("APIError", Exception),
        ("APIStatusError", "APIError"),
        ("AuthenticationError", "APIStatusError"),
        ("PermissionDeniedError", "APIStatusError"),
        ("NotFoundError", "APIStatusError"),
        ("BadRequestError", "APIStatusError"),
        ("UnprocessableEntityError", "APIStatusError"),
        ("RateLimitError", "APIStatusError"),
        ("APIConnectionError", "APIError"),
        ("APITimeoutError", "APIConnectionError"),
    ):
        base = getattr(module, parent) if isinstance(parent, str) else parent
        setattr(module, name, type(name, (base,), {}))
    response = SimpleNamespace(
        id="resp-test",
        model="gpt-4.1-mini-2025-04-14",
        status="completed",
        output=[],
        output_text="  paper planes, midnight sky  ",
        usage=SimpleNamespace(input_tokens=20, output_tokens=8, total_tokens=28),
        incomplete_details=None,
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
    context = MagicMock()
    context.__enter__.return_value = client
    context.__exit__.return_value = False
    module.OpenAI = Mock(return_value=context)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-test-not-a-real-key")
    return SimpleNamespace(module=module, client=client, context=context, response=response)


def generate(**options):
    return OpenAILLM().generate(
        LLMRequest(task="keyword-extraction", prompt="Return search phrases.", options=options)
    )


def test_defaults_map_to_responses_and_record_safe_metadata(sdk):
    result = generate()
    sdk.module.OpenAI.assert_called_once_with(
        api_key="sk-env-test-not-a-real-key",
        base_url="https://api.openai.com/v1",
        timeout=60.0,
        max_retries=2,
    )
    sdk.client.responses.create.assert_called_once_with(
        model="gpt-4.1-mini",
        input="Return search phrases.",
        max_output_tokens=128,
        store=False,
    )
    assert result.plugin == "openai"
    assert result.model == "gpt-4.1-mini-2025-04-14"
    assert result.text == "paper planes, midnight sky"
    assert result.metadata == {
        "task": "keyword-extraction",
        "api": "responses",
        "requested_model": "gpt-4.1-mini",
        "response_id": "resp-test",
        "store": False,
        "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
    }
    sdk.context.__exit__.assert_called_once()


def test_explicit_key_overrides_environment(sdk):
    generate(api_key="  sk-explicit-test  ")
    assert sdk.module.OpenAI.call_args.kwargs["api_key"] == "sk-explicit-test"


def test_key_and_model_are_resolved_per_call(sdk, monkeypatch):
    llm = OpenAILLM()
    request = LLMRequest(task="test", prompt="A prompt.")
    llm.generate(request)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-rotated-test")
    llm.generate(request)
    assert sdk.module.OpenAI.call_count == 2
    assert sdk.module.OpenAI.call_args.kwargs["api_key"] == "sk-rotated-test"


def test_client_and_generation_controls_are_separated(sdk):
    generate(
        model="a-future-text-model",
        api_key="sk-explicit-test",
        organization="org-test",
        project="proj-test",
        timeout="12.5",
        max_retries="0",
        max_output_tokens="2048",
        temperature="0.25",
        reasoning_effort="low",
        unused_provider_option="must-not-be-forwarded",
    )
    assert sdk.module.OpenAI.call_args.kwargs == {
        "api_key": "sk-explicit-test",
        "base_url": "https://api.openai.com/v1",
        "timeout": 12.5,
        "max_retries": 0,
        "organization": "org-test",
        "project": "proj-test",
    }
    assert sdk.client.responses.create.call_args.kwargs == {
        "model": "a-future-text-model",
        "input": "Return search phrases.",
        "max_output_tokens": 2048,
        "store": False,
        "temperature": 0.25,
        "reasoning": {"effort": "low"},
    }


def test_inherited_base_url_cannot_redirect_api_key(sdk, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    generate()
    assert sdk.module.OpenAI.call_args.kwargs["base_url"] == "https://api.openai.com/v1"


def test_request_token_budget_is_used_without_an_override(sdk):
    OpenAILLM().generate(LLMRequest(task="test", prompt="Prompt", max_new_tokens=512))
    assert sdk.client.responses.create.call_args.kwargs["max_output_tokens"] == 512


def test_model_specific_controls_are_not_assumed(sdk):
    generate(model="a-reasoning-model")
    params = sdk.client.responses.create.call_args.kwargs
    assert "temperature" not in params
    assert "reasoning" not in params


@pytest.mark.parametrize(
    "options",
    [
        {"api_key": None},
        {"api_key": ""},
        {"api_key": " \t "},
        {"api_key": 123},
        {"model": ""},
        {"model": False},
        {"max_output_tokens": 0},
        {"max_output_tokens": -1},
        {"max_output_tokens": True},
        {"max_output_tokens": 16.5},
        {"max_output_tokens": "16.5"},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": True},
        {"timeout": "not-a-number"},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"max_retries": -1},
        {"max_retries": 2.5},
        {"max_retries": "bad"},
        {"max_retries": True},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"temperature": float("nan")},
        {"temperature": {}},
        {"reasoning_effort": ""},
        {"organization": None},
        {"project": ""},
    ],
)
def test_invalid_options_fail_before_creating_a_client(sdk, options):
    with pytest.raises(ConfigurationError):
        generate(**options)
    sdk.module.OpenAI.assert_not_called()


def test_blank_prompt_is_rejected(sdk):
    with pytest.raises(ConfigurationError, match="prompt"):
        OpenAILLM().generate(LLMRequest(task="test", prompt="  "))
    sdk.module.OpenAI.assert_not_called()


def test_missing_key_gives_an_actionable_error(sdk, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        generate()
    sdk.module.OpenAI.assert_not_called()


def test_missing_sdk_gives_install_instructions(sdk, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ConfigurationError, match=r"keywordmoves\[openai\]"):
        generate()


@pytest.mark.parametrize(
    ("name", "expected_type", "message"),
    [
        ("AuthenticationError", ConfigurationError, "authentication failed"),
        ("PermissionDeniedError", ConfigurationError, "denied access"),
        ("NotFoundError", ConfigurationError, "model was not found"),
        ("BadRequestError", ConfigurationError, "rejected the request"),
        ("UnprocessableEntityError", ConfigurationError, "rejected the request"),
        ("RateLimitError", KeywordMovesError, "rate limit or quota"),
        ("APITimeoutError", KeywordMovesError, "timed out"),
        ("APIConnectionError", KeywordMovesError, "Could not connect"),
        ("APIStatusError", KeywordMovesError, "API request failed"),
        ("APIError", KeywordMovesError, "API request failed"),
    ],
)
def test_sdk_errors_are_clean_and_do_not_echo_provider_details(sdk, name, expected_type, message):
    secret = "sk-error-test-credential"
    sdk.client.responses.create.side_effect = getattr(sdk.module, name)(
        f"The provider echoed {secret} and private source text"
    )
    with pytest.raises(expected_type, match=message) as caught:
        generate(api_key=secret)
    rendered = "".join(traceback.format_exception(caught.value))
    assert secret not in rendered
    assert "private source text" not in rendered
    sdk.context.__exit__.assert_called_once()


def test_refusal_is_not_parsed_as_a_keyword(sdk):
    sdk.response.output = [
        SimpleNamespace(type="reasoning"),
        SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="private refusal")]),
    ]
    with pytest.raises(KeywordMovesError, match="refused") as caught:
        generate()
    assert "private refusal" not in str(caught.value)


def test_output_limit_discards_partial_text(sdk):
    sdk.response.status = "incomplete"
    sdk.response.incomplete_details = SimpleNamespace(reason="max_output_tokens")
    with pytest.raises(KeywordMovesError, match="llm_max_output_tokens"):
        generate()


def test_other_incomplete_response_discards_partial_text(sdk):
    sdk.response.status = "incomplete"
    sdk.response.incomplete_details = SimpleNamespace(reason="content_filter")
    with pytest.raises(KeywordMovesError, match="incomplete"):
        generate()


@pytest.mark.parametrize("status", ["failed", "cancelled", "queued", "in_progress", None])
def test_non_completed_responses_are_rejected(sdk, status):
    sdk.response.status = status
    with pytest.raises(KeywordMovesError, match="did not complete"):
        generate()


@pytest.mark.parametrize("text", ["", " \n ", None])
def test_empty_text_is_rejected(sdk, text):
    sdk.response.output_text = text
    with pytest.raises(KeywordMovesError, match="no text"):
        generate()


def test_missing_usage_and_model_are_handled(sdk):
    sdk.response.usage = None
    sdk.response.model = None
    result = generate()
    assert "usage" not in result.metadata
    assert result.model == "gpt-4.1-mini"


def test_usage_metadata_is_allowlisted(sdk):
    sdk.response.usage.secret = "sk-must-not-be-copied"
    sdk.response.usage.input_tokens = None
    sdk.response.usage.output_tokens = True
    result = generate()
    assert result.metadata["usage"] == {"total_tokens": 28}
    assert "sk-must-not-be-copied" not in json.dumps(result.metadata)


def test_discovery_and_local_extraction_do_not_import_sdk(monkeypatch, capsys):
    original_import = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name == "openai" or name.startswith("openai."):
            raise AssertionError("Core/local operations must not import the OpenAI SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "openai" in LLMRegistry().names()
    assert main(["plugins", "--kind", "llm", "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in listing} == {"huggingface-transformers", "openai"}
    assert main([
        "run", "text-library", "--operation", "extract-local", "--input", str(FIXTURE),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["operation"] == "extract-local"


@pytest.mark.parametrize("key_source", ["environment", "flag", "option", "flag-over-option"])
def test_cli_runs_both_text_library_calls(sdk, capsys, key_source):
    args = [
        "run", "text-library", "--operation", "extract", "--input", str(FIXTURE),
        "--llm", "openai", "--model", "gpt-4.1-mini", "--option", "limit=20",
        "--option", "llm_max_output_tokens=512",
    ]
    key = "sk-env-test-not-a-real-key"
    if key_source in {"option", "flag-over-option"}:
        args += ["--option", "llm_api_key=sk-option-test"]
        key = "sk-option-test"
    if key_source in {"flag", "flag-over-option"}:
        args += ["--openai-api-key", "sk-flag-test"]
        key = "sk-flag-test"
    related = SimpleNamespace(**vars(sdk.response))
    related.output_text = "flight path, city at night"
    sdk.client.responses.create.side_effect = [sdk.response, related]
    assert main(args) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["metadata"]["llm_plugin"] == "openai"
    assert any(k["phrase"] == "flight path" for k in result["keywords"])
    assert any(k["relationship"] == "llm-related" for k in result["keywords"])
    assert sdk.client.responses.create.call_count == 2
    for call in sdk.module.OpenAI.call_args_list:
        assert call.kwargs["api_key"] == key
    for call in sdk.client.responses.create.call_args_list:
        assert call.kwargs["max_output_tokens"] == 512
        assert "api_key" not in call.kwargs
    assert "Extract up to" in sdk.client.responses.create.call_args_list[0].kwargs["input"]
    assert "Suggest up to" in sdk.client.responses.create.call_args_list[1].kwargs["input"]
    assert key not in captured.out + captured.err


@pytest.mark.parametrize("llm_args", [[], ["--llm", "huggingface-transformers"]])
def test_key_flag_requires_openai_selection(sdk, capsys, llm_args):
    assert main([
        "run", "text-library", "--operation", "extract", "--input", str(FIXTURE),
        "--openai-api-key", "sk-cli-test", *llm_args,
    ]) == 2
    captured = capsys.readouterr()
    assert "requires --llm openai" in captured.err
    assert "sk-cli-test" not in captured.err
    sdk.module.OpenAI.assert_not_called()


def test_cli_auth_error_returns_two_without_key_or_traceback(sdk, capsys):
    sdk.client.responses.create.side_effect = sdk.module.AuthenticationError("sk-echoed-test")
    assert main([
        "run", "text-library", "--operation", "extract", "--input", str(FIXTURE),
        "--llm", "openai", "--openai-api-key", "sk-echoed-test",
    ]) == 2
    captured = capsys.readouterr()
    assert "authentication failed" in captured.err
    assert "sk-echoed-test" not in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out
