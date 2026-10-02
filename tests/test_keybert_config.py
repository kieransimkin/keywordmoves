"""Configuration, resource bounds and lazy discovery without model downloads."""
import builtins
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from keywordmoves.builtin import keybert_keywords as kb
from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": True}, {"limit": 1.5}, {"limit": "bad"}, {"limit": 1001},
    {"min_words": 0}, {"min_words": 4, "max_words": 3}, {"max_words": 11},
    {"min_length": 0}, {"min_df": 0}, {"min_occurrences": 0}, {"max_candidates": 5001},
    {"max_chars": 0}, {"max_occurrences": -1}, {"batch_size": 0}, {"revision": ""},
    {"cache_dir": True}, {"keybert_model": ""}, {"keybert_model": 42}, {"device": "tpu"},
    {"local_files_only": 1}, {"local_files_only": "yes"}, {"method": "both"},
    {"method": "mmr", "diversity": "NaN"}, {"method": "mmr", "diversity": 1.1},
    {"method": "mmr", "diversity": True}, {"method": "mmr", "diversity": []},
    {"diversity": 0.2}, {"nr_candidates": 10},
    {"method": "maxsum", "nr_candidates": 5, "limit": 6},
    {"stop_words": "french"}, {"stopwords": 1}, {"stopwords": [1]},
    {"llm": "openai"}, {"llm_model": "anything"}, {"features": "noun-chunks"},
])
def test_bad_configuration(options):
    with pytest.raises(ConfigurationError):
        kb._Settings.parse(options)


def test_model_revision_does_not_leak_to_custom_model():
    assert kb._Settings.parse({}).revision == kb.DEFAULT_REVISION
    cfg = kb._Settings.parse({"keybert_model": "other/model", "local_files_only": "true",
                              "stop_words": None, "stopwords": "Noise,noise"})
    assert cfg.revision is None and cfg.local_files_only
    assert cfg.stop_words == "none" and cfg.stopwords == {"noise"}
    assert kb._Settings.parse({"revision": "custom"}).revision == "custom"


@pytest.mark.parametrize("options", [{}, {"text": ""}, {"text": True}, {"text": " \n"}])
def test_missing_input(options):
    with pytest.raises(InputError):
        kb._documents(PluginRequest(operation="extract", options=options), kb._Settings.parse({}))


def test_read_utf8_bom_preserve_crlf_and_deduplicate(tmp_path):
    path = tmp_path / "example.TXT"
    path.write_bytes(b"\xef\xbb\xbfPaper planes.\r\nBrighton lights.\r\n")
    docs = kb._documents(PluginRequest(operation="extract", inputs=(tmp_path, path)), kb._Settings.parse({}))
    assert docs == [(str(path.resolve()), "Paper planes.\r\nBrighton lights.\r\n")]


@pytest.mark.parametrize("kind", ["missing", "unsupported", "bad-encoding", "empty-directory"])
def test_bad_file(tmp_path, kind):
    path = tmp_path / ("data.png" if kind == "unsupported" else "data.txt")
    if kind in {"unsupported", "bad-encoding"}:
        path.write_bytes(b"\xff")
    if kind == "empty-directory":
        path = tmp_path
    with pytest.raises(InputError):
        kb._documents(PluginRequest(operation="extract", inputs=(path,)), kb._Settings.parse({}))


def test_whole_input_budget(tmp_path):
    path = tmp_path / "one.txt"
    path.write_text("abcd", encoding="utf-8")
    request = PluginRequest(operation="extract", inputs=(path,), options={"text": "abcd"})
    with pytest.raises(InputError, match="max_chars"):
        kb._documents(request, kb._Settings.parse({"max_chars": 6}))


def test_no_llm_and_invalid_operation_fail_early():
    with pytest.raises(ConfigurationError, match="operation"):
        kb.KeyBERTKeywordPlugin().run(PluginRequest(operation="generate"), ExecutionContext(llms=None))
    with pytest.raises(ConfigurationError, match="non-empty"):
        kb.KeyBERTKeywordPlugin().run(PluginRequest(operation="extract", keywords=("",)), ExecutionContext(llms=None))


def test_keybert_missing_optional_dependencies(monkeypatch):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"keybert", "sentence_transformers"}:
            raise ImportError("blocked for test")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ConfigurationError, match=r"keywordmoves\[keybert\]"):
        kb.KeyBERTKeywordPlugin()._load(kb._Settings.parse({}))


def test_explicit_encoder_backend_load_cache_and_failure(monkeypatch):
    calls = []
    backend_calls = []
    runtime_calls = []
    package = ModuleType("keybert")
    backend_module = ModuleType("keybert.backend")
    st = ModuleType("sentence_transformers")

    def encoder(name, **kwargs):
        calls.append((name, kwargs))
        if name == "fail":
            raise OSError("network/cache failed")
        return SimpleNamespace(model=name)

    def backend(model):
        backend_calls.append(model)
        return ("backend", model)

    def runtime(**kwargs):
        runtime_calls.append(kwargs)
        return SimpleNamespace()

    package.KeyBERT, backend_module.SentenceTransformerBackend = runtime, backend
    st.SentenceTransformer = encoder
    for name, module in [("keybert", package), ("keybert.backend", backend_module), ("sentence_transformers", st)]:
        monkeypatch.setitem(sys.modules, name, module)
    plugin = kb.KeyBERTKeywordPlugin()
    cfg = kb._Settings.parse({})
    first = plugin._load(cfg)
    assert plugin._load(cfg) == first and len(calls) == 1
    assert calls[0] == (kb.DEFAULT_MODEL, {
        "revision": kb.DEFAULT_REVISION, "cache_folder": None, "device": "cpu",
        "local_files_only": False, "trust_remote_code": False,
        "model_kwargs": {"use_safetensors": True},
    })
    assert runtime_calls[0]["model"][0] == "backend" and runtime_calls[0]["llm"] is None
    plugin._load(kb._Settings.parse({"local_files_only": True, "device": "auto", "cache_dir": "cache"}))
    assert len(calls) == 2 and calls[1][1]["local_files_only"]
    assert calls[1][1]["device"] is None and calls[1][1]["cache_folder"] == "cache"
    with pytest.raises(ConfigurationError, match="Cannot load"):
        plugin._load(kb._Settings.parse({"keybert_model": "fail"}))
    assert len(backend_calls) == 2


def test_plugin_discovery_without_importing_optional_libraries():
    source = """
import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in {'keybert', 'sentence_transformers', 'sklearn', 'numpy', 'torch', 'spacy', 'nltk', 'openai'}:
        raise AssertionError('optional import during discovery: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from keywordmoves.cli import main
raise SystemExit(main(['plugins', '--json']))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    result = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    plugins = json.loads(result.stdout)
    assert any(item["name"] == "keybert" and item["kind"] == "keyword" for item in plugins)


def test_cli_uses_existing_error_exit_code(capsys):
    assert main(["run", "keybert", "--operation", "extract", "--option", "method=bad"]) == 2
    assert "method" in capsys.readouterr().err


def test_oversized_inline_input_rejected():
    with pytest.raises(InputError, match="max_chars"):
        kb._documents(PluginRequest(operation="extract", options={"text": "abcdef"}),
                      kb._Settings.parse({"max_chars": 3}))


def test_missing_vectorizer_dependency(monkeypatch):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("blocked for test")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ConfigurationError, match=r"keywordmoves\[keybert\]"):
        kb.KeyBERTKeywordPlugin().run(
            PluginRequest(operation="extract", options={"text": "paper planes"}),
            ExecutionContext(llms=None),
        )
