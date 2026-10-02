"""Synthetic, deterministic evidence tests; no claim of measured YouTube demand."""
import json
from dataclasses import replace

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import KeywordCandidate, KeywordEvidence
from keywordmoves.online import youtube_analysis as a

DATE = "2026-10-02T12:00:00+00:00"
VID = "AAAAAaaaa01"


def run(rows, **o):
    return a.analyse(rows, o, source="synthetic", scope="test-sample", observed_at=DATE)


def video(i=VID, **fields):
    return {"id": i, "title": "Paper planes #UKGarage", "description": "#Brighton #UKGarage", **fields}


def values(item):
    return {e.metric: e.value for e in item.evidence}


@pytest.mark.parametrize("value", [True, False, -1, 1.5, float("nan"), float("inf"), 1e20, "-1", "2.5M", "1,000", {}, [], "1e7", "9" * 40])
def test_invalid_exact_counts(value):
    with pytest.raises(InputError):
        a.count(value)


@pytest.mark.parametrize("value,expected", [(0, 0), (None, None), ("", None), ("0", 0), ("9007199254740993", 9007199254740993), ("42.00", 42)])
def test_integer_precision(value, expected):
    assert a.count(value) == expected


@pytest.mark.parametrize("value,expected", [("1.2M", (1200000, True)), ("1,234", (1234, False)), ("4K", (4000, True)), ("2.1B", (2100000000, True)), (None, (None, False))])
def test_display_counts(value, expected):
    assert a.display_count(value) == expected


@pytest.mark.parametrize("value", ["2026-10-02T12:00:00", "yesterday", "2026-33-44", None, True])
def test_ambiguous_times_rejected(value):
    with pytest.raises(InputError):
        a.timestamp(value)


@pytest.mark.parametrize("value", ["music rock", "#", "foo/bar", "x" * 101, None, "#bad-tag"])
def test_hashtag_requires_literal(value):
    with pytest.raises(InputError):
        a.hashtag(value)


def test_unicode_hashtags_and_offsets():
    s = "C#code https://example.test/#notatag #Musique #café #東京 #UK_Garage"
    spans = a.hashtag_spans(s)
    assert [p for p, _, _ in spans] == ["#musique", "#café", "#東京", "#uk_garage"]
    assert all(a.hashtag(s[x:y]) == p for p, x, y in spans)


@pytest.mark.parametrize("value", [VID, "https://youtu.be/" + VID, "https://www.youtube.com/watch?v=" + VID, "https://www.youtube.com/shorts/" + VID, "https://youtube.com/live/" + VID])
def test_id_or_canonical_url(value):
    assert a.video_id(value) == VID


@pytest.mark.parametrize("value", ["shortid", "http://youtu.be/" + VID, "https://evil.test/watch?v=" + VID, "https://user@youtube.com/watch?v=" + VID, "https://youtube.com:999/watch?v=" + VID, "https://youtube.com/watch?v=" + VID + "&v=" + VID, "https://youtube.com/watch?v=" + VID + "#secret", "https://youtube.com/@handle"])
def test_invalid_video_url(value):
    with pytest.raises(ConfigurationError):
        a.video_id(value)


def test_tags_are_not_hashtags_and_duplicate_posts_not_inflated():
    v = video(tags=["UK Garage", "#UKGarage"], viewCount="1000", likeCount=50, commentCount=0)
    items, meta = run([v, v, video("BBBBBbbbb02", viewCount=0, likeCount=None, commentCount=3)])
    by = {(i.phrase, i.metadata["kind"]): i for i in items}
    h = by[("#ukgarage", "hashtag")]
    assert values(h)["sample_record_count"] == 2
    assert values(h)["occurrences"] == 4
    assert values(h)["sample_views_sum"] == 1000
    assert values(h)["sample_likes_available"] == 1
    assert values(h)["sample_likes_comments_per_view"] == 0.05
    assert ("#ukgarage", "video-tag") in by and ("uk garage", "video-tag") in by
    assert meta["duplicate_records"] == 1
    assert h.score is None
    assert h.metadata["cooccurring_hashtags"][0]["hashtag"] == "#brighton"
    assert h.metadata["cooccurring_hashtags"][0]["jaccard"] == 1


def test_never_join_words_over_stopwords_punctuation_lines_hashtags():
    terms = [p for p, _, _ in a.terms("paper and planes. bright\ncity #hello new music", 3, a.STOPWORDS)]
    assert "paper planes" not in terms and "planes bright" not in terms and "bright city" not in terms
    assert "hello" not in terms and "city new" not in terms
    assert "new music" in terms


def test_sample_coverage_not_inferred_and_rounding_preserved():
    items, meta = run([video(viewCount="2.1M", published_at="2026-10-01", content_type="shorts"),
                       video("BBBBBbbbb02", published_at="2026-10-03", viewCount=None)])
    h = next(i for i in items if i.phrase == "#brighton")
    assert values(h)["sample_views_available"] == 0
    assert values(h)["sample_views_sum"] is None
    assert values(h)["sample_published_last_7d"] == 1
    assert h.metadata["supporting_records"][0]["approximate_counters"] == ["views"]
    assert "not a population" in meta["completeness"]
    assert a.normalise(video(description="#shorts", duration="PT15S"))["content_type"] == "unknown"


def test_api_extra_statistics_are_opt_in_and_permission_gated():
    i, m = a.analyse([video(viewCount=100)], {}, source="YouTube Data API", scope="api", observed_at=DATE, api_data=True)
    assert not m["derived_statistics_enabled"]
    assert "sample_views_sum" not in values(i[0])
    assert "2026-11-01" in m["api_refresh_or_delete_by"]
    with pytest.raises(ConfigurationError):
        a.analyse([video()], {"derive_metrics": True}, source="api", scope="api", observed_at=DATE, api_data=True)
    _, m = a.analyse([video()], {"derive_metrics": True, "api_derived_metrics_accepted": True}, source="api", scope="api", observed_at=DATE, api_data=True)
    assert m["derived_statistics_enabled"]


def test_metadata_bounds_and_conflict_reporting():
    v = video()
    items, meta = run([v, {**v, "views": 100}, video("BBBBBbbbb02")], max_occurrences=1, include_keywords=False)
    assert meta["conflicting_duplicate_ids"] == [VID]
    h = items[0]
    assert h.metadata["occurrences_truncated"] and h.metadata["supporting_records_truncated"]
    with pytest.raises(InputError, match="Candidate"):
        run([v], max_candidates=1)
    _, m = run([video(description=" ".join(f"#tag{i}" for i in range(61)))])
    assert m["hashtag_over_60_record_ids"] == [VID]


@pytest.mark.parametrize("change", [{"platform": "TikTok"}, {"error": "failed"}, {"snippet": []}, {"statistics": []}, {"tags": "one,two"}, {"tags": [42]}, {"contentDetails": 2}])
def test_malformed_video(change):
    with pytest.raises(InputError):
        a.normalise(video(**change))


def snapshot(initial, when="2026-10-01", **extra):
    row = {"phrase": "#UKGarage", "metric": "reported_video_count", "unit": "videos", "value": initial, **extra}
    items = a.observations([row], source="synthetic", scope="same-settings", observed_at=when)
    return a.finish("import-observations", items, {}, {}).to_dict()


def test_compare_exact_compatible_counts_and_zero_baseline():
    result = a.compare(snapshot(0), snapshot(200, "2026-10-03"), {})
    assert values(result[0])["reported_video_count_net_change"] == 200
    assert values(result[0])["reported_video_count_net_change_per_day"] == 100
    assert values(result[0])["reported_video_count_percent_change"] is None
    assert a.compare(snapshot("2M"), snapshot("3M", "2026-10-03"), {}) == []
    assert a.compare(snapshot(3), snapshot(4, "2026-10-03", country="US"), {}) == []
    assert a.compare(snapshot(3, definition="old"), snapshot(4, "2026-10-03", definition="new"), {}) == []


def test_compare_duplicate_unscoped_invalid_or_backwards_rejected():
    x = snapshot(3)
    for before, after in [({}, x), (x, x), (snapshot(3, "2026-10-03"), x)]:
        with pytest.raises(InputError):
            a.compare(before, after, {})
    x["keywords"] *= 2
    with pytest.raises(InputError):
        a.compare(x, snapshot(4, "2026-10-03"), {})


@pytest.mark.parametrize("extra", [{"approximate": "true"}, {"value": True}, {"value": float("nan")}, {"metric": ""}, {"unit": ""}, {"kind": "unknown"}, {"availability": "unavailable", "value": 0}, {"platform": "Instagram"}])
def test_invalid_observation(extra):
    with pytest.raises(InputError):
        snapshot(4, **extra)


def test_finish_sort_and_truncation():
    items = [KeywordCandidate(w, evidence=(KeywordEvidence("s", "reported_video_count", v),)) for w, v in (("b", None), ("c", 2), ("a", 4))]
    r = a.finish("test", items, {"sort_by": "reported_video_count", "limit": 2}, {})
    assert [i.phrase for i in r.keywords] == ["a", "c"]
    assert r.metadata["output_truncated"]
    assert json.loads(json.dumps(r.to_dict()))["metadata"]["platform"] == "YouTube"
    conflicting = replace(items[2], evidence=items[2].evidence + (KeywordEvidence("s", "reported_video_count", 3),))
    assert a.finish("x", [conflicting, items[1]], {"sort_by": "reported_video_count"}, {}).keywords[0].phrase == "c"
