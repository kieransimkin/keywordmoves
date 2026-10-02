"""Configuration, input and lazy-loading tests that do not require spaCy."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keywordmoves.builtin import spacy_keywords
from keywordmoves.builtin.spacy_keywords import SpacyKeywordPlugin
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": True}, {"limit": 1.5}, {"limit": "oops"},
    {"limit": 10001}, {"min_occurrences": 0}, {"min_length": -1},
    {"max_words": 0}, {"max_words": 51}, {"max_chars": 0},
    {"batch_size": 0}, {"max_occurrences": -1}, {"max_occurrences": 1001},
    {"lemmatize": "yes"}, {"lemmatize": 1}, {"spacy_model": None},
    {"spacy_model": " "}, {"features": ""}, {"features": "proper-noun"},
    {"features": ["nouns", 1]}, {"features": False}, {"entity_labels": ""},
    {"entity_labels": "*,PERSON"}, {"stopwords": 42}, {"unknown": True},
    {"llm_model": "en_core_web_sm"}, {"llm": "openai"},
])
def test_invalid_options_raise_configuration_error(options):
    with pytest.raises(ConfigurationError):
        spacy_keywords._Settings.parse(options)


def test_valid_settings_and_sequences():
    cfg = spacy_keywords._Settings.parse({"limit": "7", "lemmatize": "false", "max_occurrences": 0,
                           "features": ["nouns"], "stopwords": "Thing,stuff"})
    assert cfg.limit == 7 and not cfg.lemmatize
    assert cfg.stopwords == {"thing", "stuff"}
    assert cfg.features == {"nouns"}


@pytest.mark.parametrize("options", [{}, {"text": ""}, {"text": " \n"}, {"text": True}])
def test_reference_text_is_required(options):
    with pytest.raises(InputError):
        spacy_keywords._documents(PluginRequest(operation="extract", options=options), spacy_keywords._Settings.parse(options))


def test_inline_limit_is_not_silent_truncation():
    options = {"text": "some reference", "max_chars": 3}
    with pytest.raises(InputError, match="no text was processed"):
        spacy_keywords._documents(PluginRequest(operation="extract", options=options), spacy_keywords._Settings.parse(options))


def test_file_io_bom_crlf_duplicates_directory_and_limit(tmp_path):
    first = tmp_path / "reference.TXT"
    first.write_bytes(b"\xef\xbb\xbfPaper planes.\r\nBrighton.")
    nested = tmp_path / "nested"
    nested.mkdir()
    second = nested / "reference.md"
    second.write_text("Music.", encoding="utf-8")
    (tmp_path / "ignored.pdf").write_bytes(b"not text")
    request = PluginRequest(operation="extract", inputs=(first, tmp_path, first))
    docs = spacy_keywords._documents(request, spacy_keywords._Settings.parse({}))
    assert docs == [(str(first.resolve()), "Paper planes.\nBrighton."),
                    (str(second.resolve()), "Music.")]
    with pytest.raises(InputError, match="max_chars"):
        spacy_keywords._documents(request, spacy_keywords._Settings.parse({"max_chars": 3}))


def test_bad_inputs_are_input_errors(tmp_path):
    for path in (tmp_path / "missing.txt", tmp_path):
        with pytest.raises(InputError):
            spacy_keywords._documents(PluginRequest(operation="extract", inputs=(path,)), spacy_keywords._Settings.parse({}))
    for name, content in [("bad.txt", b"\xff\xfe\x00"), ("bad.exe", b"text"), ("empty.txt", b" \n")]:
        path = tmp_path / name
        path.write_bytes(content)
        with pytest.raises(InputError):
            spacy_keywords._documents(PluginRequest(operation="extract", inputs=(path,)), spacy_keywords._Settings.parse({}))


def test_unreadable_input_is_clean_error(tmp_path, monkeypatch):
    path = tmp_path / "reference.txt"
    path.write_text("Hello", encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(InputError, match="permissions"):
        spacy_keywords._documents(PluginRequest(operation="extract", inputs=(path,)), spacy_keywords._Settings.parse({}))


def test_missing_optional_dependency_is_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, "spacy", None)
    with pytest.raises(ConfigurationError, match=r"keywordmoves\[spacy\]"):
        SpacyKeywordPlugin()._load("en_core_web_sm")


def test_unsupported_operation_and_seeds():
    for request in [PluginRequest(operation="generate"),
                    PluginRequest(operation="extract", keywords=("seed",))]:
        with pytest.raises(ConfigurationError):
            SpacyKeywordPlugin().run(request, ExecutionContext(llms=None))


def test_cli_reports_configuration_and_input_errors(capsys):
    assert main(["run", "spacy", "--operation", "extract", "--option", "limit=0"]) == 2
    assert "limit" in capsys.readouterr().err
    assert main(["run", "spacy", "--operation", "extract"]) == 2
    assert "reference text" in capsys.readouterr().err
    assert main(["run", "spacy", "--operation", "extract", "--model", "en_core_web_sm"]) == 2
    assert "spacy_model" in capsys.readouterr().err


def test_listing_works_when_optional_libraries_cannot_import():
    root = Path(__file__).resolve().parents[1]
    code = '''import sys
sys.modules.update({name: None for name in ("spacy", "torch", "transformers", "openai")})
from keywordmoves.cli import main
raise SystemExit(main(["plugins", "--json"]))
'''
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, check=True)
    items = json.loads(result.stdout)
    assert {item["name"] for item in items if item["kind"] == "keyword"} == {
        "google-trends", "observed-evidence", "spacy", "text-library",
    }
