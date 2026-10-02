"""Real NLTK tokenizers, POS tagger and RegexpParser with controlled annotations.

The controlled tag/NER/lemma fixtures do not claim pretrained model accuracy.
No downloads or external corpora are needed for these API and integration tests.
"""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from keywordmoves.builtin import nltk_keywords as nk
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest

nltk = pytest.importorskip("nltk")

TAGS = {
    "Kieran": "NNP", "Simkin": "NNP", "Brighton": "NNP", "Arcadians": "NNPS", "May": "NNP",
    "Mary": "NNP", "Jane": "NNP", "Mary-Jane": "NNP", "Dr.": "NNP", "Bristol": "NNP",
    "University": "NNP", "Oxford": "NNP", "John": "NNP", "York": "NNP", "Zoë": "NNP",
    "writes": "VBZ", "sings": "VBZ", "fly": "VBP", "visit": "VB", "is": "VBZ", "are": "VBP",
    "paper": "NN", "plane": "NN", "planes": "NNS", "music": "NN", "city": "NN",
    "ships": "NNS", "studios": "NNS", "bright": "JJ", "independent": "JJ", "new": "JJ",
    "blue": "JJ", "the": "DT", "The": "DT", "a": "DT", "her": "PRP$", "my": "PRP$",
    "and": "CC", "of": "IN", "in": "IN", "about": "IN", "they": "PRP", "not": "RB",
    "n't": "RB", "'s": "POS", "2026": "CD", ".": ".", ",": ",", "!": ".", "-": ":",
    "``": "``", "''": "''", "(": "-LRB-", ")": "-RRB-",
}


class Entities:
    def parse(self, tagged):
        children = []
        i = 0
        while i < len(tagged):
            words = [word for word, _ in tagged[i:i + 3]]
            if words[:2] == ["Kieran", "Simkin"]:
                children.append(nltk.Tree("PERSON", tagged[i:i + 2]))
                i += 2
            elif words == ["University", "of", "Oxford"]:
                children.append(nltk.Tree("ORGANIZATION", tagged[i:i + 3]))
                i += 3
            elif words[0] in {"Brighton", "Bristol", "Oxford", "York"}:
                children.append(nltk.Tree("GPE", tagged[i:i + 1]))
                i += 1
            else:
                children.append(tagged[i])
                i += 1
        return nltk.Tree("S", children)


class Lemmas:
    def __init__(self):
        self.calls = []

    def lemmatize(self, word, pos):
        self.calls.append((word, pos))
        return {"planes": "plane", "writes": "write", "sings": "sing",
                "studios": "studio", "ships": "ship"}.get(word, word)


@pytest.fixture
def runtime():
    return nk._Runtime(
        version=nltk.__version__, sentences=nltk.tokenize.PunktSentenceTokenizer(),
        words=nltk.tokenize.TreebankWordTokenizer(),
        tagger=nltk.tag.UnigramTagger(model=TAGS, backoff=nltk.tag.DefaultTagger("NN")),
        parser=nltk.RegexpParser(nk.NP_GRAMMAR), ner=Entities(), lemmatizer=Lemmas(),
        stopwords=frozenset({"the", "a", "of", "in", "and", "her", "my", "they", "are", "is", "may"}),
    )


def run(runtime, text, **options):
    plugin = nk.NLTKKeywordPlugin()
    cfg = nk._Settings.parse(options)
    selected = replace(runtime, ner=runtime.ner if "entities" in cfg.features else None,
                       lemmatizer=runtime.lemmatizer if cfg.lemmatize else None)
    plugin._load = lambda cfg: selected
    return plugin.run(PluginRequest(operation="extract", options={"text": text, **options}),
                      ExecutionContext(llms=None))


def index(result):
    return {k.phrase: k for k in result.keywords}


def test_names_noun_chunks_lemmas_and_noninflated_counts(runtime):
    text = "Kieran Simkin writes the bright paper planes in Brighton. Kieran Simkin sings."
    result = run(runtime, text)
    by = index(result)
    assert {"Kieran Simkin", "Kieran", "Simkin", "Brighton", "bright paper plane", "paper plane"} <= set(by)
    name = by["Kieran Simkin"]
    assert name.evidence[0].value == 2
    assert {"proper-nouns", "entities", "noun-chunks", "keyphrases"} == set(name.metadata["features"])
    assert name.metadata["entity_labels"] == ["PERSON"]
    assert by["bright paper plane"].metadata["surface_forms"] == ["bright paper planes"]
    assert "not search-volume" in name.evidence[0].notes
    assert result.metadata["sources"][0]["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert result.metadata["noun_chunk_grammar"] == nk.NP_GRAMMAR
    for candidate in result.keywords:
        assert 0 < candidate.score <= 1
        for occurrence in candidate.metadata["occurrences"]:
            surface = text[occurrence["start_char"]:occurrence["end_char"]]
            assert nk._normalise(surface) in candidate.metadata["surface_forms"]


def test_plural_merging_but_names_untouched(runtime):
    by = index(run(runtime, "Arcadians sings about planes. A plane.", features="proper-nouns,nouns"))
    assert "Arcadians" in by and "Arcadian" not in by
    assert by["plane"].evidence[0].value == 2
    assert not any(word == "arcadians" for word, _ in runtime.lemmatizer.calls)
    by = index(run(runtime, "planes. plane.", lemmatize=False, features="nouns"))
    assert {"plane", "planes"} == set(by)


def test_optional_adjectives_and_verbs_pass_wordnet_pos(runtime):
    text = "Kieran writes independent music."
    normal = index(run(runtime, text))
    assert "write" not in normal and "independent" not in normal
    by = index(run(runtime, text, features="adjectives,verbs"))
    assert {"write", "independent"} == set(by)
    assert ("writes", "v") in runtime.lemmatizer.calls
    assert ("independent", "a") in runtime.lemmatizer.calls


def test_grammar_strips_articles_and_possessive_pronouns(runtime):
    for text in ("the bright paper planes", "her bright paper planes"):
        assert set(index(run(runtime, text, features="noun-chunks"))) == {"bright paper plane"}


def test_entities_keep_connectors_and_native_labels(runtime):
    by = index(run(runtime, "University of Oxford in Brighton. Kieran Simkin.", features="entities",
                   entity_labels="ORGANIZATION"))
    assert set(by) == {"University of Oxford"}
    assert by["University of Oxford"].metadata["entity_labels"] == ["ORGANIZATION"]
    assert "Brighton" in index(run(runtime, "Brighton", features="entities", entity_labels="*"))


@pytest.mark.parametrize("separator", ["\n", "\r\n", ". ", ", ", " and "])
def test_no_phrases_across_boundaries(runtime, separator):
    by = index(run(runtime, "paper" + separator + "planes", features="keyphrases,noun-chunks"))
    assert "paper plane" not in by


def test_line_break_processing_preserves_later_chunk(runtime):
    by = index(run(runtime, "the\nbright paper planes", features="noun-chunks"))
    assert "bright paper plane" in by


def test_proper_nouns_quoted_hyphenated_and_stopword_names(runtime):
    by = index(run(runtime, '"Kieran Simkin". Mary - Jane. Mary-Jane. May.', features="proper-nouns"))
    assert {"Kieran Simkin", "Mary - Jane", "Mary-Jane", "May"} <= set(by)
    assert "\"Kieran" not in by


def test_separate_entities_are_not_joined_as_proper_name(runtime):
    by = index(run(runtime, "Brighton Bristol", features="entities,proper-nouns"))
    assert set(by) == {"Brighton", "Bristol"}


def test_stopwords_email_urls_and_numbers(runtime):
    text = "music. 2026. https://example.com/SecretName. user@example.org. planes."
    by = index(run(runtime, text, features="nouns", stopwords="music"))
    assert set(by) == {"plane"}
    assert not run(runtime, "music", features="nouns", stopwords="music").keywords
    assert "No candidates" in run(runtime, "the", features="nouns").notes[-1]


def test_limits_frequency_and_location_cap(runtime):
    result = run(runtime, "planes. planes. music.", features="nouns", min_occurrences=2,
                 max_occurrences=1, limit=1)
    assert len(result.keywords) == 1
    k = result.keywords[0]
    assert k.evidence[0].value == 2 and k.metadata["occurrences_omitted"] == 1
    assert len(k.metadata["occurrences"]) == 1
    k = run(runtime, "planes.", features="nouns", max_occurrences=0).keywords[0]
    assert not k.metadata["occurrences"] and k.metadata["occurrences_omitted"] == 1
    assert not run(runtime, "May", features="proper-nouns", min_length=4).keywords
    assert not run(runtime, "the bright paper planes", features="noun-chunks", max_words=2).keywords


def test_multiple_sources_duplicate_paths_crlf_and_unicode(runtime, tmp_path):
    text = "Zoë sings.\r\nplanes."
    first = tmp_path / "first.txt"
    first.write_bytes(b"\xef\xbb\xbf" + text.encode())
    plugin = nk.NLTKKeywordPlugin()
    plugin._load = lambda cfg: runtime
    result = plugin.run(PluginRequest(operation="extract", inputs=(first, first),
                                     options={"text": "planes.", "features": "nouns,proper-nouns"}),
                        ExecutionContext(llms=None))
    assert len(result.metadata["sources"]) == 2
    plane = index(result)["plane"]
    assert plane.metadata["document_frequency"] == 2 and plane.evidence[0].value == 2
    loc = next(x for x in plane.metadata["occurrences"] if x["source"] != "inline:text")
    assert text[loc["start_char"]:loc["end_char"]] == "planes"
    assert "Zoë" in index(result)


def test_determinism_and_batch_sizes(runtime):
    text = "music. plane. Kieran Simkin. Brighton. paper planes."
    assert run(runtime, text, batch_size=1).to_dict() == run(runtime, text, batch_size=16).to_dict()


def test_cli_inline_and_file(runtime, monkeypatch, capsys):
    monkeypatch.setattr(nk.NLTKKeywordPlugin, "_load", lambda self, cfg: runtime)
    assert main(["run", "nltk", "--operation", "extract", "--option", "text=Kieran Simkin."]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plugin"] == "nltk"
    assert "Kieran Simkin" in {k["phrase"] for k in result["keywords"]}
    fixture = Path(__file__).parent / "fixtures" / "nltk_reference.txt"
    assert main(["run", "nltk", "--operation", "extract", "--input", str(fixture),
                 "--format", "text"]) == 0
    assert "Kieran Simkin\tnltk-extracted" in capsys.readouterr().out


def test_resource_failure_during_inference_is_clean(runtime, monkeypatch, capsys):
    def missing(*args, **kwargs):
        raise LookupError("missing corpus")

    runtime.lemmatizer.lemmatize = missing
    monkeypatch.setattr(nk.NLTKKeywordPlugin, "_load", lambda self, cfg: runtime)
    assert main(["run", "nltk", "--operation", "extract", "--option", "text=planes."]) == 2
    assert "python -m nltk.downloader" in capsys.readouterr().err


@pytest.mark.parametrize("exception", [OSError("bad data"), ValueError("bad data")])
def test_processing_failures_are_wrapped(runtime, exception):
    def fail(*args, **kwargs):
        raise exception

    runtime.tagger.tag_sents = fail
    with pytest.raises(InputError, match="could not process"):
        run(runtime, "planes")


def test_inconsistent_tagger_and_chunk_offsets_are_rejected(runtime):
    runtime.tagger.tag_sents = lambda sents: [[("changed", "NN")]]
    with pytest.raises(InputError, match="changed tokens"):
        run(runtime, "planes")
    with pytest.raises(InputError, match="changed token order"):
        list(nk._tree_spans(nltk.Tree("S", [("wrong", "NN")]), [("right", "NN")]))


def test_inconsistent_tokenizer_offsets(runtime):
    runtime.words = SimpleNamespace(tokenize=lambda text: ["x"], span_tokenize=lambda text: [])
    with pytest.raises(InputError, match="inconsistent source offsets"):
        run(runtime, "planes")


def test_real_runtime_loader_caches_and_loads_only_selected_components(runtime, monkeypatch):
    import nltk.chunk.named_entity
    import nltk.corpus
    import nltk.stem
    import nltk.tag
    import nltk.tokenize

    calls = []
    monkeypatch.setattr(nk, "_require_data", lambda library, resources: calls.append(resources))
    monkeypatch.setattr(nltk.tokenize, "PunktTokenizer", lambda language: runtime.sentences)
    monkeypatch.setattr(nltk.tag, "PerceptronTagger", lambda lang: runtime.tagger)
    monkeypatch.setattr(nltk.chunk.named_entity, "Maxent_NE_Chunker", lambda fmt: runtime.ner)
    monkeypatch.setattr(nltk.stem, "WordNetLemmatizer", lambda: runtime.lemmatizer)
    monkeypatch.setattr(nltk.corpus, "stopwords", SimpleNamespace(words=lambda lang: runtime.stopwords))
    plugin = nk.NLTKKeywordPlugin()
    cfg = nk._Settings.parse({})
    loaded = plugin._load(cfg)
    assert plugin._load(cfg) is loaded and len(calls) == 1
    assert run(loaded, "the bright paper planes").keywords
    minimal = plugin._load(nk._Settings.parse({"features": "nouns", "lemmatize": False}))
    assert minimal.ner is None and minimal.lemmatizer is None and len(calls) == 2
    assert "planes" in index(run(minimal, "planes", features="nouns", lemmatize=False))


def test_loading_broken_data_is_actionable(monkeypatch):
    import nltk.tokenize

    def broken(language):
        raise ValueError("broken data")

    monkeypatch.setattr(nk, "_require_data", lambda *args: None)
    monkeypatch.setattr(nltk.tokenize, "PunktTokenizer", broken)
    with pytest.raises(ConfigurationError, match="Cannot load NLTK data"):
        nk.NLTKKeywordPlugin()._load(nk._Settings.parse({}))


def test_extra_stopwords_filter_names_and_lemmatised_forms(runtime):
    assert not run(runtime, "Brighton", features="proper-nouns", stopwords="brighton").keywords
    assert not run(runtime, "planes", features="nouns", stopwords="plane").keywords
    runtime.stopwords = runtime.stopwords | {"plane"}
    assert not run(runtime, "planes", features="nouns").keywords
