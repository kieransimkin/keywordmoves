"""Reviewed imports, snapshot compatibility and removal of derived source content."""
from __future__ import annotations

import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import reddit_imports as im
from keywordmoves.online.reddit import RedditPlugin

OPTIONS = {"source": "synthetic", "scope": "fixed-sample", "observed_at": "2026-10-02", "limit": 1000}


def save(tmp_path, value, name="sample.json"):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def call(operation, paths=(), keywords=(), **options):
    return RedditPlugin().run(PluginRequest(operation, tuple(keywords), tuple(paths),
                                           {**OPTIONS, **options}), ExecutionContext(None))


def thing(identity="abc", kind="t3", **extra):
    return {"kind": kind, "data": {"id": identity, "title": "Paper planes", "body": "Midnight music", **extra}}


def listing(*rows, after=None):
    return {"kind": "Listing", "data": {"children": list(rows), "after": after}}


def observation(day="2026-10-01", **extra):
    return {"schema": im.OBS_SCHEMA, "source": "reviewed-synthetic", "scope": "same-selection",
            "observed_at": day, "country": "GB", "period": "7d",
            "observations": [{"phrase": "paper planes", "metrics": {"reported_mentions": 10}}], **extra}


def test_native_listing_comments_tree_and_more_are_flattened():
    data = [listing(thing()), listing(thing("x", "t1", replies=listing(thing("y", "t1"))),
                                     {"kind": "more", "data": {"children": ["z", "z"]}})]
    rows, more = im.flatten(data)
    assert [r["data"]["id"] for r in rows] == ["abc", "x", "y"]
    assert more == ["z"]


@pytest.mark.parametrize("payload", [None, 1, "bad", {"x": 1}, {"kind": "Listing", "data": {}},
                                     {"kind": "more", "data": {"children": [123]}}])
def test_unknown_tree_shapes_fail_not_empty(payload):
    with pytest.raises(InputError):
        im.flatten(payload)


def test_tree_traversal_and_count_bounds():
    with pytest.raises(InputError):
        im.flatten([thing(), thing("def")], maximum=1)
    payload = []
    for _ in range(30):
        payload = [payload]
    with pytest.raises(InputError):
        im.flatten(payload, maximum=1)


def test_native_and_canonical_file_imports(tmp_path):
    for data in [listing(thing()), [{"id": "abc", "kind": "t3", "title": "Paper planes"}],
                 {"records": [{"id": "abc", "kind": "t3", "title": "Paper planes"}]}]:
        path = save(tmp_path, data)
        result = call("import-posts", [path])
        assert any(k.phrase == "paper planes" for k in result.keywords)
        assert result.metadata["live_query_performed"] is False
    path = save(tmp_path, listing(thing(), thing("def", "t1")))
    comments = call("import-comments", [path])
    assert comments.metadata["record_ids"] == ["t1_def"]


def test_reimport_saved_analysis_requires_explicit_records(tmp_path):
    path = save(tmp_path, [thing()])
    result = call("import-posts", [path], include_records=True)
    later = call("import-posts", [save(tmp_path, result.to_dict(), "result.json")])
    assert later.metadata["record_ids"] == result.metadata["record_ids"]
    result = call("import-posts", [path])
    with pytest.raises(InputError, match="no records"):
        call("import-posts", [save(tmp_path, result.to_dict(), "result.json")])


def test_csv_explicit_mappings_and_missing_values(tmp_path):
    path = tmp_path / "input.csv"
    path.write_bytes(b'\xef\xbb\xbfID,Title,Score,Comments\r\nabc,Paper planes,-2,0\r\ndef,Paper planes,,\r\n')
    out = call("import-posts", [path], id_column="ID", title_column="Title", score_column="Score", num_comments_column="Comments")
    row = next(k for k in out.keywords if k.phrase == "paper planes")
    values = {e.metric: e.value for e in row.evidence}
    assert values["sample_post_score_observed"] == 1
    assert values["sample_post_score_median"] == -2
    assert values["sample_post_comments_median"] == 0
    with pytest.raises(ConfigurationError):
        call("import-posts", [path])
    with pytest.raises(InputError):
        call("import-posts", [path], id_column="Wrong", title_column="Title")
    with pytest.raises(ConfigurationError):
        call("import-posts", [path], id_column="ID", title_column="Title", delimiter="xx")


def test_bad_csv_row_is_not_silently_interpreted(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("id,title\na,words,extra\n")
    with pytest.raises(InputError):
        call("import-posts", [path], id_column="id", title_column="title")


@pytest.mark.parametrize("content", ['NaN', '{"n":Infinity}', '{broken', '"scalar"'])
def test_invalid_json_or_schema(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(InputError):
        call("import-posts", [path])


def test_file_errors_and_limits(tmp_path):
    with pytest.raises(InputError):
        im.read(tmp_path / "absent")
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff")
    with pytest.raises(InputError):
        im.read(path)
    path.write_text("1234")
    with pytest.raises(InputError):
        im.read(path, maximum=3)


def test_observations_scope_units_and_approximation(tmp_path):
    data = observation(observations=[
        {"phrase": "music", "metrics": {"reported_mentions": "2.5k"}, "approximate": True},
        {"phrase": "planes", "metrics": {"estimated_search_volume": 50, "reported_net_score": -2}},
        {"phrase": "unknown", "metrics": {"reported_mentions": None}, "availability": "unavailable"},
    ])
    result = call("import-observations", [save(tmp_path, data)])
    assert result.metadata["scope"] == "same-selection"
    assert result.keywords[0].evidence[0].value == "2.5k"
    assert result.keywords[1].evidence[0].unit == "searches_per_month"
    assert result.keywords[2].evidence[0].value is None
    assert result.keywords[0].score is None


@pytest.mark.parametrize("row", [
    {"phrase": "x", "metrics": {"invented_score": 1}},
    {"phrase": "x", "metrics": {"reported_mentions": "2.5k"}},
    {"phrase": "x", "metrics": {"reported_mentions": 0}, "availability": "unavailable"},
    {"phrase": "x", "metrics": {}, "availability": "banned"},
    {"phrase": "x"}, {"metrics": {}},
])
def test_observation_errors(tmp_path, row):
    with pytest.raises((InputError, ConfigurationError)):
        call("import-observations", [save(tmp_path, observation(observations=[row]))])


def test_html_reviewed_selectors_and_explicit_empty_marker(tmp_path):
    pytest.importorskip("bs4")
    path = tmp_path / "capture.html"
    path.write_text('<article><b class="id">abc</b><h2>Paper planes</h2><i>-2</i></article>')
    result = call("import-html", [path], row_selector="article", id_selector=".id", title_selector="h2", score_selector="i")
    assert next(k for k in result.keywords if k.phrase == "paper planes").evidence
    for opts in [{"row_selector": "missing"}, {"id_selector": "missing"}, {"row_selector": "[["}]:
        with pytest.raises(InputError):
            call("import-html", [path], **({"row_selector": "article", "id_selector": ".id", "title_selector": "h2"} | opts))
    path.write_text('<div class="empty">No rows</div>')
    result = call("import-html", [path], row_selector="article", id_selector=".id", title_selector="h2", empty_selector=".empty")
    assert not result.keywords


def test_community_import(tmp_path):
    data = {"records": [{"id": "abc", "kind": "t5", "display_name": "Music", "subscribers": 20}]}
    result = call("import-communities", [save(tmp_path, data)])
    assert result.keywords[0].phrase == "r/music"


def snapshots(tmp_path):
    first = call("import-observations", [save(tmp_path, observation())])
    newer = observation("2026-10-02", observations=[
        {"phrase": "paper planes", "metrics": {"reported_mentions": 15}},
        {"phrase": "new phrase", "metrics": {"reported_mentions": 0}}])
    second = call("import-observations", [save(tmp_path, newer)])
    return first.to_dict(), second.to_dict()


def test_compare_net_changes_missing_values_and_geography(tmp_path):
    first, second = snapshots(tmp_path)
    result = call("compare", [save(tmp_path, first, "one.json"), save(tmp_path, second, "two.json")])
    row = next(k for k in result.keywords if k.phrase == "paper planes")
    assert row.evidence[0].value == 5
    assert row.evidence[0].geography == "GB"
    assert row.metadata["percent_change"] == 50
    assert row.metadata["net_change_per_day"] == 5
    missing = next(k for k in result.keywords if k.phrase == "new phrase")
    assert missing.evidence[0].value is None
    assert missing.metadata["before_present"] is False
    assert missing.metadata["after"] == 0


@pytest.mark.parametrize("change", [
    {"source": "other"}, {"scope": "other"}, {"country": "US"}, {"period": "30d"},
    {"analysis_signature": "other"}, {"output_truncated": True}, {"observed_at": "2026-10-01"},
    {"platform": "Google"},
])
def test_compare_refuses_incompatible_results(tmp_path, change):
    first, second = snapshots(tmp_path)
    second["metadata"].update(change)
    with pytest.raises(InputError):
        call("compare", [save(tmp_path, first, "one.json"), save(tmp_path, second, "two.json")])


def test_compare_excludes_approximation_and_zero_baseline_percent(tmp_path):
    first, second = snapshots(tmp_path)
    first["keywords"][0]["evidence"][0]["value"] = 0
    second["keywords"][1]["metadata"]["approximate"] = True
    result = call("compare", [save(tmp_path, first, "one.json"), save(tmp_path, second, "two.json")])
    assert len(result.keywords) == 1
    assert result.keywords[0].metadata["percent_change"] is None


def test_redact_recomputes_keywords_and_does_not_overwrite(tmp_path):
    path = save(tmp_path, [thing(), thing("def", title="Unique secret phrase", body="")])
    analysis = call("import-posts", [path], include_records=True)
    result_path = save(tmp_path, analysis.to_dict(), "analysis.json")
    original = result_path.read_bytes()
    redacted = call("redact", [result_path], keywords=("t3_def",), include_records=True)
    assert redacted.metadata["record_ids"] == ["t3_abc"]
    assert not any("secret" in k.phrase for k in redacted.keywords)
    assert result_path.read_bytes() == original
    assert redacted.metadata["original_file_modified"] is False
    assert redacted.metadata["redacted_ids"] == ["t3_def"]


def test_local_reference_and_config_errors(tmp_path):
    out = call("analyse-text", text="Paper planes #UKGarage")
    assert out.metadata["known_status"] == "reference-only-not-verified-on-reddit"
    assert all(k.metadata["known_status"] == out.metadata["known_status"] for k in out.keywords)
    path = tmp_path / "lyrics.txt"
    path.write_text("Paper planes")
    assert call("analyse-text", [path]).keywords
    for op, options in [("unknown", {}), ("analyse-text", {}), ("analyse-text", {"text": True}),
                        ("analyse-text", {"text": "x", "llm": "openai"}),
                        ("analyse-text", {"text": "x", "unknown": "value"})]:
        with pytest.raises((InputError, ConfigurationError)):
            call(op, **options)
    with pytest.raises(ConfigurationError):
        call("compare", [])
    with pytest.raises(ConfigurationError):
        call("import-posts", [])


def test_cli_local_success_and_missing_access(capsys, monkeypatch):
    from keywordmoves.cli import main
    assert main(["run", "reddit", "--operation", "analyse-text", "--option", "text=Paper planes"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["plugin"] == "reddit" and data["metadata"]["live_query_performed"] is False
    monkeypatch.delenv("REDDIT_ACCESS_TOKEN", raising=False)
    assert main(["run", "reddit", "--operation", "search", "--keyword", "music"]) == 2
    assert "approval" in capsys.readouterr().err


def test_redaction_requires_records_and_preserves_saved_extraction_settings(tmp_path):
    path = save(tmp_path, [thing(), thing("def", title="Other words")])
    result = call("import-posts", [path], include_records=True, min_words=2)
    redacted = call("redact", [save(tmp_path, result.to_dict(), "result.json")], keywords=("t3_def",))
    assert redacted.metadata["analysis_options"]["min_words"] == 2
    assert not any(k.relationship == "reddit-keyword" and len(k.phrase.split()) == 1 for k in redacted.keywords)
    for payload in [{}, {"plugin": "reddit", "metadata": {}},
                    {"plugin": "reddit", "metadata": {"records": [1]}}]:
        with pytest.raises((InputError, ConfigurationError)):
            call("redact", [save(tmp_path, payload)], keywords=("t3_def",))


def test_synthetic_workflow_in_separate_directory_and_no_overwrite(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "docs" / "examples" / "reddit-workflow.py")]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    process = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert "seven synthetic outputs" in process.stdout
    files = sorted((tmp_path / "reddit-demo").glob("*.json"))
    assert len(files) == 7
    contents = {str(p): p.read_bytes() for p in files}
    data = json.loads((tmp_path / "reddit-demo" / "redacted-analysis.json").read_text())
    assert data["metadata"]["record_ids"] == ["t3_synthetica"]
    second = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert second.returncode != 0 and "already exists" in second.stderr
    assert contents == {str(p): p.read_bytes() for p in files}
