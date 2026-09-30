from pathlib import Path

import pytest

from keywordmoves.builtin.text_library import TextLibraryPlugin
from keywordmoves.errors import ConfigurationError
from keywordmoves.models import ExecutionContext, LLMResult, PluginRequest

FIXTURE = Path(__file__).parent / "fixtures" / "lyrics.txt"


class FakeLLM:
    def generate(self, request):
        text = (
            "paper planes, midnight sky"
            if request.task == "keyword-extraction"
            else "flight path, city at night"
        )
        return LLMResult(
            plugin="fake-local",
            model="tiny-test-model",
            text=text,
            metadata={"revision": "test-revision"},
        )


class FakeLLMs:
    def get(self, name):
        assert name == "fake-local"
        return FakeLLM()


def test_local_extraction_is_deterministic():
    result = TextLibraryPlugin().run(
        PluginRequest(operation="extract-local", inputs=(FIXTURE,), options={"limit": 5}),
        ExecutionContext(llms=FakeLLMs()),
    )
    assert result.keywords[0].phrase == "paper planes"
    assert result.keywords[0].evidence[0].metric == "occurrences"
    assert "No LLM was used" in result.notes[1]


def test_extract_requires_explicit_llm_selection():
    with pytest.raises(ConfigurationError, match="explicit LLM plugin selection"):
        TextLibraryPlugin().run(
            PluginRequest(operation="extract", inputs=(FIXTURE,)),
            ExecutionContext(llms=FakeLLMs()),
        )


def test_selected_llm_adds_related_concepts():
    result = TextLibraryPlugin().run(
        PluginRequest(
            operation="extract",
            inputs=(FIXTURE,),
            options={"limit": 20, "llm": "fake-local"},
        ),
        ExecutionContext(llms=FakeLLMs()),
    )
    by_phrase = {item.phrase: item for item in result.keywords}
    assert by_phrase["flight path"].relationship == "llm-related"
    assert by_phrase["city at night"].evidence[0].metric == "semantic_suggestion"
    assert result.metadata["llm_plugin"] == "fake-local"
    assert result.metadata["llm_metadata"]["revision"] == "test-revision"


def test_llm_prefixed_options_are_forwarded_without_prefix():
    captured = {}

    class CapturingLLM:
        def generate(self, request):
            captured.update(request.options)
            return LLMResult(plugin="capture", model="chosen", text="sky, air")

    class Registry:
        def get(self, name):
            return CapturingLLM()

    TextLibraryPlugin().run(
        PluginRequest(
            operation="extract",
            inputs=(FIXTURE,),
            options={"llm": "capture", "llm_model": "chosen", "llm_device": "cpu"},
        ),
        ExecutionContext(llms=Registry()),
    )
    assert captured == {"model": "chosen", "device": "cpu"}


def test_limit_is_validated():
    with pytest.raises(ConfigurationError):
        TextLibraryPlugin().run(
            PluginRequest(operation="extract-local", inputs=(FIXTURE,), options={"limit": 0}),
            ExecutionContext(llms=FakeLLMs()),
        )
