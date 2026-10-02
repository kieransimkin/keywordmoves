"""Optional pretrained-English-model smoke test; mandatory in the nltk-data CI job."""
import os
from pathlib import Path

import pytest

from keywordmoves.builtin import nltk_keywords as nk
from keywordmoves.errors import ConfigurationError
from keywordmoves.models import ExecutionContext, PluginRequest


def test_pretrained_english_pipeline():
    plugin = nk.NLTKKeywordPlugin()
    fixture = Path(__file__).parent / "fixtures" / "nltk_reference.txt"
    try:
        import nltk
        nk._require_data(nltk, nk._resources(nk._Settings.parse({})))
    except (ImportError, ConfigurationError) as exc:
        if os.environ.get("KEYWORDMOVES_REQUIRE_NLTK_DATA") == "1":
            pytest.fail(str(exc))
        pytest.skip(f"Optional NLTK library/data not installed: {exc}")
    # Corrupt installed models or inference failures must fail, not masquerade as a skip.
    result = plugin.run(PluginRequest(operation="extract", inputs=(fixture,)),
                        ExecutionContext(llms=None))
    assert result.plugin == "nltk" and result.keywords
    features = {feature for k in result.keywords for feature in k.metadata["features"]}
    assert {"proper-nouns", "noun-chunks", "entities", "nouns"} <= features
    assert any(k.metadata["entity_labels"] for k in result.keywords)
    text = fixture.read_text(encoding="utf-8")
    for k in result.keywords:
        for loc in k.metadata["occurrences"]:
            assert text[loc["start_char"]:loc["end_char"]] in k.metadata["surface_forms"]
    assert all(k.relationship == "nltk-extracted" for k in result.keywords)
    # Exercise the non-NER/non-WordNet path using the same genuine pretrained tagger.
    result = plugin.run(PluginRequest(operation="extract", options={
        "text": "The bright paper planes fly.", "features": "noun-chunks", "lemmatize": False,
    }), ExecutionContext(llms=None))
    assert result.keywords
    assert "wordnet" not in result.metadata["resources"]
    assert "maxent_ne_chunker_tab" not in result.metadata["resources"]
