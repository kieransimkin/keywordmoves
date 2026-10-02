"""Synthetic Instagram records; no live metrics, accounts or credentials."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from keywordmoves.errors import InputError
from keywordmoves.online.common import OnlineSourceError
from keywordmoves.online.instagram_analysis import (
    analyse,
    compare,
    count,
    displayed_count,
    hashtag,
    hashtags,
    media_row,
    permalink,
    stats_candidates,
    timestamp,
)

OBSERVED = "2026-10-02T12:00:00+00:00"


def values(candidate):
    return {e.metric: e.value for e in candidate.evidence}


def sample(rows, **kwargs):
    return analyse([media_row(r, i, provider=kwargs.get("source", "fixture")) for i, r in enumerate(rows)],
                   source=kwargs.pop("source", "fixture"), observed=OBSERVED, **kwargs)


@pytest.mark.parametrize("raw,expected", [(" #Brighton ", "#brighton"), ("UK_Garage", "#uk_garage"),
    ("#Café", "#café"), ("#Cafe\u0301", "#café"), ("#東京", "#東京"), ("#straße", "#straße"),
    ("#ukg2026", "#ukg2026")])
def test_hashtag_normalisation(raw, expected):
    assert hashtag(raw) == expected


@pytest.mark.parametrize("raw", [None, True, 123, "", "#", "#word space", "two-words", "x/y", "x?y", "a"*101])
def test_invalid_tag_is_not_silently_changed(raw):
    with pytest.raises(InputError):
        hashtag(raw)


def test_offsets_are_original_codepoints_not_normalised_bytes():
    raw = "Rain 💜 #Cafe\u0301, #UK_Garage #東京. url/path#ignored # heading"
    result = hashtags(raw)
    assert [x[0] for x in result] == ["#café", "#uk_garage", "#東京"]
    assert [raw[a:b] for _, a, b in result] == ["#Cafe\u0301", "#UK_Garage", "#東京"]


@pytest.mark.parametrize("raw,expected,rounded", [(None,None,False), (0,0,False), ("0",0,False),
    ("12,345",12345,False), ("2.5m",2500000,True), ("2.15 G",2150000000,True),
    ("3K+ posts",3000,True), ("12+",12,True), ("unavailable",None,True),
    ("1,2 млн",None,True)])
def test_display_counts_retain_precision(raw, expected, rounded):
    assert displayed_count(raw) == (expected, rounded)


@pytest.mark.parametrize("raw", [True, -1, float("inf"), float("nan"), {}, "junk"])
def test_bad_counts(raw):
    with pytest.raises(OnlineSourceError):
        count(raw)


def test_provider_missing_count_is_not_zero():
    assert count(-1, sentinel=True) is None
    assert count(0, sentinel=True) == 0
    row = media_row({"id":"1", "caption":"#x", "likesCount":-1, "commentsCount":0}, 0, provider="apify:test")
    assert row.metrics["likes"] is None and row.metrics["comments"] == 0


@pytest.mark.parametrize("raw", ["2026-10-02", "2026-10-02T12:00:00Z", "2026-10-02T13:00:00+01:00", 0])
def test_valid_timestamps_are_timezone_aware(raw):
    assert timestamp(raw).tzinfo is not None


@pytest.mark.parametrize("raw", ["2026-10-02T12:00:00", "bad", True, {}, float("nan"), 1e30])
def test_invalid_timestamps_fail(raw):
    with pytest.raises(InputError):
        timestamp(raw)


def test_permalink_drops_query_and_refuses_private_host():
    assert permalink("https://instagram.com/p/AbC/?utm_source=test") == "https://www.instagram.com/p/AbC/"
    for url in ("https://instagram.com.evil.test/p/abc", "http://instagram.com/p/abc",
                "https://token@instagram.com/p/abc", "https://instagram.com:8443/p/abc", "/p/abc"):
        assert permalink(url) is None


def test_deduplicate_by_id_not_link_or_detector_and_retain_first_count():
    items, meta = sample([
        {"id":"1", "caption":"#a #a #b", "likes":0, "comments_count":2},
        {"id":"1", "caption":"#a #b", "permalink":"https://instagram.com/p/code/", "likes":9},
        {"id":"2", "caption":"#a #c", "likes":10, "comments_count":None},
        {"id":"3", "caption":"#b", "likes":None, "comments_count":8},
    ])
    tags = {i.phrase:i for i in items}
    ev = values(tags["#a"])
    assert ev["sampled_post_count"] == 2
    assert ev["sample_likes_mean"] == 5
    assert ev["sample_likes_plus_comments_median"] == 2  # Complete pairs only, not 5+2.
    assert ev["engagement_observed_posts"] == 1
    assert meta["duplicates_removed"] == 1
    assert meta["conflicting_counts_retained_first"] == 1
    assert ev["sample_posts_last_24h"] is None
    related = {v["hashtag"]:v for v in tags["#a"].metadata["cooccurring_hashtags"]}
    assert related["#b"]["shared_posts"] == 1
    assert related["#b"]["conditional_sample_fraction"] == .5
    assert related["#b"]["sample_jaccard"] == pytest.approx(1/3)
    assert all(i.score is None for i in items)


def test_timestamp_windows_exclude_unknown_and_future():
    items, meta = sample([
        {"id":"1", "caption":"#a", "timestamp":"2026-10-02T11:00:00Z"},
        {"id":"2", "caption":"#a", "timestamp":"2026-09-30T11:00:00Z"},
        {"id":"3", "caption":"#a", "timestamp":"2026-10-03T11:00:00Z"},
        {"id":"4", "caption":"#a"},
    ])
    ev = values(items[0])
    assert ev["sample_posts_last_24h"] == 1
    assert ev["sample_posts_last_7d"] == 2
    assert ev["timestamp_observed_posts"] == 2
    assert meta["undated_media"] == meta["future_dated_media"] == 1


def test_followers_use_available_complete_pairs_only():
    items, _ = sample([
        {"caption":"#x", "likes":8, "comments_count":2, "followers":100},
        {"caption":"#x", "likes":30, "comments_count":10, "followers":0},
        {"caption":"#x", "likes":30, "followers":100},
    ])
    ev = values(items[0])
    assert ev["sample_follower_engagement_percent_mean"] == 10


def test_known_but_unresolved_tag_and_unavailable_metrics():
    items, _ = sample([], known={"#a":"unresolved-by-api"})
    assert items[0].metadata["verification"] == "unresolved-by-api"
    assert values(items[0])["sampled_post_count"] == 0
    assert values(items[0])["sample_likes_mean"] is None
    assert "banned" not in items[0].metadata


def test_reference_and_comment_text_do_not_claim_post_metrics():
    for kind, status in (("reference", "reference-only"), ("comments", "observed-in-sample")):
        items, _ = sample([{"text":"#music paper planes, city at night"}], sample_kind=kind, include_keywords=True)
        tag = next(i for i in items if i.phrase == "#music")
        assert values(tag) == {"sampled_text_count":1}
        assert tag.metadata["verification"] == status
        phrases = {i.phrase for i in items}
        assert "paper planes" in phrases
        assert "planes city" not in phrases
        assert "city night" not in phrases
        assert "#paperplanes" not in phrases


def test_representative_examples_bounded_related_limit():
    items, _ = sample([{"caption":"#a #b #c"}] * 20, related_limit=1)
    assert len(items[0].metadata["occurrence_examples"]) == 10
    assert items[0].metadata["related_truncated"]
    assert len(items[0].metadata["cooccurring_hashtags"]) == 1


def test_personal_export_and_comment_array_shape():
    row = media_row({"title":"#Brighton", "media":[{"creation_timestamp":0}], "comments":[]}, 0)
    assert row.tags == {"#brighton"}
    assert row.metrics["comments"] is None and row.published == timestamp(0)


@pytest.mark.parametrize("row", [{"caption":True}, {"caption":"x", "hashtags":"bad"},
    {"caption":"x", "media":[None]}, {"caption":"x", "id":True}, {"error":"login required"}])
def test_bad_media_rows(row):
    with pytest.raises((InputError, OnlineSourceError)):
        media_row(row, 0)


def test_provider_groups_and_rounded_precision():
    result = stats_candidates([{"name":"music", "postsCount":123456, "postsPerDay":"0",
        "related":[{"hash":"#UKG", "info":"1.2m"}], "rare":["#localmusic"]}], "fixture", OBSERVED)
    assert values(result[0])["reported_post_count"] == 123456
    assert values(result[0])["provider_posts_per_day"] == 0
    assert values(result[1])["reported_post_count"] == 1200000
    assert result[1].metadata["count_is_approximate"]
    assert values(result[2])["reported_post_count"] is None


@pytest.mark.parametrize("row", [{"name":"x","availability":"unavailable","post_count":0},
    {"name":"x","post_count_display":"1m","count_is_approximate":"false"},
    {"name":"x","availability":"banned"}, {"name":"x","related":{}},
    {"name":"x","related":[None]}, {"error":"unavailable"}])
def test_stats_reject_misleading_values(row):
    with pytest.raises((InputError, OnlineSourceError)):
        stats_candidates([row], "fixture", OBSERVED)


def snapshot(value, date, **row_options):
    rows = stats_candidates([{"name":"music", "post_count":value, **row_options}], "fixture", date)
    return {"plugin":"instagram", "metadata":{"comparison_scope":{"source":"fixture","scope":"GB"},
            "observed_at":date}, "keywords":json.loads(json.dumps([asdict(i) for i in rows]))}


def test_comparable_snapshot_net_change_and_zero_denominator():
    for a, b, delta, percent in ((100,120,20,20), (100,90,-10,-10), (0,10,10,None)):
        items = compare(snapshot(a,"2026-10-01"), snapshot(b,"2026-10-03"), "reported_post_count")
        ev = values(items[0])
        assert ev["reported_post_count_delta"] == delta
        assert ev["reported_post_count_delta_per_day"] == delta/2
        assert ev["reported_post_count_percent_change"] == percent


def test_compare_skips_unknown_and_rounded_counts():
    for value in (None, "1.2m"):
        assert compare(snapshot(value,"2026-10-01"), snapshot(20,"2026-10-02"), "reported_post_count") == []


def test_compare_rejects_scope_date_or_structure_mismatch():
    before, after = snapshot(1,"2026-10-01"), snapshot(2,"2026-10-02")
    after["metadata"]["comparison_scope"]["scope"] = "US"
    with pytest.raises(InputError,match="comparison_scope"):
        compare(before, after, "reported_post_count")
    with pytest.raises(InputError,match="oldest"):
        compare(before, before, "reported_post_count")
    after = snapshot(2,"2026-10-02")
    after["keywords"] = [None]
    with pytest.raises(InputError):
        compare(before,after,"reported_post_count")


def test_conflicting_snapshot_evidence_is_not_arbitrarily_selected():
    before, after = snapshot(1,"2026-10-01"), snapshot(2,"2026-10-02")
    extra = dict(after["keywords"][0]["evidence"][0],value=3)
    after["keywords"][0]["evidence"] = [*after["keywords"][0]["evidence"], extra]
    with pytest.raises(InputError, match="conflicting"):
        compare(before, after, "reported_post_count")


def test_unbounded_display_string_is_rejected():
    with pytest.raises(InputError):
        displayed_count("9" * 300)
