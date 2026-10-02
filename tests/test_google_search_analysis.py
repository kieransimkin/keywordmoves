"""Deterministic evidence semantics. All measurements and domains are synthetic."""
import json
from copy import deepcopy

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.online import google_search_analysis as a
from keywordmoves.online.common import OnlineSourceError, candidate

META = {"site_url": "sc-domain:example.com", "dimensions": ["query"], "start_date": "2026-09-01",
        "end_date": "2026-09-07", "observed_at": "2026-09-08", "search_type": "web",
        "source_scope": "test", "data_state": "final", "aggregation_type": "byProperty", "filters": []}


def report(rows=None, **meta):
    return a.gsc_report(rows if rows is not None else [
        {"keys": ["paper planes"], "clicks": 10, "impressions": 1000, "ctr": .01, "position": 8}], {**META, **meta})


def serp(**changes):
    return {"schema": a.SERP_SCHEMA, "query": "paper planes", "source": "synthetic-provider", "scope": "test-web",
            "observed_at": "2026-10-01T00:00:00Z", "country": "gb", "language": "en", "device": "desktop",
            "engine": "google", "organic": [
                {"url": "https://www.example.com/a#section", "position": 1, "title": "Paper planes tutorial"},
                {"url": "https://example.com/b", "position": 2, "title": "Paper planes"},
                {"url": "https://other.example/a?x=1", "position": 3, "title": "Paper planesmith"}],
            "features_returned": ["ads", "related_questions"], "approximate_total_results": 1_000_000, **changes}


def evidence(item):
    return {e.metric: e.value for e in item.evidence}


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -1, "unknown", [], {}])
def test_bad_numbers_fail(value):
    with pytest.raises(OnlineSourceError):
        a.numeric(value)


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("0", 0), (0, 0), ("2.5", 2.5)])
def test_missing_is_not_zero(value, expected):
    assert a.numeric(value) == expected


@pytest.mark.parametrize("value", [True, float("nan"), None, 1.1, "yes"])
def test_invalid_option_float(value):
    with pytest.raises(ConfigurationError):
        a.option_float({"x": value}, "x", .5)


@pytest.mark.parametrize("url", ["/relative", "ftp://example.com", "https://name:secret@example.com", "https://example.com:bad"])
def test_bad_urls(url):
    with pytest.raises(OnlineSourceError):
        a.normal_url(url)


def test_url_normalisation_is_conservative():
    assert a.normal_url("https://EXAMPLE.com/Path?x=1#frag") == "https://example.com/Path?x=1"
    assert a.hostname("https://www.example.com/a") == "example.com"


@pytest.mark.parametrize("meta", [{"dimensions": ["query", "query"]}, {"dimensions": ["bad"]},
    {"site_url": None}, {"source_scope": ""}, {"start_date": "20260901"},
    {"start_date": "2026-09-30"}, {"observed_at": "2026-02-30"}])
def test_report_metadata_is_required(meta):
    with pytest.raises(InputError):
        report(**meta)


@pytest.mark.parametrize("row", [{"keys": []}, {"keys": [42]}, {"dimensions": {}},
    {"keys": ["a"], "clicks": 11, "impressions": 10}, {"keys": ["a"], "ctr": 5},
    {"keys": ["a"], "position": -1}])
def test_invalid_rows(row):
    with pytest.raises((InputError, OnlineSourceError)):
        report([row])


def test_duplicate_rows_not_double_counted():
    row = {"keys": ["x"], "impressions": 10}
    with pytest.raises(InputError, match="Duplicate"):
        report([row, row])


def test_ctr_uses_ratio_and_preserves_unknown_zero():
    r = report([{"keys": ["Exact CASE"], "clicks": 4, "impressions": 100, "position": 0},
                {"keys": ["unknown"], "impressions": None}, {"keys": ["zero"], "clicks": 0, "impressions": 0},
                {"keys": [""], "clicks": 1, "impressions": 20}])
    assert r["rows"][0]["ctr"] == .04 and r["rows"][1]["impressions"] is None
    assert r["rows"][2]["impressions"] == 0 and r["rows"][2]["ctr"] is None
    items = a.gsc_candidates(r, META["observed_at"])
    assert [i.phrase for i in items] == ["Exact CASE", "unknown", "zero"]
    assert all(i.score is None for i in items)
    assert r["date_timezone"] == "America/Los_Angeles"


def test_opportunities_are_explicit_ctr_what_if():
    items = a.gsc_opportunities(report(), {"target_ctr": .03}, META["observed_at"])
    e = evidence(items[0])
    assert e["additional_clicks_if_target_ctr_at_same_impressions"] == pytest.approx(20)
    assert items[0].metadata["target_ctr"] == .03
    assert not a.gsc_opportunities(report(), {"max_ctr": .005}, META["observed_at"])
    assert "additional_clicks_if_target_ctr_at_same_impressions" not in evidence(a.gsc_opportunities(report(), {}, META["observed_at"])[0])
    with pytest.raises(ConfigurationError):
        a.gsc_opportunities(report(dimensions=["page"]), {}, META["observed_at"])


def test_query_page_overlap_keeps_country_device_separate():
    rows = [{"keys": ["planes", url, country], "clicks": 2, "impressions": n, "position": p}
            for url, country, n, p in [("/a", "gbr", 80, 4), ("/b", "gbr", 20, 8), ("/c", "usa", 5, 2)]]
    r = report(rows, dimensions=["query", "page", "country"])
    items = a.query_page_overlap(r, META["observed_at"])
    assert len(items) == 1
    assert evidence(items[0])["distinct_returned_pages"] == 2
    assert items[0].metadata["pages"][0]["share_of_returned_page_impressions"] == .8
    assert "not a proven" in items[0].evidence[0].notes
    r["rows"][1]["impressions"] = None
    assert evidence(a.query_page_overlap(r, META["observed_at"])[0])["returned_page_impressions"] is None
    with pytest.raises(ConfigurationError):
        a.query_page_overlap(report(), META["observed_at"])


def test_compare_ctr_percentage_points_position_and_missing_queries():
    old = report()
    new = report([{"keys": ["paper planes"], "clicks": 20, "impressions": 1000, "ctr": .02, "position": 5},
                  {"keys": ["new query"], "clicks": 4, "impressions": 60}], start_date="2026-09-08", end_date="2026-09-14")
    results = {i.phrase: i for i in a.compare_gsc(old, new, {}, "2026-10-02")}
    e = evidence(results["paper planes"])
    assert e["ctr_change"] == 1 and e["position_change"] == -3
    assert evidence(results["new query"])["clicks_change"] is None
    assert results["new query"].metadata["presence"] == "only_after"


@pytest.mark.parametrize("change", [{"site_url": "sc-domain:other.com"}, {"data_state": "all"},
    {"source_scope": "other"}, {"aggregation_type": "byPage"}, {"filters": ["changed"]},
    {"start_date": "2026-09-03", "end_date": "2026-09-09"},
    {"start_date": "2026-09-08", "end_date": "2026-09-15"}])
def test_incompatible_gsc_comparisons_fail(change):
    b = report(start_date="2026-09-08", end_date="2026-09-14")
    b.update(change)
    with pytest.raises(InputError):
        a.compare_gsc(report(), b, {}, "2026-10-02")


def test_date_dim_cannot_compare_different_period_rows():
    b = report(dimensions=["date"], start_date="2026-09-08", end_date="2026-09-14")
    with pytest.raises(InputError, match="period totals"):
        a.compare_gsc(report(dimensions=["date"]), b, {}, "2026-10-02")


def test_serp_has_no_universal_difficulty_or_result_count_ratio():
    r = a.validate_serp(serp())
    item = a.serp_competition(r, {"target_host": "example.com"})[0]
    e = evidence(item)
    assert e["top_n_distinct_hostnames"] == 2
    assert e["top_n_largest_hostname_share"] == pytest.approx(2/3)
    assert e["top_n_title_phrase_matches"] == 2
    assert e["target_best_observed_organic_rank"] == 1
    assert item.metadata["difficulty_verdict"] is None and item.score is None
    assert "not_competition" in next(e.unit for e in item.evidence if e.metric == "approximate_reported_results")
    missing = a.serp_competition(r, {"target_host": "missing.example"})[0]
    assert evidence(missing)["target_best_observed_organic_rank"] is None
    assert evidence(a.serp_competition(serp(organic=[]), {})[0])["top_n_largest_hostname_share"] is None


def test_serp_dedup_and_querystrings_remain_distinct():
    r = serp()
    r["organic"].append(dict(r["organic"][0]))
    assert len(a.validate_serp(r)["organic"]) == 3
    r["organic"].append({"url": "https://other.example/a?x=2", "position": 4})
    assert len(a.validate_serp(r)["organic"]) == 4


@pytest.mark.parametrize("changes", [{"schema": "unknown"}, {"source": ""}, {"observed_at": "garbage"},
    {"features_returned": [{}]}, {"organic": [{"url": "https://example.com", "position": 1.2}]},
    {"organic": [{"url": "https://a.example", "position": 1}, {"url": "https://b.example", "position": 1}]}])
def test_malformed_serp(changes):
    with pytest.raises((InputError, OnlineSourceError)):
        a.validate_serp(serp(**changes))


def test_paa_suggestions_dedup_and_continuation_token():
    suggestion = {"phrase": "How to fold?", "kind": "people-also-ask", "next_page_token": "next"}
    items = a.serp_suggestions(serp(suggestions=[suggestion, suggestion]))
    assert len(items) == 1 and items[0].metadata["next_page_token"] == "next"


def test_serp_compare_tracks_observed_urls_not_deindexing():
    old = serp()
    new = deepcopy(old)
    new["observed_at"] = "2026-10-02T00:00:00Z"
    new["organic"] = [{"url": "https://other.example/a?x=1", "position": 1}, {"url": "https://new.example/", "position": 2}]
    items, meta = a.compare_serps(old, new, {})
    assert meta["url_jaccard_overlap"] == .25
    byurl = {i.phrase: evidence(i) for i in items}
    assert byurl["https://other.example/a?x=1"]["rank_improvement"] == 2
    assert byurl["https://example.com/b"]["rank_after"] is None


@pytest.mark.parametrize("key,value", [("country", "us"), ("device", "mobile"), ("location", "different"),
    ("source", "different"), ("html_selection", {"organic_selector": "changed"}), ("observed_at", "2026-09-01")])
def test_incompatible_serps(key, value):
    b = serp(observed_at="2026-10-02T00:00:00Z")
    b[key] = value
    with pytest.raises(InputError):
        a.compare_serps(serp(), b, {})


def test_merge_evidence_does_not_average_or_lose_provenance():
    x = candidate("A", "Paper planes", {"volume": (100, "searches_per_month")}, observed_at="2026-10-01", geography="GB")
    y = candidate("B", "paper planes", {"volume": (80, "searches_per_month")}, observed_at="2026-10-01", geography="GB")
    from dataclasses import asdict
    merged = a.merge_keywords(json.loads(json.dumps([{"plugin": "a", "keywords": [asdict(x)]}, {"plugin": "b", "keywords": [asdict(y)]}])))
    assert len(merged) == 1 and [e.value for e in merged[0].evidence] == [100, 80]
    assert merged[0].score is None


def test_unwrap_and_limit():
    r = report()
    assert a.unwrap({"metadata": {"gsc_report": r}}, a.GSC_SCHEMA, "gsc_report") == r
    with pytest.raises((InputError, OnlineSourceError)):
        a.unwrap({}, a.GSC_SCHEMA, "gsc_report")
    items = a.gsc_candidates(r, META["observed_at"])*2
    result = a.finish("gsc-query", items, {"limit": 1})
    assert len(result.keywords) == 1 and result.metadata["output_truncated"] is True


def test_combining_equal_counts_keeps_page_device_and_period_associations():
    from dataclasses import asdict

    results = []
    for page, device, start in [("https://example.com/a", "MOBILE", "2026-09-01"),
                                 ("https://example.com/b", "DESKTOP", "2026-09-08")]:
        item = candidate("Google Search Console", "same query", {"impressions": (100, "count")},
                         observed_at="2026-10-02", metadata={"dimensions": {"page": page, "device": device}})
        results.append({"plugin": "google-search", "operation": "gsc-query-pages",
                        "keywords": [asdict(item)], "metadata": {"gsc_report": {
                            "start_date": start, "filters": [{"country": "gbr"}], "rows": []}}})
    merged = a.merge_keywords(json.loads(json.dumps(results)))[0]
    assert len(merged.evidence) == 1  # Union, not sum of equal measurements.
    origins = merged.metadata["origins"]
    assert len(origins) == 2 and all(o["evidence"][0]["value"] == 100 for o in origins)
    assert origins[0]["candidate_metadata"]["dimensions"]["device"] == "MOBILE"
    assert origins[1]["candidate_metadata"]["dimensions"]["page"].endswith("/b")
    assert origins[0]["source_context"]["gsc_report"]["start_date"] == "2026-09-01"
    assert origins[1]["source_context"]["gsc_report"]["start_date"] == "2026-09-08"
    assert "rows" not in origins[0]["source_context"]["gsc_report"]
