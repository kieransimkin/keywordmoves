"""Adapter tests use controlled embeddings; optional tests exercise real KeyBERT."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from keywordmoves.builtin import keybert_keywords as kb
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError, KeywordMovesError
from keywordmoves.models import ExecutionContext, PluginRequest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")


class Tokenizer:
    def encode(self, text, *, add_special_tokens, truncation):
        assert add_special_tokens is True and truncation is False
        # A long word consumes several wordpieces, so tests also cover subword limits.
        return [0] * (2 + sum(max(1, (len(word) + 5) // 6) for word in text.split()))

    def num_special_tokens_to_add(self):
        return 2


class Encoder:
    max_seq_length = 16
    tokenizer = Tokenizer()

    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        values = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            row = np.array(list(digest[:8]), dtype=float) + 1
            values.append(row / np.linalg.norm(row))
        return np.array(values)


class Ranker:
    def __init__(self):
        self.calls = []

    def extract_keywords(self, **kwargs):
        self.calls.append(kwargs)
        vectorizer = kwargs["vectorizer"].fit([kwargs["docs"]])
        counts = vectorizer.transform([kwargs["docs"]])
        indices = counts[0].nonzero()[1]
        words = vectorizer.get_feature_names_out()
        embeddings = kwargs["word_embeddings"]
        document = kwargs["doc_embeddings"][0]
        scores = [(words[i], round(float(np.dot(embeddings[i], document)), 4)) for i in indices]
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:kwargs["top_n"]]


@pytest.fixture
def runtime(monkeypatch):
    encoder, ranker = Encoder(), Ranker()
    monkeypatch.setattr(kb.KeyBERTKeywordPlugin, "_load", lambda self, cfg: (encoder, ranker))
    return encoder, ranker


def run(text, *, keywords=(), inputs=(), **options):
    if text is not None:
        options["text"] = text
    return kb.KeyBERTKeywordPlugin().run(
        PluginRequest(operation="extract", inputs=inputs, keywords=keywords, options=options),
        ExecutionContext(llms=None),
    )


def test_preserves_literal_spans_names_and_no_invented_adjacency(runtime):
    text = "Kieran Simkin visits Brighton. Paper and planes\nNeon lights; Neon lights."
    result = run(text, limit=100)
    items = {item.phrase: item for item in result.keywords}
    assert "kieran simkin" in items and "paper and planes" in items
    assert "paper planes" not in items and "brighton paper" not in items
    assert "planes neon" not in items and "lights neon" not in items
    assert items["neon lights"].metadata["occurrences_total"] == 2
    assert items["kieran simkin"].metadata["surface_forms"] == ["Kieran Simkin"]
    for item in result.keywords:
        assert -1 <= item.score <= 1
        for occurrence in item.metadata["occurrences"]:
            assert text[occurrence["start_char"]:occurrence["end_char"]] == occurrence["text"]
    assert runtime[1].calls[0]["use_mmr"] is False
    json.dumps(result.to_dict(), allow_nan=False)


def test_unicode_hyphens_apostrophes_and_whitespace(runtime):
    text = "São Paulo meets Jean-Luc. O’Connor writes café songs."
    result = run(text, limit=100, stop_words="none")
    phrases = {item.phrase for item in result.keywords}
    assert {"são paulo", "jean-luc", "o’connor", "café songs"} <= phrases


def test_options_shape_candidates_and_explicit_candidate_filter(runtime):
    result = run("Neon lights and paper planes. Paper planes return.",
                 keywords=("Paper Planes", "absent concept"), min_words=2, max_words=2,
                 min_occurrences=2, stopwords="return")
    assert [item.phrase for item in result.keywords] == ["paper planes"]
    assert result.keywords[0].evidence[1].value == 2
    assert result.metadata["candidate_filter"] == "explicit"
    assert runtime[1].calls[0]["word_embeddings"].shape[0] == 1


def test_multiple_documents_do_not_join_and_counts_cover_unselected_documents(runtime, tmp_path):
    path = tmp_path / "second.txt"
    path.write_text("Paper planes. City lights.", encoding="utf-8")
    result = run("Paper planes. Neon lights.", inputs=(path, path), min_df=2, limit=30)
    assert len(result.metadata["sources"]) == 2
    assert {k.phrase for k in result.keywords} == {"paper", "planes", "paper planes", "lights"}
    for item in result.keywords:
        assert item.metadata["document_frequency"] == 2
        assert item.metadata["occurrences_total"] == 2
        assert len(item.metadata["document_scores"]) == 2
        assert item.score == max(x["score"] for x in item.metadata["document_scores"])
    assert len(runtime[1].calls) == 2
    assert runtime[1].calls[0]["word_embeddings"] is runtime[1].calls[1]["word_embeddings"]


@pytest.mark.parametrize("text,options", [
    ("the and or", {}), ("123 456 !!!", {}), ("paper planes", {"min_df": 2}),
    ("paper planes", {"min_occurrences": 2}), ("paper planes", {"stopwords": "paper,planes"}),
])
def test_empty_vocabulary_never_loads_model(monkeypatch, text, options):
    def forbidden(self, cfg):
        raise AssertionError("model should not be loaded")
    monkeypatch.setattr(kb.KeyBERTKeywordPlugin, "_load", forbidden)
    result = run(text, **options)
    assert result.keywords == () and result.metadata["candidate_count"] == 0


def test_candidate_budget_before_model(monkeypatch):
    monkeypatch.setattr(kb.KeyBERTKeywordPlugin, "_load", lambda *a: pytest.fail("loaded model"))
    with pytest.raises(InputError, match="max_candidates"):
        run("alpha beta gamma delta", max_candidates=2)


def test_mmr_arguments_and_maxsum_small_vocabulary(runtime):
    run("paper planes and neon lights", method="mmr", diversity=0.8, limit=3)
    call = runtime[1].calls[-1]
    assert call["use_mmr"] and not call["use_maxsum"] and call["diversity"] == 0.8
    result = run("paper planes", method="maxsum", nr_candidates=20, limit=20)
    call = runtime[1].calls[-1]
    assert call["top_n"] == 3 and call["nr_candidates"] == 3
    assert call["use_maxsum"] and len(result.keywords) == 3


def test_maxsum_budget_guard_before_model(monkeypatch):
    monkeypatch.setattr(kb.KeyBERTKeywordPlugin, "_load", lambda *a: pytest.fail("loaded model"))
    with pytest.raises(ConfigurationError, match="combination budget"):
        run(" ".join(f"word{i}" for i in range(30)), method="maxsum", nr_candidates=30, limit=10)


def test_long_document_all_words_are_embedded_without_truncation(runtime):
    text = " ".join(["Paper planes above Brighton."] * 50)
    result = run(text, max_occurrences=1, limit=20)
    encoder = runtime[0]
    chunks = encoder.calls[1][0]
    assert " ".join(chunks) == text
    assert all(len(encoder.tokenizer.encode(c, add_special_tokens=True, truncation=False)) <= encoder.max_seq_length
               for c in chunks)
    assert result.metadata["sources"][0]["embedding_chunks"] == len(chunks) > 1
    assert any("not truncated" in note for note in result.notes)
    for _, kwargs in encoder.calls:
        assert kwargs["show_progress_bar"] is False and kwargs["prompt"] == ""
    item = next(k for k in result.keywords if k.phrase == "paper planes")
    assert item.metadata["occurrences_total"] == 50
    assert item.metadata["occurrences_truncated"] and len(item.metadata["occurrences"]) == 1


def test_occurrence_sample_can_be_disabled(runtime):
    result = run("paper planes", max_occurrences=0)
    assert all(not k.metadata["occurrences"] for k in result.keywords)
    assert all(k.metadata["occurrences_total"] == 1 for k in result.keywords)


def test_oversized_word_and_invalid_context_window():
    encoder = Encoder()
    with pytest.raises(InputError, match="context window"):
        kb._chunks("z" * 300, encoder)
    encoder.max_seq_length = None
    with pytest.raises(ConfigurationError, match="max_seq_length"):
        kb._chunks("paper planes", encoder)
    encoder.max_seq_length = 16
    assert kb._chunks("\n  ", encoder) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.2, True])
def test_bad_scores(runtime, value):
    runtime[1].extract_keywords = lambda **kwargs: [("paper", value)]
    with pytest.raises(KeywordMovesError, match="invalid similarity"):
        run("paper planes")


@pytest.mark.parametrize("result", [[], None, ["paper"], [("absent", 0.5)], [("paper", 0.5), ("paper", 0.4)]])
def test_malformed_or_empty_rankings_raise_not_empty_success(runtime, result):
    runtime[1].extract_keywords = lambda **kwargs: result
    with pytest.raises(KeywordMovesError):
        run("paper planes")


def test_negative_scores_preserved(runtime):
    runtime[1].extract_keywords = lambda **kwargs: [("paper", -0.25)]
    result = run("paper planes")
    assert result.keywords[0].score == -0.25


@pytest.mark.parametrize("values", [np.zeros((1, 3)), np.array([[float("nan"), 1]]), np.ones(3)])
def test_invalid_embeddings_rejected(values):
    encoder = SimpleNamespace(encode=lambda *a, **kw: values)
    with pytest.raises(KeywordMovesError, match="embeddings"):
        kb._embeddings(encoder, ["paper planes"], kb._Settings.parse({}))


def test_cli_file_mode_and_json(runtime, capsys, tmp_path):
    path = tmp_path / "reference.txt"
    path.write_text("Paper planes over Brighton. Neon lights.", encoding="utf-8")
    assert main(["run", "keybert", "--operation", "extract", "--input", str(path),
                 "--option", "limit=3"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["keywords"]) == 3 and output["plugin"] == "keybert"
    assert main(["run", "keybert", "--operation", "extract", "--option", "text=Paper planes",
                 "--keyword", "paper planes", "--format", "text"]) == 0
    assert "paper planes" in capsys.readouterr().out


@pytest.mark.parametrize("method", ["cosine", "mmr", "maxsum"])
def test_real_keybert_api_with_controlled_embeddings(monkeypatch, method):
    # Actual KeyBERT scoring/selection and CountVectorizer; no encoder download.
    keybert = pytest.importorskip("keybert", reason="Install keywordmoves[keybert] for API tests")
    from keybert.backend import BaseEmbedder

    class NoEmbed(BaseEmbedder):
        def embed(self, documents, verbose=False):
            raise AssertionError("The adapter should supply precomputed embeddings")

    encoder, ranker = Encoder(), keybert.KeyBERT(model=NoEmbed(), llm=None)
    monkeypatch.setattr(kb.KeyBERTKeywordPlugin, "_load", lambda self, cfg: (encoder, ranker))
    options = {"method": method, "limit": 3}
    if method == "maxsum":
        options["nr_candidates"] = 6
    result = run("Paper planes over Brighton. Neon lights illuminate city streets.", **options)
    assert len(result.keywords) == 3
    assert all(-1 <= item.score <= 1 for item in result.keywords)


def test_document_without_eligible_candidates_is_not_embedded(runtime, tmp_path):
    path = tmp_path / "stopwords.txt"
    path.write_text("the and or", encoding="utf-8")
    result = run("paper planes", inputs=(path,))
    assert len(runtime[1].calls) == 1
    assert result.metadata["sources"][1]["embedding_chunks"] == 0


def test_oversized_candidate_is_rejected(runtime):
    with pytest.raises(InputError, match="candidate exceeds"):
        run("x" * 200, max_words=1)


def test_inconsistent_embedding_dimensions_are_rejected(runtime):
    encoder = runtime[0]
    original = encoder.encode

    def changed(texts, **kwargs):
        result = original(texts, **kwargs)
        return result[:, :3] if len(encoder.calls) > 1 else result

    encoder.encode = changed
    with pytest.raises(KeywordMovesError, match="dimensions"):
        run("paper planes")


def test_cancelled_chunk_average_is_rejected(runtime):
    encoder = runtime[0]
    original = encoder.encode

    def changed(texts, **kwargs):
        result = original(texts, **kwargs)
        if len(encoder.calls) == 2:
            assert len(result) == 2
            result[0] = np.ones(8)
            result[1] = -np.ones(8)
        return result

    encoder.encode = changed
    with pytest.raises(KeywordMovesError, match="document embedding"):
        run(" ".join(["word"] * 28), max_words=1)


def test_unexpected_backend_error_has_cli_error_type(runtime):
    def failed(**kwargs):
        raise ValueError("backend error")
    runtime[1].extract_keywords = failed
    with pytest.raises(KeywordMovesError, match="extraction failed"):
        run("paper planes")
