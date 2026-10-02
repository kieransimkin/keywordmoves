"""Offline import, CLI and input-isolation tests with synthetic observations."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import youtube_analysis as a
from keywordmoves.online import youtube_imports as imp
from keywordmoves.online.youtube import YouTubePlugin

VID = "AAAAAaaaa01"
BASE = {"source": "Synthetic export", "scope": "fixture:same-collection", "observed_at": "2026-10-02"}


def saved(tmp_path, value, name="input.json"):
    p = tmp_path / name
    p.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")
    return p


def run(op, *paths, **options):
    return YouTubePlugin().run(PluginRequest(op, inputs=paths, options={**BASE, **options}), ExecutionContext(None))


def test_json_native_video_and_precise_counters(tmp_path):
    p = saved(tmp_path, {"items": [{"kind": "youtube#video", "id": VID, "snippet": {"title": "#Garage UK", "tags": ["garage music"]}, "statistics": {"viewCount": "9007199254740993"}}]})
    result = run("import-videos", p)
    assert result.metadata["api_data"] and not result.metadata["derived_statistics_enabled"]
    assert result.metadata["request_count"] == 0
    row = next(k for k in result.keywords if k.phrase == "#garage")
    assert row.metadata["supporting_records"][0]["counters"]["views"] == 9007199254740993


def test_csv_explicit_columns_preserve_zero_missing_and_tags(tmp_path):
    p = saved(tmp_path, f'ID,Heading,Views,Likes,Tags\n{VID},"#Garage session",1000,0,"garage music|Brighton"\n', "sample.csv")
    r = run("import-videos", p, id_column="ID", title_column="Heading", views_column="Views", likes_column="Likes", tags_column="Tags")
    h = next(i for i in r.keywords if i.phrase == "#garage")
    vals = {e.metric: e.value for e in h.evidence}
    assert vals["sample_likes_sum"] == 0 and vals["sample_comments_sum"] is None
    assert next(i for i in r.keywords if i.phrase == "garage music").relationship == "youtube-video-tag"


def test_semicolon_csv_observation_compact_display_and_defaults(tmp_path):
    p = saved(tmp_path, 'Tag;Reported\n#Garage;2.5M\n', "counts.csv")
    r = run("import-observations", p, delimiter=";", phrase_column="Tag", value_column="Reported", metric="reported_video_count", unit="videos", country="GB")
    assert r.keywords[0].evidence[0].value == 2500000
    assert r.keywords[0].metadata["approximate"] is True
    assert r.keywords[0].metadata["scope"] == BASE["scope"]


def test_nested_path_and_bad_path(tmp_path):
    p = saved(tmp_path, {"export": {"clips": [{"id": VID, "title": "Music"}]}})
    assert run("import-videos", p, records_path="export.clips").keywords
    with pytest.raises(InputError, match="records_path"):
        run("import-videos", p, records_path="data.items")


@pytest.mark.parametrize("content", ["NaN", "Infinity", '{"items":[NaN]}', '{', 'true', '{}', '[3]'])
def test_json_invalid_shapes_and_nonfinite(tmp_path, content):
    with pytest.raises(InputError):
        run("import-videos", saved(tmp_path, content))


@pytest.mark.parametrize("content", ['a,a\nx,y\n', 'a,b\nx\n', 'a,b\nx,y,z\n', ''])
def test_malformed_csv(tmp_path, content):
    with pytest.raises(InputError):
        run("import-videos", saved(tmp_path, content, "bad.csv"))


@pytest.mark.parametrize("options", [{"id_column": "Missing"}, {"tags_column": "Missing"}, {"delimiter": "||"}])
def test_invalid_csv_mappings(tmp_path, options):
    with pytest.raises((ConfigurationError, InputError)):
        run("import-videos", saved(tmp_path, f'id,title\n{VID},Music\n', "x.csv"), **options)


def test_bom_crlf_and_bad_file_encoding_or_size(tmp_path):
    p = tmp_path / "text.txt"
    p.write_bytes(b'\xef\xbb\xbfPaper\r\nplanes #garage')
    assert imp.read(p, {}).startswith("Paper\r\n")
    with pytest.raises(InputError, match="max_input_bytes"):
        imp.read(p, {"max_input_bytes": 4})
    p.write_bytes(b'\xff')
    with pytest.raises(InputError, match="UTF-8"):
        imp.read(p, {})
    with pytest.raises(InputError):
        imp.read(tmp_path / "missing", {})


def test_apify_native_comments_map_ids_votes_without_parent_title_pollution(tmp_path):
    p = saved(tmp_path, [{"cid": "Ugx1", "comment": "Love the #Garage sound", "videoId": VID,
                         "voteCount": 2, "replyToCid": None, "title": "#NotInTheComment", "author": "NOT-RETAINED"}])
    r = run("import-comments", p)
    assert any(k.phrase == "#garage" for k in r.keywords)
    assert not any(k.phrase == "#notinthecomment" for k in r.keywords)
    assert "NOT-RETAINED" not in json.dumps(r.to_dict())
    support = r.keywords[0].metadata["supporting_records"][0]
    assert support["counters"]["likes"] == 2 and support["video_id"] == VID


def test_native_comment_stats_and_exact_text_offsets(tmp_path):
    text = "R&B #Garage"
    p = saved(tmp_path, [{"kind": "youtube#comment", "id": "Ug1", "snippet": {"textOriginal": text, "likeCount": 3}}])
    r = run("import-comments", p)
    h = next(k for k in r.keywords if k.phrase == "#garage")
    span = h.metadata["occurrences"][0]
    assert text[span["start"]:span["end"]] == "#Garage"
    assert h.metadata["supporting_records"][0]["counters"]["likes"] == 3


@pytest.mark.parametrize("fmt,body", [
    ("vtt", 'WEBVTT\n\n00:01.000 --> 00:02.000\n<c.en>Paper #Garage</c>\n\n00:02.000 --> 00:03.000\nplanes flying\n'),
    ("srt", '1\n00:00:01,000 --> 00:00:02,000\nPaper #Garage\n\n2\n00:00:02,000 --> 00:00:03,000\nplanes flying\n'),
    ("txt", 'Paper #Garage\nplanes flying\n'),
])
def test_transcript_import_preserves_cue_boundaries(tmp_path, fmt, body):
    p = saved(tmp_path, body, "subtitles." + fmt)
    r = run("import-transcript", p)
    assert len(r.metadata["transcript_cues"]) == 2
    assert any(i.phrase == "#garage" for i in r.keywords)
    assert not any(i.phrase == "garage planes" or i.phrase == "paper planes" for i in r.keywords)
    if fmt != "txt":
        assert r.metadata["transcript_cues"][0]["start_seconds"] == 1


@pytest.mark.parametrize("text,fmt", [("not subtitles", "srt"), ("", "xml"), ("00:70.000 --> 00:71.000\nword", "vtt"), ("00:02.000 --> 00:01.000\nword", "vtt")])
def test_bad_transcripts_fail(text, fmt):
    with pytest.raises((InputError, ConfigurationError)):
        imp.transcript(text, "test", fmt)


def test_empty_transcript_and_exact_duplicate_cues():
    assert imp.transcript("", "test", "vtt") == []
    cue = "00:01.000 --> 00:02.000\n#Garage\n\n"
    assert len(imp.transcript(cue + cue, "test", "vtt")) == 1


def test_html_explicit_selectors_and_no_implicit_zero(tmp_path):
    p = saved(tmp_path, '<article><b>#Garage</b><i>2.5M</i></article>', "page.html")
    r = run("import-html", p, item_selector="article", phrase_selector="b", value_selector="i", metric="reported_video_count", unit="videos")
    assert r.keywords[0].metadata["approximate"]
    empty = saved(tmp_path, '<p class="none">No results</p>', "empty.html")
    with pytest.raises(InputError, match="No result"):
        run("import-html", empty, item_selector="article")
    assert not run("import-html", empty, item_selector="article", empty_selector=".none").keywords


@pytest.mark.parametrize("html,options", [
    ('<title>Sign in</title>', {"item_selector": "div"}),
    ('<title>CAPTCHA</title>', {"item_selector": "div"}),
    ('<article>hi</article>', {"item_selector": "[!"}),
    ('<article>hi</article>', {"item_selector": "article", "phrase_selector": "b"}),
    ('<article>#garage</article>', {"item_selector": "article", "value_selector": "b"}),
])
def test_html_challenges_and_missing_structure(tmp_path, html, options):
    with pytest.raises(InputError):
        run("import-html", saved(tmp_path, html, "page.html"), **options)


def test_missing_bs4_is_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, "bs4", None)
    with pytest.raises(ConfigurationError, match="online"):
        imp.soup("<p>sample</p>")


def test_local_extract_accepts_inline_and_multiple_files(tmp_path):
    left = saved(tmp_path, "Paper #Garage\nplanes", "first.txt")
    right = saved(tmp_path, "#Brighton music", "second.txt")
    r = run("extract", left, right, text="#UKMusic at night")
    assert r.metadata["unique_records"] == 3 and not r.metadata["live_query_performed"]
    assert {"#garage", "#brighton", "#ukmusic"} <= {i.phrase for i in r.keywords}
    assert "planes brighton" not in {i.phrase for i in r.keywords}


@pytest.mark.parametrize("operation,options", [
    ("extract", {}), ("extract", {"text": True}), ("missing", {}),
    ("search", {"llm": "openai"}), ("extract", {"text": "hi", "llm_model": "any"}),
    ("import-videos", {}), ("compare", {}),
])
def test_invalid_operation_input_and_llm_options(operation, options):
    with pytest.raises((ConfigurationError, InputError)):
        YouTubePlugin().run(PluginRequest(operation, options=options), ExecutionContext(None))


def test_import_requires_provenance_and_local_rejects_seeds(tmp_path):
    p = saved(tmp_path, [{"id": VID, "title": "#Garage"}])
    for missing in BASE:
        with pytest.raises(ConfigurationError):
            YouTubePlugin().run(PluginRequest("import-videos", inputs=(p,), options={k: v for k, v in BASE.items() if k != missing}), ExecutionContext(None))
    with pytest.raises(ConfigurationError):
        YouTubePlugin().run(PluginRequest("extract", keywords=("garage",)), ExecutionContext(None))


def test_saved_snapshot_comparison_real_json(tmp_path):
    row = {"phrase": "#Garage", "metric": "reported_video_count", "unit": "videos", "value": 100}
    p = saved(tmp_path, [row])
    before = run("import-observations", p)
    p.write_text(json.dumps([{**row, "value": 130}]), encoding="utf-8")
    after = run("import-observations", p, observed_at="2026-10-04")
    b, c = saved(tmp_path, before.to_dict(), "before.json"), saved(tmp_path, after.to_dict(), "after.json")
    r = run("compare", b, c)
    values = {e.metric: e.value for e in r.keywords[0].evidence}
    assert values["reported_video_count_net_change"] == 30
    assert values["reported_video_count_net_change_per_day"] == 15
    assert values["reported_video_count_percent_change"] == 30


@pytest.mark.parametrize("bad", [None, 1, [], {}, {"metadata": None}, {"metadata": {"schema": a.SCHEMA}, "keywords": "x"}, {"metadata": {"schema": a.SCHEMA}, "keywords": [{"metadata": []}]}])
def test_bad_snapshot_is_clean_error(tmp_path, bad):
    b = saved(tmp_path, bad, "b.json")
    with pytest.raises(InputError):
        run("compare", b, b)


def test_trends_requires_youtube_property_attestation_and_clears_score():
    p = Path(__file__).parent / "fixtures" / "trends_interest.csv"
    r = run("trends-import", p, search_property="youtube")
    assert r.metadata["search_property"] == "youtube"
    assert all(i.score is None and i.metadata["search_property"] == "youtube" for i in r.keywords)
    with pytest.raises(ConfigurationError):
        run("trends-import", p, search_property="web")


def test_cli_and_no_optional_imports(capsys):
    assert main(["run", "youtube", "--operation", "extract", "--option", "text=#Garage music"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metadata"]["platform"] == "YouTube"
    assert main(["run", "youtube", "--operation", "extract", "--option", "max_words=0"]) == 2
    assert "max_words" in capsys.readouterr().err
    root = Path(__file__).resolve().parents[1]
    code = '''import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'httpx','bs4','spacy','nltk','keybert','sentence_transformers','openai','torch','transformers'}:
        raise ImportError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from keywordmoves.cli import main
raise SystemExit(main(['plugins','--json']))
'''
    r = subprocess.run([sys.executable, "-c", code], cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, check=True)
    assert "youtube" in {i["name"] for i in json.loads(r.stdout)}


@pytest.mark.parametrize("option,value", [("limit", 0), ("include_keywords", "yes"), ("max_words", 9), ("stopwords", []), ("pages", 0), ("include_replies", "perhaps"), ("max_occurrences", -1)])
def test_analysis_settings_fail_before_network(option, value):
    httpx = pytest.importorskip("httpx")
    def no_request(_):
        pytest.fail("An invalid analysis setting must not spend provider credits.")
    plugin = YouTubePlugin(transport=httpx.MockTransport(no_request))
    with pytest.raises(ConfigurationError):
        plugin.run(PluginRequest("apify-start", keywords=("music",), options={"allow_paid": True, "max_charge_usd": 1, "apify_token": "secret", option: value}), ExecutionContext(None))


@pytest.mark.parametrize("unit,raw,expected", [("percent", "4.1%", 4.1), ("ratio", "0.025", 0.025), ("minutes", "123.5", 123.5), ("seconds", 23.1, 23.1), ("ordinal", "3", 3)])
def test_numeric_observation_units_keep_numeric_values(unit, raw, expected):
    values = a.observations([{"phrase": "garage", "metric": "native_report_metric", "unit": unit, "value": raw}], source="synthetic", scope="test", observed_at="2026-10-02")
    assert values[0].evidence[0].value == expected
