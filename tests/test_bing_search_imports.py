"""Offline interchange schemas, CLI integration and lazy optional dependencies."""
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
from keywordmoves.online.bing_search import BingSearchPlugin
from keywordmoves.online.bing_search_imports import read_json, read_text

ROOT = Path(__file__).parent / "fixtures" / "bing-search"


def run(op, paths, **o):
    return BingSearchPlugin().run(PluginRequest(op, inputs=tuple(paths), options=o), ExecutionContext(None))


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_canonical_import_all_artifact_types():
    for op, file in [("import-serp", "serp-before.json"), ("bwt-import", "bwt-before.json"),
                     ("import-observations", "observations-before.json")]:
        result = run(op, [ROOT / file])
        assert result.keywords and result.metadata["live_query_performed"] is False
        assert all(x.score is None for x in result.keywords)


def test_native_bwt_import_requires_original_query_page_context():
    result = run("bwt-import", [ROOT / "native-queries.json"], input_format="bwt-native",
                 source="Reviewed saved Bing Webmaster API", site_url="https://example.com/", observed_at="2026-10-02")
    assert result.metadata["report"]["context"]["granularity"] == "native-buckets"
    for kind, extra in [("query-history", {}), ("page-queries", {})]:
        with pytest.raises(ConfigurationError):
            run("bwt-import", [ROOT / "native-queries.json"], input_format="bwt-native", report_type=kind,
                source="Reviewed API", site_url="https://example.com/", observed_at="2026-10-02", **extra)


def test_csv_period_totals_and_ctr_units():
    o = dict(source="Reviewed Bing Webmaster export", scope="web:all-devices:all-regions",
             site_url="https://example.com/", observed_at="2026-10-02", start_date="2026-09-01",
             end_date="2026-09-30", period_totals=True, query_column="Query", page_column="Page",
             clicks_column="Clicks", impressions_column="Impressions", ctr_column="CTR", ctr_is_percent=True,
             impression_position_column="Average impression position")
    result = run("bwt-import", [ROOT / "performance.csv"], **o)
    assert result.metadata["report"]["rows"][0]["ctr"] == 0.01
    for change in [{"period_totals": False}, {"ctr_is_percent": False}, {"query_column": "NoSuchColumn"}]:
        with pytest.raises(KeywordMovesError):
            run("bwt-import", [ROOT / "performance.csv"], **{**o, **change})


def test_observation_csv_requires_explicit_scope_units():
    result = run("import-observations", [ROOT / "observations.csv"], source="Reviewed licensed Bing estimates",
                 scope="Bing only:GB:English", observed_at="2026-10-02", geography="GB",
                 phrase_column="Keyword", value_column="Value", metric="estimated_search_volume", unit="searches_per_month")
    assert result.keywords[0].evidence[0].value == 1200
    assert result.keywords[1].evidence[0].value is None


def test_csv_per_row_metric_and_units(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("Term,Metric,Unit,Value\nplanes,organic_difficulty,provider_index_0_100,15\n")
    result = run("import-observations", [path], source="Reviewed Bing provider", scope="Bing:US",
                 observed_at="2026-10-02", phrase_column="Term", value_column="Value",
                 metric_column="Metric", unit_column="Unit")
    assert result.keywords[0].evidence[0].value == 15


@pytest.mark.parametrize("content", ["A,A\nx,y\n", "A,B\nx\n", "A,B\nx,y,z\n", ""])
def test_csv_malformed_headers_and_rows_fail(content, tmp_path):
    path = tmp_path / "input.csv"
    path.write_text(content)
    with pytest.raises(KeywordMovesError):
        run("import-observations", [path], source="reviewed", scope="Bing:GB", observed_at="2026-10-02",
            phrase_column="A", value_column="B", metric="backlinks", unit="count")


def html_options():
    return {"source": "Reviewed saved Bing HTML", "observed_at": "2026-10-02", "market": "en-GB",
            "device": "desktop", "organic_only": True, "row_selector": ".organic", "link_selector": "h2 a",
            "snippet_selector": "p"}


def html_run(path, o):
    return BingSearchPlugin().run(PluginRequest("import-serp-html", keywords=("paper plane song",),
                inputs=(path,), options=o), ExecutionContext(None))


def test_html_uses_reviewed_organic_rows_not_ads():
    result = html_run(ROOT / "serp.html", html_options())
    assert len(result.metadata["snapshot"]["organic"]) == 2
    assert result.metadata["snapshot"]["organic"][0]["title"] == "Paper plane song"
    assert "ads.example" not in json.dumps(result.to_dict())
    assert not result.metadata["live_query_performed"]


@pytest.mark.parametrize("options", [{"organic_only": False}, {"row_selector": ".no-matching-rows"},
                                    {"row_selector": "[[["}, {"link_selector": ".nonexistent"}])
def test_html_optin_empty_changed_and_bad_selectors_fail(options):
    with pytest.raises(KeywordMovesError):
        html_run(ROOT / "serp.html", {**html_options(), **options})


def test_html_explicit_no_result_marker_and_tracking_link_rejection(tmp_path):
    p = tmp_path / "page.html"
    p.write_text('<p class="empty">No results in this reviewed sample</p>')
    result = html_run(p, {**html_options(), "no_results_selector": ".empty"})
    assert result.metadata["snapshot"]["organic"] == []
    p.write_text('<div class="organic"><h2><a href="https://www.bing.com/ck/a">Result</a></h2></div>')
    with pytest.raises(InputError, match="tracking"):
        html_run(p, html_options())


def test_local_comparisons_reviews_and_combination_roundtrip(tmp_path):
    before, after = ROOT / "bwt-before.json", ROOT / "bwt-after.json"
    assert run("bwt-compare", [before, after]).keywords
    assert run("bwt-opportunities", [before]).keywords
    assert run("bwt-overlap", [before]).keywords
    assert run("serp-compare", [ROOT / "serp-before.json", ROOT / "serp-after.json"]).keywords
    assert run("compare", [ROOT / "observations-before.json", ROOT / "observations-after.json"]).keywords
    reports = [run("bwt-import", [before]), run("import-observations", [ROOT / "observations-before.json"])]
    paths = [write(tmp_path, f"report{i}.json", result.to_dict()) for i, result in enumerate(reports)]
    assert run("combine", paths).keywords
    assert run("keyword-gap", paths).keywords[0].phrase == "quiet music"
    assert run("bwt-import", paths[:1]).metadata["report"]["context"]["site_url"] == "https://example.com/"


@pytest.mark.parametrize("op", ["bwt-import", "import-serp", "import-serp-html", "import-observations",
                                "bwt-compare", "serp-compare", "compare", "combine", "keyword-gap"])
def test_local_inputs_are_required(op):
    with pytest.raises(ConfigurationError):
        run(op, [])


@pytest.mark.parametrize("value", [0, True, 1.2, -1, "lots", 50001])
def test_invalid_limits_fail_before_io(value):
    with pytest.raises(ConfigurationError):
        run("bwt-import", [ROOT / "bwt-before.json"], limit=value)


def test_invalid_option_names_and_llm_rejected():
    for o in ({"llm": "openai"}, {"provider": "google"}, {"sort_by": False}):
        with pytest.raises(ConfigurationError):
            run("bwt-import", [ROOT / "bwt-before.json"], **o)
    with pytest.raises(ConfigurationError):
        run("submit-url", [])


def test_ordering_keeps_missing_values_last_and_output_cap():
    result = run("bwt-import", [ROOT / "bwt-before.json"], sort_by="impressions", limit=1)
    assert result.keywords[0].phrase == "paper plane song"
    assert result.metadata["candidate_count"] == 3 and result.metadata["output_truncated"]
    with pytest.raises(ConfigurationError):
        run("bwt-import", [ROOT / "bwt-before.json"], sort_by="unavailable_metric")


def test_bad_input_encoding_size_and_json_are_clean_errors(tmp_path):
    for raw in (b"\xff", b"NaN", b"{", b"Infinity"):
        p = tmp_path / "data.json"
        p.write_bytes(raw)
        with pytest.raises(InputError):
            read_json(p, {})
    p.write_bytes(b"a" * 1025)
    with pytest.raises(InputError, match="not truncated"):
        read_text(p, {"max_input_bytes": 1024})
    with pytest.raises(InputError):
        read_text(tmp_path / "missing", {})
    p.write_bytes(b"\xef\xbb\xbf{}")
    assert read_json(p, {}) == {}


def test_no_live_input_or_local_keyword_confusion():
    for request in [PluginRequest("bwt-sites", inputs=(ROOT / "bwt-before.json",)),
                    PluginRequest("bwt-import", inputs=(ROOT / "bwt-before.json",), keywords=("seed",)),
                    PluginRequest("rank-check", keywords=("seed",)),
                    PluginRequest("ideas", keywords=("seed",), options={"provider": "keywordtool"}),
                    PluginRequest("suggestions", keywords=("seed",), options={"provider": "dataforseo"})]:
        with pytest.raises(ConfigurationError):
            BingSearchPlugin().run(request, ExecutionContext(None))


def test_cli_json_text_and_missing_key(capsys, monkeypatch):
    for format_ in ("json", "text"):
        assert main(["run", "bing-search", "--operation", "bwt-import", "--input", str(ROOT / "bwt-before.json"),
                     "--format", format_]) == 0
        out = capsys.readouterr()
        assert "paper plane song" in out.out
    monkeypatch.delenv("BING_WEBMASTER_API_KEY", raising=False)
    assert main(["run", "bing-search", "--operation", "bwt-sites"]) == 2
    assert "BING_WEBMASTER_API_KEY" in capsys.readouterr().err


def test_discovery_has_no_optional_dependency_imports():
    root = Path(__file__).resolve().parents[1]
    code = '''import sys
sys.modules.update({name: None for name in ("httpx", "bs4", "torch", "spacy", "nltk", "openai", "keybert", "sentence_transformers")})
from keywordmoves.cli import main
raise SystemExit(main(["plugins", "--json"]))
'''
    p = subprocess.run([sys.executable, "-c", code], cwd=root,
                       env={**os.environ, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, check=True)
    names = {r["name"] for r in json.loads(p.stdout)}
    assert {"bing-search", "bing-autocomplete", "reddit", "google-search", "youtube", "instagram", "tiktok"} <= names


def test_missing_html_dependency_does_not_disable_json_imports(monkeypatch):
    monkeypatch.setitem(sys.modules, "bs4", None)
    assert run("bwt-import", [ROOT / "bwt-before.json"]).keywords
    with pytest.raises(ConfigurationError, match="online"):
        html_run(ROOT / "serp.html", html_options())


def test_synthetic_example_and_non_overwrite(tmp_path):
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "demo"
    args = [sys.executable, str(root / "docs" / "examples" / "bing-search-workflow.py"),
            "--output", str(destination)]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    first = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True, check=True)
    assert "No network requests" in first.stdout
    files = sorted(destination.glob("*.json"))
    assert len(files) == 9
    previous = {p.name: p.read_bytes() for p in files}
    assert all(json.loads(p.read_text())["metadata"]["live_query_performed"] is False for p in files)
    second = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True)
    assert second.returncode == 2 and "already exists" in second.stderr
    assert previous == {p.name: p.read_bytes() for p in files}
