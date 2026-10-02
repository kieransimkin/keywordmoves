"""Optional installed-model smoke test; CI opts into downloading the pinned model."""
import os

import pytest

from keywordmoves.builtin.keybert_keywords import KeyBERTKeywordPlugin, _Settings
from keywordmoves.errors import ConfigurationError
from keywordmoves.models import ExecutionContext, PluginRequest


def test_pretrained_keybert_model():
    require = os.environ.get("KEYWORDMOVES_REQUIRE_KEYBERT_MODEL") == "1"
    plugin = KeyBERTKeywordPlugin()
    options = {"local_files_only": not require, "limit": 10, "device": "cpu"}
    try:
        plugin._load(_Settings.parse(options))
    except ConfigurationError as exc:
        if require:
            pytest.fail(str(exc))
        pytest.skip("KeyBERT dependencies/pinned model not installed; install the keybert extra and cache the model")
    options["text"] = (
        "Machine learning discovers patterns in data. Neural networks power machine learning. "
        "Keyword extraction uses sentence embeddings to find important phrases."
    )
    result = plugin.run(PluginRequest(operation="extract", options=options), ExecutionContext(llms=None))
    assert 0 < len(result.keywords) <= 10
    assert result.metadata["versions"]["keybert"] is not None
    assert result.metadata["sources"][0]["embedding_chunks"] >= 1
    assert all(-1 <= item.score <= 1 for item in result.keywords)
    for item in result.keywords:
        for occurrence in item.metadata["occurrences"]:
            assert options["text"][occurrence["start_char"]:occurrence["end_char"]] == occurrence["text"]
