from keywordmoves.online import factories as online_factories
from keywordmoves.registry import LLMRegistry, PluginRegistry


def test_builtin_keyword_plugins_are_discoverable():
    expected = {"google-trends", "native-export", "keybert", "nltk", "observed-evidence", "spacy", "text-library"}
    assert set(PluginRegistry().names()) == expected | set(online_factories())


def test_builtin_llm_plugin_is_discoverable():
    assert LLMRegistry().names() == ("huggingface-transformers", "openai")


def test_registry_caches_instances():
    registry = PluginRegistry()
    assert registry.get("google-trends") is registry.get("google-trends")


def test_registry_reports_available_names():
    registry = PluginRegistry()
    try:
        registry.get("missing")
    except Exception as exc:
        assert "google-trends" in str(exc)
        assert "observed-evidence" in str(exc)
        assert "text-library" in str(exc)
    else:
        raise AssertionError("missing plugin should fail")
