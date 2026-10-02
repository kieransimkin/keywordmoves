"""Real spaCy Doc/Span/POS/noun-chunk APIs with controlled annotations.

No language-model download is needed. The tiny pipeline wrapper supplies
hand-annotated Docs, so these test extraction logic, not statistical accuracy.
"""
import hashlib
import json
from pathlib import Path

import pytest

from keywordmoves.builtin.spacy_keywords import SpacyKeywordPlugin
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest

spacy = pytest.importorskip("spacy")
Doc = spacy.tokens.Doc

REFERENCE = "Kieran Simkin writes paper planes in Brighton. Paper planes inspire independent music."


def annotated(words, pos=None, lemmas=None, heads=None, deps=None, ents=None,
              spaces=None, lang="en"):
    nlp = spacy.blank(lang)
    if spaces is None:
        spaces = [i < len(words) - 1 and words[i + 1] not in {".", ",", "!"}
                  for i in range(len(words))]
    kwargs = {"pos": pos, "lemmas": lemmas, "heads": heads, "deps": deps, "ents": ents}
    if heads is None:
        kwargs["sent_starts"] = [True] + [False] * (len(words) - 1)
    return Doc(nlp.vocab, words=words, spaces=spaces,
               **{key: val for key, val in kwargs.items() if val is not None})


def reference_doc():
    return annotated(
        words=["Kieran", "Simkin", "writes", "paper", "planes", "in", "Brighton", ".",
               "Paper", "planes", "inspire", "independent", "music", "."],
        pos=["PROPN", "PROPN", "VERB", "NOUN", "NOUN", "ADP", "PROPN", "PUNCT",
             "NOUN", "NOUN", "VERB", "ADJ", "NOUN", "PUNCT"],
        lemmas=["Kieran", "Simkin", "write", "paper", "plane", "in", "Brighton", ".",
                "paper", "plane", "inspire", "independent", "music", "."],
        heads=[1, 2, 2, 4, 2, 2, 5, 2, 9, 10, 10, 12, 10, 10],
        deps=["compound", "nsubj", "ROOT", "compound", "dobj", "prep", "pobj", "punct",
              "compound", "nsubj", "ROOT", "amod", "dobj", "punct"],
        ents=["B-PERSON", "I-PERSON", "O", "O", "O", "O", "B-GPE", "O",
              "O", "O", "O", "O", "O", "O"],
    )


class Pipeline:
    meta = {"version": "annotated-test-v1"}
    pipe_names = ["test-annotations"]
    lang = "en"
    max_length = 1000000

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def pipe(self, texts, batch_size):
        for text in texts:
            self.calls.append((text, batch_size))
            yield self.docs[text]


class NoLLM:
    def get(self, name):
        raise AssertionError("spaCy must never select an LLM")


def extract(monkeypatch, doc=None, **options):
    doc = reference_doc() if doc is None else doc
    pipeline = Pipeline({doc.text: doc})
    monkeypatch.setattr(spacy, "load", lambda name: pipeline)
    return SpacyKeywordPlugin().run(
        PluginRequest(operation="extract", options={"text": doc.text, **options}),
        ExecutionContext(llms=NoLLM()),
    )


def by_phrase(result):
    return {item.phrase: item for item in result.keywords}


def test_proper_nouns_noun_chunks_entities_and_compounds(monkeypatch):
    doc = reference_doc()
    assert doc.text == REFERENCE
    assert "paper planes" in [chunk.text for chunk in doc.noun_chunks]
    result = extract(monkeypatch, doc)
    items = by_phrase(result)
    assert {"Kieran", "Simkin", "Kieran Simkin", "Brighton", "paper plane", "independent music"} <= items.keys()
    name = items["Kieran Simkin"]
    assert set(name.metadata["features"]) == {"entities", "proper-nouns", "noun-chunks", "keyphrases"}
    assert name.metadata["entity_labels"] == ["PERSON"]
    assert name.evidence[0].value == 1  # Four detectors must not count four occurrences.
    assert items["paper plane"].evidence[0].value == 2
    assert items["paper plane"].metadata["surface_forms"] == ["Paper planes", "paper planes"]
    assert "write" not in items and "independent" not in items
    assert result.metadata["spacy_version"] == spacy.__version__
    assert result.metadata["sources"][0]["text_sha256"] == hashlib.sha256(REFERENCE.encode()).hexdigest()
    assert "demand evidence" in result.notes[1]
    json.dumps(result.to_dict())


def test_offsets_are_exact_and_counts_not_metadata_cap(monkeypatch):
    result = extract(monkeypatch, max_occurrences=1)
    item = by_phrase(result)["paper plane"]
    assert item.evidence[0].value == 2 and item.metadata["occurrences_omitted"] == 1
    for keyword in result.keywords:
        for occurrence in keyword.metadata["occurrences"]:
            assert REFERENCE[occurrence["start_char"]:occurrence["end_char"]] in keyword.metadata["surface_forms"]
    hidden = by_phrase(extract(monkeypatch, max_occurrences=0))["paper plane"]
    assert hidden.metadata["occurrences"] == [] and hidden.metadata["occurrences_omitted"] == 2


def test_lemma_merging_and_name_preservation(monkeypatch):
    doc = annotated(["Arcadians", "planes", "plane"], pos=["PROPN", "NOUN", "NOUN"],
                    lemmas=["Arcadian", "plane", "plane"])
    items = by_phrase(extract(monkeypatch, doc, features="proper-nouns,nouns"))
    assert "Arcadians" in items and "Arcadian" not in items
    assert items["plane"].evidence[0].value == 2
    items = by_phrase(extract(monkeypatch, doc, features="proper-nouns,nouns", lemmatize=False))
    assert {"Arcadians", "planes", "plane"} == items.keys()


def test_determiners_and_pronouns_trimmed_but_name_stopwords_preserved(monkeypatch):
    doc = annotated(["The", "bright", "paper", "planes", "fly", "."],
                    pos=["DET", "ADJ", "NOUN", "NOUN", "VERB", "PUNCT"],
                    lemmas=["the", "bright", "paper", "plane", "fly", "."],
                    heads=[3, 3, 3, 4, 4, 4], deps=["det", "amod", "compound", "nsubj", "ROOT", "punct"])
    items = by_phrase(extract(monkeypatch, doc, features="noun-chunks"))
    assert set(items) == {"bright paper plane"}
    doc = annotated(["The", "Who"], pos=["DET", "PROPN"], ents=["B-ORG", "I-ORG"])
    items = by_phrase(extract(monkeypatch, doc, features="entities,proper-nouns"))
    assert "The Who" in items and "Who" in items


def test_plain_noun_chunk_pronoun_is_not_a_keyword(monkeypatch):
    doc = annotated(["She", "sings", "."], pos=["PRON", "VERB", "PUNCT"],
                    heads=[1, 1, 1], deps=["nsubj", "ROOT", "punct"])
    assert extract(monkeypatch, doc, features="noun-chunks").keywords == ()


def test_numeric_entities_are_not_reintroduced_by_pos_features(monkeypatch):
    doc = annotated(["October", "Brighton", "2026"], pos=["PROPN", "PROPN", "NUM"],
                    ents=["B-DATE", "B-GPE", "B-DATE"])
    items = by_phrase(extract(monkeypatch, doc, features="entities,proper-nouns,keyphrases"))
    assert set(items) == {"Brighton"}
    items = by_phrase(extract(monkeypatch, doc, features="entities,proper-nouns", entity_labels="*"))
    assert "October" in items and "2026" not in items
    items = by_phrase(extract(monkeypatch, features="entities", entity_labels="PERSON"))
    assert set(items) == {"Kieran Simkin"}


def test_stopwords_noise_filtering_and_optional_adjectives_verbs(monkeypatch):
    items = by_phrase(extract(monkeypatch, features="nouns,adjectives,verbs"))
    assert {"write", "inspire", "independent", "plane"} <= items.keys()
    items = by_phrase(extract(monkeypatch, features="nouns", stopwords="paper,plane"))
    assert set(items) == {"music"}
    doc = annotated(["the", "and", "42", "https://example.com", "a@example.com", "!!!"],
                    pos=["NOUN"] * 6)
    assert not extract(monkeypatch, doc, features="nouns").keywords


def test_feature_selection_deterministic_sort_limit_frequency_and_size(monkeypatch):
    first = extract(monkeypatch, features="proper-nouns,noun-chunks", limit=3)
    assert first == extract(monkeypatch, features="proper-nouns,noun-chunks", limit=3)
    assert len(first.keywords) == 3
    assert all(set(k.metadata["features"]) <= {"proper-nouns", "noun-chunks"} for k in first.keywords)
    assert [k.score for k in first.keywords] == sorted([k.score for k in first.keywords], reverse=True)
    assert all(0 < k.score <= 1 for k in first.keywords)
    repeated = extract(monkeypatch, min_occurrences=2)
    assert set(by_phrase(repeated)) == {"paper", "plane", "paper plane"}
    single = extract(monkeypatch, max_words=1)
    assert all(k.metadata["word_count"] == 1 for k in single.keywords)
    long = extract(monkeypatch, min_length=20)
    assert not long.keywords and "No candidates" in long.notes[-1]


def test_names_and_keyphrases_do_not_cross_lines_or_sentences(monkeypatch):
    doc = annotated(["Kieran", "\n", "Brighton"], pos=["PROPN", "SPACE", "PROPN"],
                    spaces=[False, False, False])
    assert set(by_phrase(extract(monkeypatch, doc, features="proper-nouns,keyphrases"))) == {"Kieran", "Brighton"}
    doc = annotated(["Kieran", "Brighton"], pos=["PROPN", "PROPN"], heads=[0, 1], deps=["ROOT", "ROOT"])
    assert set(by_phrase(extract(monkeypatch, doc, features="proper-nouns,keyphrases"))) == {"Kieran", "Brighton"}
    doc = annotated(["Jean", "-", "Luc"], pos=["PROPN", "PUNCT", "PROPN"], spaces=[False] * 3)
    assert "Jean-Luc" in by_phrase(extract(monkeypatch, doc, features="proper-nouns"))


def test_no_stopword_deletion_invents_contiguous_compounds(monkeypatch):
    doc = annotated(["paper", "with", "planes"], pos=["NOUN", "ADP", "NOUN"])
    assert not extract(monkeypatch, doc, features="keyphrases").keywords


def test_unicode_and_single_letter_name_option(monkeypatch):
    doc = annotated(["Beyoncé", "Zürich", ".", "X"], pos=["PROPN", "PROPN", "PUNCT", "PROPN"])
    items = by_phrase(extract(monkeypatch, doc, features="proper-nouns", min_length=1))
    assert {"Beyoncé", "Zürich", "X"} <= items.keys()


@pytest.mark.parametrize("doc,features,error", [
    (lambda: annotated(["paper"]), "nouns", "POS"),
    (lambda: annotated(["paper"], pos=["NOUN"]), "noun-chunks", "DEP"),
    (lambda: annotated(["paper"], pos=["NOUN"]), "entities", "ENT_IOB"),
    (lambda: annotated(["paper"], pos=["NOUN"], heads=[0], deps=["ROOT"], lang="xx"),
     "noun-chunks", "no noun-chunk iterator"),
])
def test_missing_annotations_do_not_silently_omit_features(monkeypatch, doc, features, error):
    with pytest.raises(ConfigurationError, match=error):
        extract(monkeypatch, doc(), features=features)


def test_entity_only_pipeline_does_not_need_pos_or_parser(monkeypatch):
    doc = annotated(["Brighton"], ents=["B-GPE"])
    assert set(by_phrase(extract(monkeypatch, doc, features="entities"))) == {"Brighton"}


def test_missing_model_and_broken_pipeline_are_actionable(monkeypatch):
    def missing(name):
        raise OSError("model not found")

    monkeypatch.setattr(spacy, "load", missing)
    with pytest.raises(ConfigurationError, match="python -m spacy download"):
        SpacyKeywordPlugin()._load("missing_model")
    pipeline = Pipeline({})

    def broken(*args, **kwargs):
        raise ValueError("invalid tensor shape")

    pipeline.pipe = broken
    monkeypatch.setattr(spacy, "load", lambda name: pipeline)
    with pytest.raises(InputError, match="could not process"):
        SpacyKeywordPlugin().run(PluginRequest(operation="extract", options={"text": "Hello"}),
                                 ExecutionContext(llms=NoLLM()))


def test_pipeline_cache_and_per_document_processing(monkeypatch, tmp_path):
    doc = reference_doc()
    pipeline = Pipeline({doc.text: doc, " ": spacy.blank("en")(" ")})
    loaded = []

    def load(name):
        loaded.append(name)
        return pipeline

    monkeypatch.setattr(spacy, "load", load)
    plugin = SpacyKeywordPlugin()
    first, second, empty = (tmp_path / name for name in ["first.txt", "second.txt", "empty.txt"])
    for path in (first, second):
        path.write_text(doc.text, encoding="utf-8")
    empty.write_text(" ", encoding="utf-8")
    request = PluginRequest(operation="extract", inputs=(first, second, empty),
                            options={"spacy_model": "custom-model", "batch_size": 2})
    result = plugin.run(request, ExecutionContext(llms=NoLLM()))
    assert result == plugin.run(request, ExecutionContext(llms=NoLLM()))
    assert loaded == ["custom-model"] and all(batch == 2 for _, batch in pipeline.calls)
    item = by_phrase(result)["Kieran Simkin"]
    assert item.metadata["document_frequency"] == 2 and item.evidence[0].value == 2
    assert len(result.metadata["sources"]) == 3
    assert any("Skipped empty" in note for note in result.notes)


def test_cli_file_and_inline_text_output(monkeypatch, capsys):
    doc = reference_doc()
    monkeypatch.setattr(spacy, "load", lambda name: Pipeline({doc.text: doc}))
    fixture = Path(__file__).parent / "fixtures" / "spacy_reference.txt"
    assert fixture.read_text(encoding="utf-8") == doc.text
    assert main(["run", "spacy", "--operation", "extract", "--input", str(fixture)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plugin"] == "spacy" and result["metadata"]["spacy_model"] == "en_core_web_sm"
    assert main(["run", "spacy", "--operation", "extract", "--option", f"text={doc.text}",
                 "--option", "features=entities", "--format", "text"]) == 0
    output = capsys.readouterr()
    assert "Kieran Simkin" in output.out and "no LLM" in output.err


def test_missing_lemmas_retain_surface_with_explicit_note(monkeypatch):
    doc = annotated(["planes"], pos=["NOUN"])
    result = extract(monkeypatch, doc, features="nouns")
    assert set(by_phrase(result)) == {"planes"}
    assert any("lemmas were unavailable" in note for note in result.notes)


def test_proper_names_do_not_merge_separate_entity_spans(monkeypatch):
    doc = annotated(["Brighton", "London"], pos=["PROPN", "PROPN"], ents=["B-GPE", "B-GPE"])
    assert set(by_phrase(extract(monkeypatch, doc, features="proper-nouns"))) == {"Brighton", "London"}


@pytest.mark.parametrize("docs", [[], [spacy.blank("en")("Changed text")]])
def test_pipeline_must_return_one_unchanged_doc_per_input(monkeypatch, docs):
    pipeline = Pipeline({})
    pipeline.pipe = lambda *args, **kwargs: iter(docs)
    monkeypatch.setattr(spacy, "load", lambda name: pipeline)
    with pytest.raises(InputError):
        SpacyKeywordPlugin().run(
            PluginRequest(operation="extract", options={"text": "Original"}),
            ExecutionContext(llms=NoLLM()),
        )


def test_exact_stop_phrase_does_not_remove_its_constituent_nouns(monkeypatch):
    items = by_phrase(extract(monkeypatch, stopwords="paper plane"))
    assert "paper plane" not in items
    assert "paper" in items and "plane" in items


def test_saved_local_entity_pipeline_uses_actual_spacy_loader(tmp_path):
    # A genuine saved/loaded spaCy pipeline, without trained-model weights or downloads.
    nlp = spacy.blank("en")
    ruler = nlp.add_pipe("entity_ruler")
    ruler.add_patterns([{"label": "GPE", "pattern": "Brighton"}])
    model_path = tmp_path / "local-pipeline"
    nlp.to_disk(model_path)
    result = SpacyKeywordPlugin().run(
        PluginRequest(operation="extract", options={
            "text": "Brighton is by the sea.", "features": "entities",
            "spacy_model": str(model_path),
        }),
        ExecutionContext(llms=NoLLM()),
    )
    assert set(by_phrase(result)) == {"Brighton"}
    assert result.metadata["pipeline"] == ["entity_ruler"]
    assert by_phrase(result)["Brighton"].metadata["entity_labels"] == ["GPE"]


def test_actual_missing_model_is_reported_without_fallback():
    with pytest.raises(ConfigurationError, match="No pipeline was downloaded automatically"):
        SpacyKeywordPlugin()._load("keywordmoves_missing_model_for_test_859724")
