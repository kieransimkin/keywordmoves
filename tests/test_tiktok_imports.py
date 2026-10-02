"""Local TikTok import, provenance, CLI and schema-drift regression tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.tiktok import TikTokPlugin
from keywordmoves.online.tiktok_analysis import compare, normalise_video, observations
from keywordmoves.online.tiktok_imports import (
    challenge_page,
    decode_json,
    html_records,
    load_records,
    read,
    reference_records,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tiktok"
PROVENANCE = {"source": "Synthetic fixture", "scope": "same-collection:GB:7", "observed_at": "2026-10-01"}


def run(op, inputs=(), keywords=(), **options):
    return TikTokPlugin().run(PluginRequest(op, inputs=inputs, keywords=keywords, options=options), ExecutionContext(None))


def evidence(item):
    return {v.metric: v.value for v in item.evidence}


def test_local_video_fixture_and_known_not_observed():
    result = run("import-videos", (FIXTURES / "videos.json",), keywords=("notobserved",), include_keywords=True, **PROVENANCE)
    assert result.metadata["unique_records"] == 3 and result.metadata["duplicate_records"] == 1
    assert result.metadata["live_query_performed"] is False
    items = {v.phrase: v for v in result.keywords}
    assert evidence(items["#ukgarage"])["sample_video_views_sum"] == 3000
    assert evidence(items["#ukgarage"])["sample_interactions_per_view"] == pytest.approx(240 / 3000)
    assert evidence(items["#notobserved"])["sampled_video_count"] == 0
    assert "reported_post_count" not in evidence(items["#notobserved"])


def test_local_hashtag_fixture_does_not_conflate_all_time_and_period():
    result = run("import-hashtags", (FIXTURES / "hashtags-before.json",), **PROVENANCE)
    assert evidence(result.keywords[0])["reported_post_count"] == 1000
    assert evidence(result.keywords[0])["period_post_count"] == 20
    assert result.keywords[0].metadata["native_dimensions"]["audienceInterests"][0]["score"] == 142
    assert result.keywords[1].metadata["approximate_metrics"] == ["reported_post_count"]


def test_csv_explicit_column_mapping_for_search_insights():
    mapping = {"phrase": "Topic", "metric": "Measurement", "value": "Amount", "unit": "Units", "country": "Region", "window": "Window"}
    result = run("import-observations", (FIXTURES / "search-insights.csv",), column_map=json.dumps(mapping), **PROVENANCE)
    assert evidence(result.keywords[0])["search_popularity_index"] == 72
    assert result.keywords[0].evidence[0].unit == "index_0_100"
    assert "reported_view_count" not in evidence(result.keywords[0])
    assert evidence(result.keywords[1])["content_gap_indicator"] == "available"


def test_json_official_videos_comments_and_explicit_records_path(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"error": {"code": "ok"}, "data": {"comments": [{"id": 1, "video_id": 7, "text": "#music", "like_count": 3}]}}))
    result = run("import-comments", (path,), **PROVENANCE)
    assert evidence(result.keywords[0])["sampled_comment_count"] == 1
    assert not any("video" in e.metric for e in result.keywords[0].evidence)
    path.write_text(json.dumps({"deep": {"list": [{"caption": "#music"}]}}))
    assert load_records((path,), {"records_path": "deep.list"}, "videos") == [{"caption": "#music"}]


def test_csv_list_cells_and_empty_metrics(tmp_path):
    path = tmp_path / "posts.csv"
    path.write_text('id,caption,hashtags,view_count,isSlideshow\n1,hello,"[""music""]",,true\n')
    result = run("import-videos", (path,), **PROVENANCE)
    item = result.keywords[0]
    assert item.phrase == "#music" and evidence(item)["sample_video_views_sum"] is None
    assert item.metadata["formats"] == {"slideshow": 1}


@pytest.mark.parametrize("body", ["a,a\n1,2\n", "a,b\n1\n", "a\n1,2\n", ""])
def test_invalid_csv_is_not_empty_success(tmp_path, body):
    path = tmp_path / "bad.csv"
    path.write_text(body)
    with pytest.raises(InputError):
        load_records((path,), {}, "videos")


@pytest.mark.parametrize("body", ["NaN", "Infinity", '{"a":NaN}', "not JSON", "<html>Login</html>"])
def test_invalid_finite_json(body):
    with pytest.raises(InputError):
        decode_json(body)


@pytest.mark.parametrize("payload", [{"schema": "wrong", "videos": []}, {"error": {"code": "access_denied"}, "data": {"videos": []}}, {"data": {"other": []}}, {"videos": [1]}, {"videos": {}}])
def test_wrong_schema_error_and_wrong_shape_are_explicit(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(InputError):
        load_records((path,), {}, "videos")


def test_input_limit_and_read_errors(tmp_path):
    path = tmp_path / "data.json"
    path.write_bytes(b"x" * 1025)
    with pytest.raises(InputError, match="max_input_bytes"):
        read(path, 1024)
    path.write_bytes(b"\xff")
    with pytest.raises(InputError, match="UTF-8"):
        read(path)
    with pytest.raises(InputError):
        read(tmp_path / "missing")
    with pytest.raises(InputError):
        load_records((), {}, "videos")
    path.write_text('[{"caption":"#a"},{"caption":"#b"}]')
    with pytest.raises(InputError):
        load_records((path,), {"max_records": 1}, "videos")
    assert len(load_records((path, path), {}, "videos")) == 2


@pytest.mark.parametrize("mapping", [{}, [], {"phrase": None}, {"phrase": 1}, {"": "name"}, {"name": ""}])
def test_invalid_mapping(tmp_path, mapping):
    path = tmp_path / "data.json"
    path.write_text('[{"name":"music"}]')
    with pytest.raises(ConfigurationError):
        load_records((path,), {"column_map": mapping}, "videos")


def test_missing_mapped_column_and_missing_records_path(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('[{"name":"music"}]')
    for options in ({"column_map": {"caption": "absent"}}, {"records_path": "data.items"}):
        with pytest.raises(InputError):
            load_records((path,), options, "videos")


def test_reference_bom_lines_no_dependency_and_deduplication(tmp_path):
    p = tmp_path / "one.txt"
    p.write_bytes(b"\xef\xbb\xbf#music\r\n#ukg")
    (tmp_path / "ignored.pdf").write_bytes(b"ignored")
    rows = reference_records((tmp_path, p), {"text": "#brighton"})
    assert len(rows) == 2 and rows[0]["caption"] == "#music\r\n#ukg"
    result = run("extract", (p,), text="#brighton", include_keywords=False)
    assert {i.phrase for i in result.keywords} == {"#music", "#ukg", "#brighton"}
    assert all(i.metadata["verification"] == "observed-in-text" for i in result.keywords)


@pytest.mark.parametrize("options", [{}, {"text": ""}, {"text": " \n"}, {"text": True}])
def test_missing_reference(options):
    with pytest.raises(InputError):
        reference_records((), options)


def test_reference_and_record_suffix_limits(tmp_path):
    p = tmp_path / "data.exe"
    p.write_text("#music")
    with pytest.raises(InputError):
        reference_records((p,), {})
    with pytest.raises(InputError):
        load_records((p,), {}, "videos")
    with pytest.raises(InputError):
        reference_records((), {"text": "x" * 2000, "max_input_bytes": 1024})
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for path in (a, b):
        path.write_text(json.dumps([{"caption": "x" * 600}]))
    with pytest.raises(InputError, match="Combined"):
        load_records((a, b), {"max_input_bytes": 1024}, "videos")


def test_saved_html_selectors_actual_parser():
    pytest.importorskip("bs4")
    fields = {"hashtag": "h2", "reported_post_count": ".posts", "country": ".country", "period": ".period"}
    result = run("import-html", (FIXTURES / "rendered.html",), record_selector="article.tag-result", field_selectors=fields, **PROVENANCE)
    assert len(result.keywords) == 2
    assert evidence(result.keywords[0])["reported_post_count"] == 1035
    assert result.keywords[1].metadata["approximate_metrics"] == ["reported_post_count"]
    assert result.metadata["access"] == "local"


def test_html_no_match_missing_field_limit_and_empty_marker():
    pytest.importorskip("bs4")
    options = {"record_selector": "article", "field_selectors": {"hashtag": "h2"}}
    for body in ("<body>Please log in</body>", "<article><h3>#a</h3></article>"):
        with pytest.raises(InputError):
            html_records(body, options)
    assert html_records('<p class="none">No results for this query</p>', {**options, "empty_selector": ".none"}) == []
    with pytest.raises(InputError):
        html_records("<article><h2>#a</h2></article>" * 2, {**options, "max_records": 1})
    with pytest.raises(ConfigurationError):
        html_records("<article></article>", {**options, "record_selector": "["})
    assert html_records("<article>#a</article>", {**options, "field_selectors": {"hashtag": ":self"}}) == [{"hashtag": "#a"}]


def test_missing_bs4_is_clean_configuration_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "bs4", None)
    with pytest.raises(ConfigurationError, match="online"):
        html_records("", {"record_selector": "a", "field_selectors": {"phrase": "a"}})


def test_legacy_page_counts_and_unrelated_invalid_names():
    pytest.importorskip("bs4")
    body = '<script id="SIGI_STATE">' + json.dumps({"ChallengeModule": {
        "a": {"title": "invalid name", "stats": {"viewCount": 1}},
        "b": {"id": "123", "title": "Music", "videoCount": 0, "viewCount": 3}
    }}) + '</script>'
    rows = challenge_page(body, "#music")
    assert rows == [{"hashtag": "#music", "hashtag_id": "123", "reported_post_count": 0, "reported_view_count": 3}]


@pytest.mark.parametrize("body", ["<html>Login</html>", '<script id="SIGI_STATE">{}</script>', '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">[]</script>', '<script id="SIGI_STATE">{"ChallengeModule":{"a":{"title":"other","viewCount":123}}}</script>', '<script id="SIGI_STATE">{"ChallengeModule":{"a":{"title":"music","viewCount":null}}}</script>'])
def test_unknown_page_shape_never_returns_zero(body):
    pytest.importorskip("bs4")
    with pytest.raises(InputError):
        challenge_page(body, "music")


def test_unavailable_counts_and_empty_provenance_are_rejected():
    for row in ({"hashtag": "music", "availability": "unavailable", "reported_post_count": 0},
                {"hashtag": "music", "reported_post_count": 1, "source": ""},
                {"hashtag": "music", "reported_post_count": 1, "scope": ""}):
        with pytest.raises(InputError):
            observations([row], "test", "2026-10-01", "scope", hashtags_only=True)


def test_duplicate_zero_duration_is_not_overwritten_and_sound_name_is_retained():
    from keywordmoves.online.tiktok_analysis import analyse
    items, meta = analyse([{"id": "1", "caption": "#music", "duration": 0, "musicMeta": {"musicId": "9", "musicName": "Synthetic Song"}},
                           {"id": "1", "caption": "#music", "duration": 2}], {}, "test", "2026-10-01")
    assert meta["conflicting_duplicate_fields"] == 1
    assert items[0].metadata["sounds"][0]["names"] == ["Synthetic Song"]
    assert normalise_video({"isSlideshow": "false"})["format"] == "video"


def snapshots():
    return [run("import-hashtags", (FIXTURES / filename,), **{**PROVENANCE, "observed_at": when}).to_dict()
            for filename, when in (("hashtags-before.json", "2026-10-01"), ("hashtags-after.json", "2026-10-02"))]


def test_compare_actual_saved_json_and_cli(tmp_path, capsys):
    before, after = snapshots()
    paths = [tmp_path / "before.json", tmp_path / "after.json"]
    for path, data in zip(paths, (before, after)):
        path.write_text(json.dumps(data))
    assert main(["run", "tiktok", "--operation", "compare", "--input", str(paths[0]), "--input", str(paths[1])]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["keywords"]) == 2
    assert {i["phrase"] for i in output["keywords"]} == {"#ukgarage"}
    assert output["keywords"][0]["evidence"][0]["value"] == 35
    assert main(["run", "tiktok", "--operation", "extract", "--format", "text", "--option", "text=#Music"]) == 0
    assert "#music" in capsys.readouterr().out


@pytest.mark.parametrize("patch", [{"metadata": []}, {"keywords": None}, {"keywords": [None]}, {"keywords": [{"metadata": []}]}, {"keywords": [{"metadata": {"platform": "TikTok"}, "evidence": [None]}]}, {"keywords": [{"metadata": {"platform": "TikTok", "approximate_metrics": True}}]}])
def test_malformed_snapshots_fail_cleanly(patch):
    before, after = snapshots()
    before.update(patch)
    with pytest.raises(InputError):
        compare(before, after)


@pytest.mark.parametrize("field,value", [("source", None), ("unit", {}), ("observed_at", None), ("geography", [])])
def test_malformed_snapshot_measurement_metadata(field, value):
    before, after = snapshots()
    before = deepcopy(before)
    before["keywords"][0]["evidence"][0][field] = value
    with pytest.raises(InputError):
        compare(before, after)


def test_cli_errors_are_clean_and_no_llm_selection(capsys):
    for args in (["--option", "limit=0"], ["--llm", "openai"], ["--model", "some-model"]):
        assert main(["run", "tiktok", "--operation", "extract", *args]) == 2
        assert "Traceback" not in capsys.readouterr().err


def test_tiktok_registration_without_all_optional_dependencies():
    root = Path(__file__).resolve().parents[1]
    code = '''import sys
sys.modules.update({name: None for name in ("httpx", "bs4", "nltk", "spacy", "keybert", "torch", "openai", "transformers", "sentence_transformers")})
from keywordmoves.cli import main
raise SystemExit(main(["plugins", "--json"]))
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=root,
                            env={**os.environ, "PYTHONPATH": str(root / "src")}, text=True, capture_output=True, check=True)
    names = {p["name"] for p in json.loads(result.stdout)}
    assert {"tiktok", "instagram", "keybert", "nltk", "spacy", "openai"} <= names
