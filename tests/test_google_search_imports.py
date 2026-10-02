"""Offline schemas, imports, CLI and fixture-driven workflows; no private data."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keywordmoves.cli import main
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import google_search_imports as im
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.google_search import GoogleSearchPlugin

F = Path(__file__).parent / "fixtures" / "google-search"


def run(operation, files=(), keywords=(), **options):
    return GoogleSearchPlugin().run(PluginRequest(operation, tuple(keywords), tuple(Path(x) for x in files), options), ExecutionContext(None))


def save(tmp_path, name, value):
    path = tmp_path/name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_native_gsc_csv_percent_and_grouped_count():
    r = run("gsc-import", [F/"gsc.csv"], site_url="sc-domain:example.com", start_date="2026-09-01", end_date="2026-09-07",
            observed_at="2026-09-08", scope="csv-property-by-query", source="GSC CSV")
    rows = r.metadata["gsc_report"]["rows"]
    assert rows[0]["ctr"] == .01 and rows[1]["impressions"] == 1000
    assert not r.metadata["live_query_performed"] and r.metadata["request_count"] == 0


def test_canonical_report_needs_no_redundant_date_options(tmp_path):
    r = run("gsc-query", [F/"gsc-before.json"])
    assert len(r.keywords) == 3
    path = save(tmp_path, "result.json", r.to_dict())
    reread = run("gsc-import", [path])
    assert reread.metadata["gsc_report"] == r.metadata["gsc_report"]
    opp = run("gsc-opportunities", [path], target_ctr=.03)
    assert len(opp.keywords) == 3 and all(i.relationship == "review-candidate" for i in opp.keywords)
    overlap = run("gsc-overlap", [path])
    assert len(overlap.keywords) == 1 and overlap.keywords[0].phrase == "paper plane song"


def test_gsc_compare_uses_original_captures():
    r = run("gsc-compare", [F/"gsc-before.json", F/"gsc-after.json"])
    assert r.metadata["before_period"] == ["2026-09-01", "2026-09-07"]
    missing = next(i for i in r.keywords if i.phrase == "independent music")
    assert missing.metadata["presence"] == "only_before"
    assert next(e.value for e in missing.evidence if e.metric == "impressions_after") is None


@pytest.mark.parametrize("body", ["{", '{"rows":null}', '{"rows":[{"keys":["x"],"ctr":Infinity}]}'])
def test_invalid_gsc_json(tmp_path, body):
    p = tmp_path/"input.json"
    p.write_text(body)
    with pytest.raises((InputError, OnlineSourceError, ConfigurationError)):
        run("gsc-import", [p])


@pytest.mark.parametrize("body", ["Query,Clicks\nx,2\n", "Query,Clicks,Impressions,CTR,Position\nx,1,2,1%,3,extra\n"])
def test_malformed_csv_does_not_invent_columns(tmp_path, body):
    p = tmp_path/"input.csv"
    p.write_text(body)
    with pytest.raises(InputError):
        run("gsc-import", [p], observed_at="2026-10-01")


def test_serp_json_import_compare_and_rank_filter():
    r = run("import-serp", [F/"serp-before.json"], target_host="example.com")
    assert len(r.keywords) == 2
    assert r.metadata["serp_report"]["source"] == "synthetic-demo"
    c = run("serp-compare", [F/"serp-before.json", F/"serp-after.json"])
    assert c.metadata["url_jaccard_overlap"] == 1
    page = next(i for i in c.keywords if i.phrase == "https://example.com/music/paper-planes")
    assert next(e.value for e in page.evidence if e.metric == "rank_improvement") == 1


def test_saved_html_explicit_selectors_exclude_ad():
    r = run("import-serp-html", [F/"serp.html"], ["paper plane song"], source="saved-browser", scope="test",
            observed_at="2026-10-01", country="uk", organic_selector=".organic", title_selector="h3", link_selector="a",
            snippet_selector="p", rank_attribute="data-rank")
    assert len(r.metadata["serp_report"]["organic"]) == 1
    assert r.metadata["serp_report"]["organic"][0]["url"].startswith("https://example.com/")
    assert r.metadata["serp_report"]["html_selection"]["organic_selector"] == ".organic"


@pytest.mark.parametrize("body", ["<title>Before you continue</title>", "<form><input type=password></form>",
                                  "<title>unusual traffic</title>", "<div class=g-recaptcha></div>"])
def test_html_challenges_blocked(body):
    with pytest.raises(OnlineSourceError):
        im.soup_for(body)


def test_bad_css_and_no_results_are_explicit():
    with pytest.raises(ConfigurationError):
        im.select(im.soup_for("<p>hello</p>"), "[")
    with pytest.raises(InputError):
        run("import-serp-html", [F/"serp.html"], ["planes"], organic_selector=".missing", title_selector="h3", link_selector="a")


def test_page_audit_static_fields_and_no_density_recommendation():
    r = run("page-audit", [F/"page.html"], ["paper plane song"], observed_at="2026-10-01")
    meta = r.metadata["page_audit"]
    assert meta["title"] == "Paper plane song" and meta["meta_robots"] == "index,follow"
    assert "secret" not in json.dumps(r.to_dict())
    assert all(i.score is None for i in r.keywords)


def test_observation_compare_missing_zero_and_censored_excluded(tmp_path):
    before = json.loads((F/"observations.json").read_text())
    after = json.loads(json.dumps(before))
    after["observations"][0].update(observed_at="2026-09-15", value=400)
    after["observations"][1].update(observed_at="2026-09-15", value=30, approximate=True)
    b = save(tmp_path, "after.json", after)
    imported = run("import-observations", [b])
    assert imported.keywords[1].metadata["approximate"]
    c = run("observed-compare", [F/"observations.json", b])
    items = {i.metadata["metric"]: i for i in c.keywords}
    assert items["estimated_search_volume"].evidence[0].value == 100
    assert items["organic_keyword_difficulty"].evidence[0].value is None
    assert items["organic_keyword_difficulty"].metadata["approximate_excluded"]


def test_observation_csv_mappings_and_zero(tmp_path):
    p = tmp_path/"obs.csv"
    p.write_text("Term,metric,value,unit,source,scope,observed_at,approximate\nplanes,volume,0,searches_per_month,test,uk,2026-10-01,false\n")
    r = run("import-observations", [p], phrase_column="Term")
    assert r.keywords[0].evidence[0].value == 0


def test_observation_comparison_rejects_unmatched_scope(tmp_path):
    b = json.loads((F/"observations.json").read_text())
    for row in b["observations"]:
        row["scope"] = "other"
    with pytest.raises(InputError, match="No compatible"):
        run("observed-compare", [F/"observations.json", save(tmp_path, "other.json", b)])


def test_trends_import_preserves_below_one_and_true_zero():
    r = run("trends-import", [F/"trends.csv"], search_property="web", country="GB", scope="test-comparison", observed_at="2026-10-01")
    points = r.keywords[0].metadata["series"]
    assert points[1]["value"] is None and points[1]["censored_below_one"]
    assert points[2]["value"] == 0 and not points[2]["censored_below_one"]
    with pytest.raises(ConfigurationError):
        run("trends-import", [F/"trends.csv"])


def test_combine_and_gap_are_supplied_evidence_not_absence_claims(tmp_path):
    base = run("gsc-query", [F/"gsc-before.json"]).to_dict()
    second = run("import-observations", [F/"observations.json"]).to_dict()
    x, y = save(tmp_path, "gsc.json", base), save(tmp_path, "obs.json", second)
    combined = run("combine", [x, y])
    plane = next(i for i in combined.keywords if i.phrase == "paper plane song")
    assert len(plane.evidence) == 10 and plane.score is None
    gap = run("keyword-gap", [y, x])
    assert [i.phrase for i in gap.keywords] == ["independent music"]
    assert "does not mean" in gap.notes[0]


def test_sort_single_metric_and_missing_last():
    r = run("gsc-query", [F/"gsc-before.json"], sort_by="average_position", sort_order="asc")
    assert next(e.value for e in r.keywords[0].evidence if e.metric == "average_position") == 6
    assert r.keywords[0].phrase == "independent music"
    with pytest.raises(ConfigurationError):
        run("gsc-query", [F/"gsc-before.json"], sort_by="imaginary_score")


@pytest.mark.parametrize("operation,files", [("gsc-import", []), ("gsc-compare", [F/"gsc-before.json"]),
    ("serp-compare", []), ("keyword-gap", []), ("combine", []), ("import-observations", []),
    ("page-audit", [F/"page.html", F/"page.html"])])
def test_file_arities(operation, files):
    with pytest.raises(ConfigurationError):
        run(operation, files)


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": True}, {"llm": "openai"}, {"typo": True}, {"sort_order": "asc"}])
def test_cli_configuration_invalid(options):
    with pytest.raises(ConfigurationError):
        run("gsc-sites", **options)


def test_bad_files_and_size_limits(tmp_path):
    for content in (b"\xff", b"x"*1025):
        p = tmp_path/"bad.json"
        p.write_bytes(content)
        with pytest.raises(InputError):
            im.read_path(p, {"max_response_bytes": 1024})
    with pytest.raises(InputError):
        im.read_path(tmp_path/"missing", {})


def test_cli_works_offline_and_lazy_dependencies(capsys):
    assert main(["run", "google-search", "--operation", "gsc-import", "--input", str(F/"gsc-before.json")]) == 0
    assert json.loads(capsys.readouterr().out)["plugin"] == "google-search"
    assert main(["run", "google-search", "--operation", "unsupported"]) == 2
    assert "Unknown" in capsys.readouterr().err
    root = Path(__file__).resolve().parents[1]
    code = '''import builtins
real_import = builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'httpx','bs4','spacy','nltk','keybert','openai','torch','sentence_transformers'}:
        raise ImportError(name)
    return real_import(name,*args,**kwargs)
builtins.__import__ = guarded
from keywordmoves.cli import main
raise SystemExit(main(['plugins','--json']))
'''
    r = subprocess.run([sys.executable, "-c", code], cwd=root,
                       env={**os.environ, "PYTHONPATH": str(root/"src")}, capture_output=True, text=True, check=True)
    names = [i["name"] for i in json.loads(r.stdout)]
    assert "google-search" in names and "search-console" in names
