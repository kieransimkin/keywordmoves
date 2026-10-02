"""Optional end-to-end test with a real installed, trained English pipeline."""
import importlib.util
import os

import pytest

from keywordmoves.builtin.spacy_keywords import SpacyKeywordPlugin
from keywordmoves.models import ExecutionContext, PluginRequest


def test_installed_english_model_end_to_end():
    available = importlib.util.find_spec("spacy") and importlib.util.find_spec("en_core_web_sm")
    if not available:
        if os.environ.get("KEYWORDMOVES_REQUIRE_SPACY_MODEL") == "1":
            pytest.fail("CI requires spaCy and en_core_web_sm; install both before this test.")
        pytest.skip("Install keywordmoves[spacy] and en_core_web_sm for the trained-model smoke test.")
    result = SpacyKeywordPlugin().run(
        PluginRequest(operation="extract", options={
            "text": "Apple opened an office in London. The software company builds new computer systems.",
        }),
        ExecutionContext(llms=None),
    )
    phrases = {item.phrase.casefold() for item in result.keywords}
    assert "london" in phrases
    features = {feature for item in result.keywords for feature in item.metadata["features"]}
    assert {"proper-nouns", "noun-chunks", "entities"} <= features
    assert result.metadata["model_version"]
