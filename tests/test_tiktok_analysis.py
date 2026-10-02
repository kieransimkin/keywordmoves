"""Synthetic TikTok evidence tests; no network, models, or live popularity claims."""
from __future__ import annotations

from copy import deepcopy

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.online.tiktok_analysis import (
    analyse,
    compare,
    count,
    displayed_count,
    finish,
    hashtag,
    hashtag_spans,
    identifier,
    normalise_video,
    observations,
    timestamp,
    video_url,
)

NOW = "2026-10-02T12:00:00+00:00"


def values(item):
    return {e.metric: e.value for e in item.evidence}


def by_phrase(items):
    return {item.phrase: item for item in items}


@pytest.mark.parametrize("value,expected", [("#UKGarage", "#ukgarage"), (" café ", "#café"), ("cafe\u0301", "#café"), ("音楽", "#音楽"), ("a_b2", "#a_b2")])
def test_unicode_normalisation(value, expected):
    assert hashtag(value) == expected


@pytest.mark.parametrize("value", ["", "#", "two words", "one-two", "https://tiktok.com", "##tag", True, 42, "x" * 101, "😀"])
def test_invalid_literal_tags(value):
    with pytest.raises(InputError):
        hashtag(value)


def test_hashtag_occurrences_and_boundaries():
    body = "#Music #MUSIC and #cafe\u0301. not#tag ##no @person"
    found = hashtag_spans(body)
    assert [v[0] for v in found] == ["#music", "#music", "#café"]
    assert all(body[a:b].startswith("#") for _, a, b in found)


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), (0, 0), ("0", 0), (2.0, 2), ("9223372036854775807", 9223372036854775807)])
def test_exact_counts(value, expected):
    assert count(value) == expected


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "unknown", "-1", float("nan"), float("inf"), float(2**60), [], {}])
def test_count_failures(value):
    with pytest.raises(InputError):
        count(value)


@pytest.mark.parametrize("value,expected", [("1.2M", (1200000, True)), ("3K", (3000, True)), ("2.5b", (2500000000, True)), ("1,234", (1234, False)), (None, (None, False)), (0, (0, False))])
def test_compact_counts_are_marked_approximate(value, expected):
    assert displayed_count(value) == expected


def test_identifiers_do_not_lose_64_bit_precision():
    assert identifier(9223372036854775807) == "9223372036854775807"
    with pytest.raises(InputError):
        identifier(1.5)
    with pytest.raises(InputError):
        identifier("bad\nvalue")


@pytest.mark.parametrize("value", [None, True, "not-date", "2026-10-01T12:00:00", 10**25])
def test_dates_reject_ambiguous_or_invalid_values(value):
    with pytest.raises(InputError):
        timestamp(value)


def test_dates_have_explicit_timezone():
    assert timestamp("2026-10-02").isoformat() == "2026-10-02T00:00:00+00:00"
    assert timestamp("2026-10-02T13:00:00+01:00").isoformat() == NOW
    assert timestamp(0).year == 1970


@pytest.mark.parametrize("url", ["http://www.tiktok.com/@a/video/1", "https://evil.example/@a/video/1", "https://vm.tiktok.com/abc/", "https://user:pass@www.tiktok.com/@a/video/1", "https://www.tiktok.com:444/@a/video/1", "https://www.tiktok.com/@a/video/1#secret", "https://www.tiktok.com/@a/video/no"])
def test_video_url_restrictions(url):
    with pytest.raises(ConfigurationError):
        video_url(url)


def test_query_tracking_is_removed_from_video_url():
    assert video_url("https://www.tiktok.com/@a/video/1?tracking=x") == "https://www.tiktok.com/@a/video/1"


def test_native_apify_and_research_aliases():
    row = normalise_video({"id": 9223372036854775807, "text": "#Music", "hashtags": [{"id": "1", "name": "fyp", "title": "For You"}],
                          "playCount": 100, "diggCount": 0, "collectCount": 3, "repostCount": 1,
                          "authorMeta": {"id": "42"}, "musicMeta": {"musicId": "123", "musicName": "Synthetic tune"},
                          "videoMeta": {"duration": 12.5}, "isSlideshow": True})
    assert row["tags"] == {"#music", "#fyp"}
    assert row["metrics"]["likes"] == 0 and row["metrics"]["comments"] is None
    assert row["metrics"]["saves"] == 3 and row["format"] == "slideshow"
    assert row["duration"] == 12.5
    research = normalise_video({"video_id": 123, "video_description": "night #music", "hashtag_names": ["ukg"],
                                "favorites_count": 4, "voice_to_text": "a transcript", "music_id": 99, "region_code": "GB"})
    assert research["metrics"]["saves"] == 4 and research["music_id"] == "99"
    assert research["tags"] == {"#music", "#ukg"}


@pytest.mark.parametrize("row", [{"error": "private"}, {"errorCode": "NOT_FOUND"}, {"stats": []}, {"author": "someone"}, {"hashtags": "#a"}, {"hashtags": [{"title": "not a name"}]}, {"view_count": -1}, {"caption": True}, {"created_at": "invalid"}, {"duration": -1}])
def test_malformed_video_does_not_invent_empty_results(row):
    with pytest.raises(InputError) as error:
        normalise_video(row)
    assert error.value is not None


def test_dedup_counts_missing_values_and_engagement_denominators():
    rows = [
        {"id": "1", "caption": "#music #ukg #music", "view_count": 100, "like_count": 10, "comment_count": 2, "share_count": 3, "music_id": "9", "username": "a", "created_at": "2026-10-01"},
        {"id": "1", "caption": "#music #ukg #music", "view_count": 999, "favorites_count": 5},
        {"id": "2", "caption": "#music", "view_count": None, "like_count": 0, "created_at": "2026-08-01"},
        {"id": "3", "caption": "#other #ukg"},
    ]
    items, meta = analyse(rows, {}, "synthetic", NOW)
    music = by_phrase(items)["#music"]
    v = values(music)
    assert meta["unique_records"] == 3 and meta["duplicate_records"] == 1
    assert meta["conflicting_duplicate_fields"] == 1
    assert v["sampled_video_count"] == 2
    assert v["sample_video_views_sum"] == 100 and v["sample_video_likes_median"] == 5
    assert v["sample_video_saves_sum"] == 5
    assert v["sample_interactions_per_view"] == .15
    assert music.metadata["metric_available_records"]["views"] == 1
    assert music.metadata["complete_engagement_records"] == 1
    assert v["sample_videos_last_7_days"] == 1
    assert "reported_post_count" not in v
    peer = music.metadata["cooccurring_hashtags"][0]
    assert peer["hashtag"] == "#ukg" and peer["shared_records"] == 1
    assert peer["jaccard"] == pytest.approx(1 / 3)
    assert peer["conditional_ratio"] == .5 and peer["lift"] == .75
    assert music.metadata["sounds"] == [{"music_id": "9", "sampled_records": 1}]
    assert music.metadata["distinct_sample_authors"] == 1


def test_no_id_does_not_merge_unrelated_equal_captions():
    items, meta = analyse([{"caption": "#music"}, {"caption": "#music"}], {}, "synthetic", NOW)
    assert values(items[0])["sampled_video_count"] == 2
    assert meta["missing_record_ids"] == 2
    assert values(items[0])["sample_videos_last_7_days"] is None


def test_known_seed_is_not_declared_banned_or_global_zero():
    items, meta = analyse([], {}, "synthetic", NOW, known=("missing",))
    assert items[0].metadata["verification"] == "not-observed-in-this-sample"
    assert values(items[0])["sampled_video_count"] == 0
    assert "reported_post_count" not in values(items[0])
    assert not meta["population_total_known"]


def test_comment_likes_do_not_become_video_hashtag_engagement():
    items, _ = analyse([{"id": "c1", "text": "great #music", "like_count": 5, "view_count": 900}], {}, "synthetic comments", NOW, kind="comments")
    music = by_phrase(items)["#music"]
    assert music.metadata["verification"] == "observed-in-comments"
    assert values(music)["sample_comment_likes_sum"] == 5
    assert "sampled_video_count" not in values(music)
    assert "sample_interactions_per_view" not in values(music)


def test_literal_caption_keywords_do_not_cross_stopwords_lines_punctuation_or_masks():
    rows = [{"caption": "paper and planes. night\ncity; warm rain #music @someone lovely moon", "transcript": "spoken phrase"}]
    items, _ = analyse(rows, {"include_keywords": True}, "synthetic", NOW)
    words = by_phrase(items)
    assert "warm rain" in words and "paper planes" not in words and "night city" not in words
    assert "someone lovely" not in words and "music someone" not in words
    assert "spoken phrase" not in words
    items, _ = analyse(rows, {"include_keywords": True, "include_transcript": True}, "synthetic", NOW)
    assert by_phrase(items)["spoken phrase"].metadata["text_fields"] == ["transcript"]


def test_future_dates_zero_views_and_duration():
    items, _ = analyse([{"caption": "#a", "view_count": 0, "like_count": 0, "share_count": 0, "comment_count": 0,
                         "created_at": "2027-01-01", "duration": 2.5}], {}, "synthetic", NOW)
    assert values(items[0])["sample_interactions_per_view"] is None
    assert values(items[0])["sample_videos_last_7_days"] == 0
    assert items[0].metadata["future_timestamps"] == 1
    assert values(items[0])["sample_duration_seconds_mean"] == 2.5


def test_reported_period_and_total_counts_stay_separate():
    row = {"hashtagName": "music", "hashtagId": "91", "publishCnt": 20, "publishCntAll": 5000,
           "videoViews": 300, "videoViewsAll": "1.2M", "countryCode": "GB", "period": "7",
           "audienceInterests": [{"score": 142, "interestInfo": {"id": "x"}}]}
    item = observations([row], "synthetic", NOW, "same-filter", hashtags_only=True)[0]
    assert values(item)["reported_post_count"] == 5000
    assert values(item)["period_post_count"] == 20
    assert values(item)["reported_view_count"] == 1200000
    assert item.metadata["approximate_metrics"] == ["reported_view_count"]
    assert item.metadata["native_dimensions"]["audienceInterests"][0]["score"] == 142
    assert item.metadata["window"] == "7"
    assert all(e.geography == "GB" for e in item.evidence)


def test_observed_search_interest_does_not_become_a_hashtag_or_volume():
    item = observations([{"phrase": "night music", "metric": "search_popularity_index", "value": "0.7", "unit": "ratio"}],
                        "Creator Search Insights synthetic", NOW, "my-account:7d")[0]
    assert item.metadata["kind"] == "keyword" and item.phrase == "night music"
    assert values(item) == {"search_popularity_index": .7}


@pytest.mark.parametrize("row", [{"phrase": "x"}, {"phrase": "x", "metric": "views"}, {"phrase": "x", "metric": "views", "unit": "count", "value": -1},
                                 {"hashtag": "a", "availability": "unavailable", "metric": "views", "unit": "count", "value": 0},
                                 {"hashtag": "a", "reported_post_count": 1, "approximate_metrics": "not-array"}])
def test_observation_schema_failures(row):
    with pytest.raises(InputError):
        observations([row], "synthetic", NOW, "scope")


def snapshot(n, date, **row):
    items = observations([{"hashtag": "music", "reported_post_count": n, **row}], "synthetic", date, "same-scope", hashtags_only=True)
    return finish("import-hashtags", items, {}, {}, date).to_dict()


def test_comparison_net_change_and_zero_baseline():
    first = snapshot(100, "2026-10-01")
    second = snapshot(110, "2026-10-02")
    item = compare(first, second)[0]
    assert values(item)["reported_post_count_delta"] == 10
    assert values(item)["reported_post_count_delta_per_day"] == 10
    assert values(item)["reported_post_count_change_percent"] == 10
    assert values(compare(snapshot(0, "2026-10-01"), second)[0])["reported_post_count_change_percent"] is None
    assert compare(snapshot(120, "2026-10-01"), second)[0].metadata["decrease_observed"]


@pytest.mark.parametrize("change", ["scope", "geography", "date", "approximate", "duplicate", "platform", "schema", "null"])
def test_comparison_refuses_incompatible_or_unusable_evidence(change):
    first = snapshot(100, "2026-10-01")
    second = deepcopy(snapshot(120, "2026-10-02"))
    if change == "scope":
        second["keywords"][0]["metadata"]["scope"] = "different"
    elif change == "geography":
        second["keywords"][0]["evidence"][0]["geography"] = "FR"
    elif change == "date":
        second["keywords"][0]["evidence"][0]["observed_at"] = "2026-10-01"
    elif change == "approximate":
        second["keywords"][0]["metadata"]["approximate_metrics"] = ["reported_post_count"]
    elif change == "duplicate":
        second["keywords"] = [*second["keywords"], second["keywords"][0]]
    elif change == "platform":
        second["keywords"][0]["metadata"]["platform"] = "Instagram"
    elif change == "schema":
        second["metadata"]["schema"] = "old"
    else:
        second["keywords"][0]["evidence"][0]["value"] = None
    with pytest.raises(InputError):
        compare(first, second)


def test_explicit_metric_sort_leaves_missing_last_and_limit_is_reported():
    items = observations([{"hashtag": "a", "reported_post_count": None}, {"hashtag": "b", "reported_post_count": 0}, {"hashtag": "c", "reported_post_count": 10}], "synthetic", NOW, "scope", hashtags_only=True)
    result = finish("import-hashtags", items, {}, {"sort_by": "reported_post_count", "limit": 2}, NOW)
    assert [i.phrase for i in result.keywords] == ["#c", "#b"]
    assert result.metadata["output_truncated"] is True
    assert all(i.score is None for i in result.keywords)


def test_native_related_hashtags_are_discovery_not_inherited_popularity():
    rows = [{"hashtagName": "music", "publishCntAll": 100, "countryCode": "GB", "period": "7",
             "relatedHashtags": [{"hashtagName": "ukg", "hashtagId": "9"}, {"hashtagName": "music"}],
             "recList": [{"hashtagName": "ukg", "hashtagId": "9"}, {"hashtagName": "brighton", "hashtagId": "10"}]}]
    items = observations(rows, "synthetic", NOW, "scope", hashtags_only=True)
    assert [v.phrase for v in items] == ["#music", "#ukg", "#brighton"]
    related = items[1]
    assert related.metadata["parent_hashtag"] == "#music"
    assert related.metadata["hashtag_id"] == "9"
    assert values(related) == {"provider_relatedHashtags_position": 1, "provider_recList_position": 1}
    assert all(e.metric != "reported_post_count" for e in related.evidence)


def test_malformed_related_tag_list_is_not_silently_discarded():
    with pytest.raises(InputError):
        observations([{"hashtagName": "music", "publishCntAll": 1, "relatedHashtags": ["a"]}], "synthetic", NOW, "scope")
