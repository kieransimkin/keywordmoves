"""Deterministic analysis and unit/period invariants using fabricated evidence."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from keywordmoves.errors import ConfigurationError, InputError, KeywordMovesError
from keywordmoves.online import bing_search_analysis as a
from keywordmoves.online.common import OnlineSourceError

ROOT = Path(__file__).parent / "fixtures" / "bing-search"


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def values(candidate):
    return {e.metric: e.value for e in candidate.evidence}


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), (0, 0), ("1.5", 1.5),
    (9007199254740993, 9007199254740993), ("9007199254740993", 9007199254740993)])
def test_numeric_preserves_missing_zero_and_integer_precision(value, expected):
    assert a.numeric(value) == expected


@pytest.mark.parametrize("value", [True, False, -1, "-1", "nan", float("inf"), {}, "unknown"])
def test_invalid_metrics_rejected(value):
    with pytest.raises(OnlineSourceError):
        a.numeric(value)


@pytest.mark.parametrize("value", [1.5, "2.2"])
def test_nonintegral_counts_rejected(value):
    with pytest.raises(OnlineSourceError):
        a.numeric(value, integer_only=True)


@pytest.mark.parametrize("value,expected", [
    ("/Date(1316156400000-0700)/", "2011-09-16T00:00:00-07:00"),
    ("/Date(0+0100)/", "1970-01-01T01:00:00+01:00"),
    ("/Date(0-0700)/", "1969-12-31T17:00:00-07:00"),
    ("/Date(-62135596800000)/", None), ("0001-01-01T00:00:00", None),
    ("2026-09-01", "2026-09-01"), ("2026-09-01T04:00:00", "2026-09-01T04:00:00"),
    ("2026-09-01T04:00:00Z", "2026-09-01T04:00:00+00:00"), (None, None), ("", None),
])
def test_native_dates_preserve_source_offset_and_do_not_invent_bucket_periods(value, expected):
    assert a.native_date(value) == expected


@pytest.mark.parametrize("value", [123, "bad", "/Date(0+2460)/", "/Date(999999999999999999999)/", "2026-02-30"])
def test_invalid_native_date(value):
    with pytest.raises(OnlineSourceError):
        a.native_date(value)


@pytest.mark.parametrize("value", [None, "20260901", "2026-02-31", "2026-09-01T00:00:00"])
def test_capture_time_requires_date_or_zone(value):
    with pytest.raises(InputError):
        a.captured(value)


@pytest.mark.parametrize("value", ["ftp://example.com/", "https://u:p@example.com/", "https://example.com:bad/", "not a URL"])
def test_url_validation(value):
    with pytest.raises(KeywordMovesError):
        a.url(value)


def test_competition_is_bounded_organic_evidence_not_result_count_difficulty():
    snapshot = a.validate_serp(load("serp-before.json"))
    c = a.competition(snapshot, {"top_n": 3, "target_host": "example.com"})
    v = values(c)
    assert v["observed_organic_results_in_top_n"] == 3
    assert v["distinct_hostnames_in_top_n"] == 2
    assert v["largest_hostname_share_of_observed_top_n"] == pytest.approx(2 / 3)
    assert v["target_best_observed_organic_rank"] == 1
    assert v["literal_title_matches_in_observed_top_n"] == 1
    assert "difficulty" not in v and "total_results_estimate" not in v and c.score is None
    assert c.metadata["top_n_fully_observed"]
    assert snapshot["total_results_estimate"] == 999999


def test_target_absence_unknown_and_exact_hostname_boundary():
    snapshot = a.validate_serp(load("serp-before.json"))
    for host, subdomains, expected in [("missing.example", False, None), ("example.net", False, None),
                                       ("example.net", True, 2), ("ample.net", True, None)]:
        c = a.competition(snapshot, {"target_host": host, "include_subdomains": subdomains})
        assert values(c)["target_best_observed_organic_rank"] == expected
    assert a.target_host("https://EXAMPLE.com/page") == "example.com"
    with pytest.raises(ConfigurationError):
        a.target_host("bad..host")


def test_top_n_does_not_relabel_a_deep_page_as_the_top():
    snapshot = load("serp-before.json")
    for row in snapshot["organic"]:
        row["rank"] += 50
    c = a.competition(a.validate_serp(snapshot), {})
    assert values(c)["observed_organic_results_in_top_n"] == 0
    assert values(c)["largest_hostname_share_of_observed_top_n"] is None
    assert not c.metadata["top_n_fully_observed"]


def test_serp_deduplication_and_dimensional_conflicts():
    raw = load("serp-before.json")
    raw["organic"].append(deepcopy(raw["organic"][0]))
    assert len(a.validate_serp(raw)["organic"]) == 3
    raw["organic"][-1]["url"] = "https://another.example/"
    with pytest.raises(InputError, match="share a rank"):
        a.validate_serp(raw)
    raw = load("serp-before.json")
    raw["engine"] = "Google"
    with pytest.raises(InputError):
        a.validate_serp(raw)


def test_serp_comparison_missing_results_are_not_infinite_ranks():
    before = a.validate_serp(load("serp-before.json"))
    after = a.validate_serp(load("serp-after.json"))
    items, meta = a.compare_serps(before, after)
    assert values(items[0])["observed_url_jaccard"] == 0.5
    changes = {r["url"]: r for r in meta["rank_changes"]}
    assert changes["https://example.com/music/"]["positions_gained"] == -1
    assert changes["https://example.com/live/"]["positions_gained"] is None
    assert changes["https://other.example.org/"]["before_rank"] is None


def test_serp_comparison_timezone_chronology_is_not_lexical():
    before = a.validate_serp(load("serp-before.json"))
    after = deepcopy(before)
    before["observed_at"] = "2026-10-02T01:00:00+02:00"
    after["observed_at"] = "2026-10-01T23:30:00+00:00"
    a.compare_serps(before, after)
    with pytest.raises(InputError, match="chronological"):
        a.compare_serps(after, before)
    after["context"]["device"] = "mobile"
    with pytest.raises(InputError, match="context"):
        a.compare_serps(before, after)


def test_bwt_distinguishes_position_metrics_and_native_buckets():
    report = a.validate_bwt(load("bwt-before.json"))
    c = a.bwt_candidates(report)[0]
    v = values(c)
    assert v["ctr"] == 0.01 and v["average_click_position"] == 4
    assert v["average_impression_position"] == 6
    assert values(a.bwt_candidates(report)[2])["ctr"] is None
    assert report["context"]["site_url"] == "https://example.com/"


@pytest.mark.parametrize("mutation", ["duplicate", "clicks-exceed", "percent-ctr", "granularity", "period-bucket",
                                      "native-no-date", "invented-period", "page-not-url", "negative-count"])
def test_invalid_bwt_aggregation_and_units_rejected(mutation):
    r = load("bwt-before.json")
    if mutation == "duplicate":
        r["rows"].append(deepcopy(r["rows"][0]))
    elif mutation == "clicks-exceed":
        r["rows"][0]["clicks"] = 2000
    elif mutation == "percent-ctr":
        r["rows"][0]["ctr"] = 1
    elif mutation == "granularity":
        r["context"]["granularity"] = "invented"
    elif mutation == "period-bucket":
        r["rows"][0]["date"] = "2026-09-01"
    elif mutation in {"native-no-date", "invented-period"}:
        r["context"]["granularity"] = "native-buckets"
        if mutation == "native-no-date":
            r["context"].pop("start_date")
            r["context"].pop("end_date")
    elif mutation == "page-not-url":
        r["rows"][0]["page"] = "page"
    else:
        r["rows"][0]["impressions"] = -1
    with pytest.raises(KeywordMovesError):
        a.validate_bwt(r)


def test_bwt_opportunities_are_row_filters_with_optional_scenarios():
    r = a.validate_bwt(load("bwt-before.json"))
    selected = a.bwt_opportunities(r, {"min_impressions": 500, "target_ctr": 0.05})
    assert len(selected) == 1
    v = values(selected[0])
    assert any("hypothetical" in e.unit for e in selected[0].evidence)
    assert 40 in v.values()  # (5% - 1%) * 1000, not a promise of additional traffic.
    assert a.bwt_opportunities(r, {"max_ctr": 0.001}) == []
    with pytest.raises(ConfigurationError):
        a.bwt_opportunities(r, {"min_position": 20, "max_position": 10})


def test_bwt_overlap_preserves_rows_and_does_not_claim_cannibalisation():
    r = a.validate_bwt(load("bwt-before.json"))
    items, meta = a.bwt_overlap(r)
    assert len(items) == 1 and items[0].evidence[0].value == 2
    assert len(items[0].metadata["rows"]) == 2
    assert "not proof" in items[0].evidence[0].notes
    assert meta["groups_examined"] == 2


def test_bwt_period_comparison_ctr_percentage_points_and_missing_unknown():
    before, after = [a.validate_bwt(load(n)) for n in ("bwt-before.json", "bwt-after.json")]
    items, meta = a.compare_bwt(before, after)
    first = next(x for x in items if x.metadata["page"] == "https://example.com/music/")
    assert values(first)["ctr_change"] == pytest.approx(2)
    assert values(first)["average_impression_position_change"] == -1
    missing = next(r for r in meta["changes"] if r["query"] == "quiet music")
    assert missing["after"] is None and missing["status"] == "missing-row-unknown"


@pytest.mark.parametrize("change", ["source", "scope", "duration", "overlap", "native"])
def test_bwt_period_comparison_requires_compatible_scopes(change):
    before, after = [a.validate_bwt(load(n)) for n in ("bwt-before.json", "bwt-after.json")]
    if change == "source":
        after["source"] = "Another source"
    elif change == "scope":
        after["context"]["scope"] = "Mobile only"
    elif change == "duration":
        after["context"]["end_date"] = "2026-09-30"
    elif change == "overlap":
        after["context"].update(start_date="2026-09-01", end_date="2026-09-14")
    else:
        after["context"]["granularity"] = "native-buckets"
    with pytest.raises(InputError):
        a.compare_bwt(before, after)


@pytest.mark.parametrize("change", ["unit", "duplicate", "range", "approximate", "one-period-bound"])
def test_observation_units_scope_and_precision(change):
    r = load("observations-before.json")
    if change == "unit":
        r["observations"][0]["unit"] = "Google searches"
    elif change == "duplicate":
        r["observations"].append(deepcopy(r["observations"][0]))
    elif change == "range":
        r["observations"][1]["value"] = 150
    elif change == "approximate":
        r["observations"][0]["approximate"] = "true"
    else:
        r["observations"][0].pop("period_end")
    with pytest.raises(KeywordMovesError):
        a.observations(r)


def test_snapshot_comparison_has_zero_baseline_and_approximation_guards():
    before, after = [a.observations(load(n)) for n in ("observations-before.json", "observations-after.json")]
    items, meta = a.compare_observations(before, after)
    assert values(items[0])["net_change"] == 300
    assert values(items[0])["change_per_day"] == 300
    assert values(items[0])["relative_change_percent"] == 25
    assert len(meta["skipped_comparisons"]) == 1
    before["observations"][0]["value"] = 0
    assert values(a.compare_observations(before, after)[0][0])["relative_change_percent"] is None
    before["observations"][0]["approximate"] = True
    assert a.compare_observations(before, after)[0] == []


def test_unknown_snapshot_periods_not_mixed_with_other_windows():
    before, after = [a.observations(load(n)) for n in ("observations-before.json", "observations-after.json")]
    after["observations"][0]["period_start"] = "2026-08-01"
    assert a.compare_observations(before, after)[0] == []
    after = deepcopy(before)
    with pytest.raises(InputError, match="chronological"):
        a.compare_observations(before, after)


def test_combination_preserves_each_source_metadata_envelope():
    item = a.observation_candidates(a.observations(load("observations-before.json")))[0]
    from keywordmoves.models import PluginResult
    report = PluginResult("bing-search", "import-observations", (item,)).to_dict()
    # JSON boundary converts the dataclass tuple output into arrays.
    report = json.loads(json.dumps(report))
    items, _ = a.combine_reports([report, report])
    assert len(items) == 1 and len(items[0].evidence) == 2 and items[0].score is None
    assert len(items[0].metadata["source_entries"]) == 2
    assert a.combine_reports([report, report], gap=True)[0] == []
    with pytest.raises(InputError):
        a.combine_reports([{"plugin": "google-search", "keywords": []}])


def test_search_comparison_keeps_collection_window():
    before = a.validate_serp(load("serp-before.json"))
    after = a.validate_serp(load("serp-after.json"))
    after["context"]["first"] = 11
    with pytest.raises(InputError, match="context"):
        a.compare_serps(before, after)


def test_unsorted_duplicate_url_retains_best_observed_rank():
    raw = load("serp-before.json")
    raw["organic"].insert(0, {**raw["organic"][0], "rank": 10})
    result = a.validate_serp(raw)
    assert len(result["organic"]) == 3 and result["organic"][0]["rank"] == 1
