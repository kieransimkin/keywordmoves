"""Input/CLI tests plus explicit third-party keyword discovery contracts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError, KeywordMovesError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.instagram import InstagramPlugin
from keywordmoves.online.instagram_imports import (
    json_input,
    public_hashtag_html,
    read_input,
    records,
    saved_html,
)


def run(op, inputs=(), **options):
    return InstagramPlugin().run(PluginRequest(op, inputs=tuple(inputs), options=options), ExecutionContext(None))


def val(item):
    return {e.metric:e.value for e in item.evidence}


def file(tmp_path, payload, name="data.json"):
    path=tmp_path/name
    path.write_text(json.dumps(payload),encoding="utf-8")
    return path


@pytest.mark.parametrize("options", [{"limit":0},{"limit":True},{"related_limit":-1},{"include_keywords":"yes"},
    {"include_media":2},{"llm":"openai"},{"sort_by":"popularity"},{"unknown":True}])
def test_bad_options_are_clear(options):
    with pytest.raises(ConfigurationError):
        run("extract",text="#music",**options)


@pytest.mark.parametrize("operation", ["post","login","scrape-private","generate"])
def test_unsupported_operations_fail(operation):
    with pytest.raises(ConfigurationError):
        run(operation)


def test_lazy_registry_even_when_all_optional_modules_blocked():
    root=Path(__file__).resolve().parents[1]
    code='''
import builtins
real_import = builtins.__import__
def blocked(name,*args,**kwargs):
    if name.split('.')[0] in {'httpx','bs4','nltk','spacy','openai','torch','transformers','keybert','sentence_transformers'}:
        raise ImportError(name)
    return real_import(name,*args,**kwargs)
builtins.__import__ = blocked
from keywordmoves.cli import main
raise SystemExit(main(['plugins','--json']))
'''
    result=subprocess.run([sys.executable,"-c",code],cwd=root,
        env={**os.environ,"PYTHONPATH":str(root/"src")},capture_output=True,text=True,check=True)
    items=json.loads(result.stdout)
    item=next(v for v in items if v["name"]=="instagram")
    assert item["kind"]=="keyword"
    assert "hashtag" in item["operations"] and "apify-fetch" in item["operations"]


def test_cli_reference_extraction_no_api_or_llm(capsys):
    assert main(["run","instagram","--operation","extract","--option","text=#Music #UKG #Music"])==0
    result=json.loads(capsys.readouterr().out)
    assert result["plugin"]=="instagram"
    assert result["metadata"]["live_query_performed"] is False
    assert result["metadata"]["request_count"]==0
    assert len(result["keywords"])==2
    assert result["keywords"][0]["score"] is None


def test_missing_dependency_does_not_break_local_input(monkeypatch):
    monkeypatch.setitem(sys.modules,"httpx",None)
    assert len(run("extract",text="#music").keywords)==1
    with pytest.raises(ConfigurationError,match="online"):
        run("quota",access_token="key",user_id="1",graph_version="v26.0")


@pytest.mark.parametrize("payload", ["NaN", "Infinity", '{"x":NaN}', "<html>Login</html>"])
def test_strict_json_input(payload):
    with pytest.raises(InputError):
        json_input(payload)


@pytest.mark.parametrize("payload", [{"error":"login"},{"errors":[1]},{"schema":"old"},[None],
    {"posts":[{"unknown":True}]}])
def test_error_exports_not_empty_success(payload):
    with pytest.raises(KeywordMovesError):
        records(payload,10,"media")


def test_canonical_media_json_metrics_and_no_secret_storage(tmp_path):
    path=file(tmp_path,{"schema":"keywordmoves-instagram/v1","posts":[
        {"id":"1","caption":"Paper planes #Music #UKG","likes":0,"comments_count":None,
         "access_token":"must-not-copy","ownerUsername":"not-needed"},
        {"id":"2","caption":"#Music","views":500,"reach":100},
    ]})
    result=run("import-media",[path],observed_at="2026-10-02",source="Test export",scope="owned-profile")
    item=next(i for i in result.keywords if i.phrase=="#music")
    assert val(item)["sampled_post_count"]==2
    assert val(item)["sample_likes_mean"]==0
    assert val(item)["sample_reach_mean"]==100
    output=json.dumps(result.to_dict())
    assert "must-not-copy" not in output and "not-needed" not in output
    assert "media" not in result.metadata


def test_multiple_input_files_do_not_join_or_double_import(tmp_path):
    path=file(tmp_path,[{"caption":"#music","id":"1"}])
    second=file(tmp_path,[{"caption":"#ukg","id":"2"}],"more.json")
    result=run("import-media",[path,path,second],observed_at="2026-10-02",source="fixture",scope="batch")
    assert result.metadata["sample_size"]==2


def test_media_csv_uses_canonical_fields(tmp_path):
    path=tmp_path/"data.csv"
    path.write_text('id,caption,likes,comments_count\n1,"Music #ukg",0,\n2,"#ukg",10,2\n',encoding="utf-8")
    result=run("import-media",[path],observed_at="2026-10-02",source="fixture",scope="csv")
    assert val(result.keywords[0])["sample_likes_mean"]==5
    assert val(result.keywords[0])["comments_observed_posts"]==1


def test_hashtag_csv_mapped_columns_preserve_unavailable_and_rounded(tmp_path):
    path=tmp_path/"data.csv"
    path.write_text('Tag,Posts,availability\n#music,1.2M,observed\n#ukg,,unavailable\n',encoding="utf-8")
    result=run("import-hashtags",[path],observed_at="2026-10-02",source="fixture",scope="browser",
               hashtag_column="Tag",post_count_column="Posts")
    assert val(result.keywords[0])["reported_post_count"]==1200000
    assert result.keywords[0].metadata["count_is_approximate"]
    assert val(result.keywords[1])["reported_post_count"] is None
    assert result.keywords[1].metadata["availability"]=="unavailable"


def test_native_saved_search_json_is_imported_without_private_requests(tmp_path):
    path=file(tmp_path,{"hashtags":[{"position":0,"hashtag":{"id":"111","name":"music","media_count":1000}}]})
    result=run("import-hashtags",[path],observed_at="2026-10-02",source="Reviewed native search",scope="signed-in-GB")
    assert val(result.keywords[0])["reported_post_count"]==1000
    assert result.metadata["live_query_performed"] is False


def test_sorting_unknown_values_last_without_fabricated_score(tmp_path):
    path=file(tmp_path,[{"name":"unknown"},{"name":"small","post_count":2},{"name":"large","post_count":100}])
    result=run("import-hashtags",[path],observed_at="2026-10-02",source="fixture",scope="counts",sort_by="reported_post_count")
    assert [i.phrase for i in result.keywords]==["#large","#small","#unknown"]
    assert all(i.score is None for i in result.keywords)


def test_local_output_limit_is_reported(tmp_path):
    result=run("extract",text="#one #two",limit=1)
    assert len(result.keywords)==1
    assert result.metadata["rows_received"]==2 and result.metadata["output_truncated"]


def test_import_requires_observation_provenance(tmp_path):
    path=file(tmp_path,[{"name":"music"}])
    for opts in ({}, {"observed_at":"2026-10-02"}, {"observed_at":"2026-10-02","source":"fixture"}):
        with pytest.raises(KeywordMovesError):
            run("import-hashtags",[path],**opts)


def test_record_and_file_bounds_are_enforced(tmp_path):
    path=file(tmp_path,[{"caption":"#a"},{"caption":"#b"}])
    with pytest.raises(InputError,match="max_records"):
        run("import-media",[path],observed_at="2026-10-02",source="fixture",scope="x",max_records=1)
    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"x" * 1025)
    with pytest.raises(InputError,match="max_input_bytes"):
        read_input(huge,1024)
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff")
    with pytest.raises(InputError,match="UTF-8"):
        read_input(bad,1024)
    with pytest.raises(InputError,match="UTF-8"):
        read_input(tmp_path/"no-file",1024)


def test_literal_extract_empty_input_and_combined_limit():
    for opts in ({}, {"text":""}, {"text":True}, {"text":"x"*1025,"max_input_bytes":1024}):
        with pytest.raises(InputError):
            run("extract",**opts)


def test_saved_html_explicit_selectors_and_no_match_failure(tmp_path):
    pytest.importorskip("bs4")
    body='<div class="tag"><span class="name">#Music</span><span class="count">2.5K posts</span></div>'
    path = tmp_path / "saved.html"
    path.write_text(body, encoding="utf-8")
    result=run("import-html",[path],observed_at="2026-10-02",source="Fixture saved HTML",scope="search",
        selector=".tag",name_selector=".name",count_selector=".count")
    assert val(result.keywords[0])["reported_post_count"]==2500
    with pytest.raises(InputError,match="No configured"):
        saved_html("<html>login</html>",{"selector":".tag"})
    with pytest.raises(ConfigurationError):
        saved_html(body,{"selector":"["})


@pytest.mark.parametrize("body", ['<html>Login</html>',
    '<meta property="og:url" content="https://www.instagram.com/explore/tags/music/">',
    '<meta property="og:url" content="https://instagram.com.evil.test/explore/tags/music/"><meta property="og:description" content="12 posts">',
    '<meta property="og:url" content="https://www.instagram.com/explore/tags/other/"><meta property="og:description" content="12 posts">'])
def test_public_html_challenge_or_changed_markup_fails(body):
    pytest.importorskip("bs4")
    with pytest.raises(OnlineSourceError):
        public_hashtag_html(body,"#music")


def test_compare_saved_results_end_to_end(tmp_path):
    files=[]
    for date,n in (("2026-10-01",100),("2026-10-02",150)):
        source=file(tmp_path,[{"name":"music","post_count":n}])
        result=run("import-hashtags",[source],source="fixture",scope="same-surface",observed_at=date)
        files.append(file(tmp_path,result.to_dict(),date+".json"))
    result=run("compare",files)
    assert val(result.keywords[0])["reported_post_count_delta"]==50
    assert result.metadata["observed_at"]=="2026-10-02T00:00:00+00:00"


@pytest.mark.parametrize("operation", ["suggestions","metrics"])
def test_keywordtool_instagram_request_and_estimate_labels(operation,monkeypatch):
    httpx=pytest.importorskip("httpx")
    monkeypatch.setattr("keywordmoves.online.common.time.sleep",lambda _:None)
    def handler(req):
        body=json.loads(req.content)
        assert req.url.path.endswith("/"+("volume" if operation=="metrics" else "suggestions")+"/instagram")
        assert body["country"]=="GB" and body["language"]=="en"
        if operation=="suggestions":
            assert body["type"]=="hashtags"
        return httpx.Response(200,json={"results":{"music":{"string":"#music","volume":50,"cpc":.3,"cmp":None,"m1":50}}})
    result=InstagramPlugin(transport=httpx.MockTransport(handler)).run(
        PluginRequest(operation,keywords=("music",),options={"api_key":"test","country":"GB"}),ExecutionContext(None))
    assert result.plugin=="instagram" and result.metadata["live_query_performed"]
    assert result.keywords[0].metadata["verification"]==("third-party-suggestion" if operation=="suggestions" else "unverified-metric-query")
    assert "reported_post_count" not in val(result.keywords[0])
    if operation=="metrics":
        assert val(result.keywords[0])["estimated_search_volume"]==50
        assert result.keywords[0].metadata["monthly_metrics"]=={"m1":50}


def test_public_description_is_bounded_before_regex():
    pytest.importorskip("bs4")
    body = '<meta property="og:url" content="https://www.instagram.com/explore/tags/music/">'
    body += '<meta property="og:description" content="' + ('9' * 10000) + '">'
    with pytest.raises(OnlineSourceError, match="length"):
        public_hashtag_html(body, "#music")
