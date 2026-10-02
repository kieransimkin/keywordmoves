"""Input, configuration and discoverability tests without NLTK data or even NLTK."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from keywordmoves.builtin import nltk_keywords as nk
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": True}, {"limit": 1.5}, {"limit": "oops"}, {"limit": 10001},
    {"min_occurrences": 0}, {"min_length": -1}, {"max_words": 0}, {"max_words": 51},
    {"max_chars": 0}, {"batch_size": 0}, {"max_occurrences": -1},
    {"max_occurrences": 1001}, {"lemmatize": "yes"}, {"lemmatize": 1},
    {"language": "german"}, {"language": None}, {"features": ""},
    {"features": "noun-phrases"}, {"features": ["nouns", 1]}, {"features": False},
    {"entity_labels": ""}, {"entity_labels": "*,PERSON"}, {"stopwords": 42},
    {"unknown": True}, {"llm_model": "english"}, {"llm": "openai"},
])
def test_invalid_options(options):
    with pytest.raises(ConfigurationError):
        nk._Settings.parse(options)


def test_valid_options_and_resource_selection():
    cfg = nk._Settings.parse({"features": ["nouns"], "limit": "7", "stopwords": "Noise,music",
                              "lemmatize": "false", "max_occurrences": 0})
    assert cfg.limit == 7 and not cfg.lemmatize
    assert cfg.stopwords == {"noise", "music"}
    assert nk._resources(cfg) == ("punkt_tab", "averaged_perceptron_tagger_eng", "stopwords")
    assert set(nk._resources(nk._Settings.parse({}))) == set(nk.RESOURCE_PATHS)


@pytest.mark.parametrize("options", [{}, {"text": ""}, {"text": " \n"}, {"text": True}])
def test_missing_reference_text(options):
    with pytest.raises(InputError):
        nk._documents(PluginRequest(operation="extract", options=options), nk._Settings.parse(options))


def test_size_limit_no_truncation():
    options = {"text": "some text", "max_chars": 3}
    with pytest.raises(InputError, match="no text was processed"):
        nk._documents(PluginRequest(operation="extract", options=options), nk._Settings.parse(options))


def test_files_preserve_crlf_bom_handling_order_and_deduplication(tmp_path):
    first = tmp_path / "one.TXT"
    first.write_bytes(b"\xef\xbb\xbfPaper planes.\r\nBrighton.")
    folder = tmp_path / "nested"
    folder.mkdir()
    second = folder / "two.md"
    second.write_text("Music.", encoding="utf-8")
    (folder / "ignored.pdf").write_bytes(b"not text")
    request = PluginRequest(operation="extract", inputs=(first, tmp_path, first))
    assert nk._documents(request, nk._Settings.parse({})) == [
        (str(first.resolve()), "Paper planes.\r\nBrighton."), (str(second.resolve()), "Music."),
    ]
    with pytest.raises(InputError, match="max_chars"):
        nk._documents(request, nk._Settings.parse({"max_chars": 3}))


def test_invalid_input_paths_and_encoding(tmp_path):
    for path in (tmp_path, tmp_path / "missing.txt"):
        with pytest.raises(InputError):
            nk._documents(PluginRequest(operation="extract", inputs=(path,)), nk._Settings.parse({}))
    for name, content in [("bad.exe", b"text"), ("bad.txt", b"\xff"), ("empty.txt", b" \n")]:
        path = tmp_path / name
        path.write_bytes(content)
        with pytest.raises(InputError):
            nk._documents(PluginRequest(operation="extract", inputs=(path,)), nk._Settings.parse({}))


def test_unreadable_file(tmp_path, monkeypatch):
    path = tmp_path / "text.txt"
    path.write_text("music", encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(InputError, match="permissions"):
        nk._documents(PluginRequest(operation="extract", inputs=(path,)), nk._Settings.parse({}))


def test_missing_dependency_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "nltk", None)
    with pytest.raises(ConfigurationError, match=r"keywordmoves\[nltk\]"):
        nk.NLTKKeywordPlugin()._load(nk._Settings.parse({}))


def test_all_missing_resources_reported_without_downloading():
    calls = []

    def find(path):
        calls.append(path)
        raise LookupError(path)

    library = SimpleNamespace(data=SimpleNamespace(find=find))
    with pytest.raises(ConfigurationError) as error:
        nk._require_data(library, nk._resources(nk._Settings.parse({})))
    assert "python -m nltk.downloader" in str(error.value)
    assert "NLTK_DATA" in str(error.value)
    for resource in nk.RESOURCE_PATHS:
        assert resource in str(error.value)
    assert len(calls) == sum(map(len, nk.RESOURCE_PATHS.values()))


def test_zipped_wordnet_resource_is_accepted():
    calls = []

    def find(path):
        calls.append(path)
        if ".zip/" not in path:
            raise LookupError(path)
        return path

    nk._require_data(SimpleNamespace(data=SimpleNamespace(find=find)), ("wordnet",))
    assert calls == ["corpora/wordnet", "corpora/wordnet.zip/wordnet/"]


def test_unsupported_operation_and_keyword_seeds():
    for request in [PluginRequest(operation="generate"),
                    PluginRequest(operation="extract", keywords=("seed",))]:
        with pytest.raises(ConfigurationError):
            nk.NLTKKeywordPlugin().run(request, ExecutionContext(llms=None))


def test_cli_errors(capsys):
    for args, message in [([], "reference text"), (["--option", "limit=0"], "limit"),
                          (["--model", "english"], "LLM options")]:
        assert main(["run", "nltk", "--operation", "extract", *args]) == 2
        assert message in capsys.readouterr().err


def test_no_optional_dependencies_needed_to_list_plugins():
    root = Path(__file__).resolve().parents[1]
    code = '''import sys
sys.modules.update({name: None for name in ("nltk", "spacy", "openai", "torch", "transformers")})
from keywordmoves.cli import main
raise SystemExit(main(["plugins", "--json"]))
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=root,
                            env={**os.environ, "PYTHONPATH": str(root / "src")},
                            text=True, capture_output=True, check=True)
    items = json.loads(result.stdout)
    assert {item["name"] for item in items if item["kind"] == "keyword"} == {
        "google-trends", "observed-evidence", "nltk", "spacy", "text-library",
    }
