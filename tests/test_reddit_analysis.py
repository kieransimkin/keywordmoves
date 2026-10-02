"""Literal extraction and sample denominators: no credentials, downloads or network."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online import reddit_analysis as a
from keywordmoves.online.reddit import RedditPlugin

CTX = a.context("synthetic", "same-sample", "2026-10-02T12:00:00Z")


def post(identity="t3_abc123", **extra):
    return {"id": identity, "kind": "t3", "title": "Paper planes #UKGarage",
            "body": "Midnight music.", "subreddit": "Music", "score": 10, "num_comments": 5,
            "created_at": "2026-10-01T12:00:00Z", **extra}


def values(result, phrase="paper planes", relationship="reddit-keyword"):
    row = next(k for k in result.keywords if k.phrase == phrase and k.relationship == relationship)
    return row, {e.metric: e.value for e in row.evidence}


@pytest.mark.parametrize("value", [None, "", 0, "0", 1, "2.5", -3, 2**64])
def test_signed_numbers_distinguish_missing_zero_and_integer_precision(value):
    expected = None if value in (None, "") else (float(value) if value == "2.5" else int(value))
    assert a.metric(value, signed=True) == expected


@pytest.mark.parametrize("value", [True, False, [], {}, "4k", "unknown", float("nan"), float("inf")])
def test_invalid_metrics(value):
    with pytest.raises(InputError):
        a.metric(value)


@pytest.mark.parametrize("value,kwargs", [(-1, {}), (1.01, {"ratio": True}), ("-2", {})])
def test_metric_ranges(value, kwargs):
    with pytest.raises(InputError):
        a.metric(value, **kwargs)


@pytest.mark.parametrize("value", [True, "2026-10-02T12:00:00", "nonsense", float("inf"), 10**40])
def test_timestamp_rejects_ambiguous_and_invalid(value):
    with pytest.raises(InputError):
        a.timestamp(value)


def test_timestamp_date_timezone_epoch_and_missing():
    assert a.timestamp("2026-10-02") == "2026-10-02T00:00:00Z"
    assert a.timestamp("2026-10-02T13:00:00+01:00") == CTX["observed_at"]
    assert a.timestamp(0) == "1970-01-01T00:00:00Z"
    assert a.timestamp(None) is None
    with pytest.raises(InputError):
        a.timestamp(None, required=True)


@pytest.mark.parametrize("value", ["../secret", "t2_user", "t3_", "a?x=y", "T3_ABC", True])
def test_fullname_path_validation(value):
    with pytest.raises(ConfigurationError):
        a.fullname(value)


def test_long_comment_ids_and_kind():
    assert a.fullname("19gsnavtu46ip", "t1") == "t1_19gsnavtu46ip"
    with pytest.raises(ConfigurationError):
        a.fullname("t1_xyz", "t3")


@pytest.mark.parametrize("value", ["r/a", "r/u_person", "https://reddit.com/r/music", "music+ukgarage", "../bad"])
def test_subreddit_validation(value):
    with pytest.raises(ConfigurationError):
        a.subreddit(value)


def test_safe_urls_strip_credentials_and_tracking():
    assert a.safe_url("/r/Music/comments/abc/title/?context=2") == "https://www.reddit.com/r/Music/comments/abc/title/"
    assert a.safe_url("https://example.org/?token=secret#hash") == "https://example.org/"
    assert a.safe_url("https://user:pass@example.org") is None
    assert a.safe_url("javascript:alert(1)") is None
    assert a.safe_url("https://[broken") is None
    with pytest.raises(InputError):
        a.safe_url(42)


def test_literal_words_no_bridging_mentions_hashes_and_markdown():
    result = a.extract("Paper and planes. Midnight, sky\n# Heading\n#UKGarage r/Music /u/person\n"
                       "`hidden code`\n```other hidden```\n> quoted text\n"
                       "[Brighton](https://private.test/secret) https://private.test/noise", {})
    assert ("keyword", "paper planes") not in result
    assert ("keyword", "midnight sky") not in result
    assert result[("hashtag", "#ukgarage")] == 1
    assert result[("community-mention", "r/music")] == 1
    assert ("hashtag", "#heading") not in result
    assert result[("keyword", "brighton")] == 1
    assert not any(any(w in phrase for w in ("hidden", "secret", "person", "quoted")) for _, phrase in result)


def test_unicode_stopwords_and_boundaries():
    words = a.extract("Café music! #日本語 r/Music\nPaper planes.", {"stopwords": "music"})
    assert ("hashtag", "#日本語") in words
    assert ("keyword", "café") in words
    assert ("keyword", "café music") not in words
    assert ("keyword", "planes café") not in words
    assert a.extract("#music #music plain text r/music", {"include_keywords": False})[("hashtag", "#music")] == 2
    assert not any(kind == "hashtag" for kind, _ in a.extract("#music", {"include_hashtags": False}))


def test_duplicate_detection_separate_post_comment_samples_and_denominators():
    first = post()
    second = post("t3_def456", score=None, num_comments=0, upvote_ratio=0, created_at=None)
    comment = {"kind": "t1", "id": "t1_comment1", "body": "Paper planes", "score": -2,
               "subreddit": "Music", "created_at": "2026-09-30T12:00:00Z"}
    result = a.analyse([first, first, second, comment], {"limit": 100}, CTX)
    item, stats = values(result)
    assert stats["document_frequency"] == 3
    assert stats["occurrences"] == 3
    assert stats["sample_post_score_observed"] == 1
    assert stats["sample_post_score_median"] == 10
    assert stats["sample_post_comments_mean"] == 2.5
    assert stats["sample_comment_score_median"] == -2
    assert stats["sample_posts_7d"] == 1
    assert stats["sample_comments_7d"] == 1
    assert item.score is None
    assert item.metadata["cooccurrence"]
    assert "records" not in result.metadata


def test_score_hidden_null_is_not_zero():
    result = a.analyse([post(score_hidden=True, score=200)], {}, CTX)
    _, stats = values(result)
    assert stats["sample_post_score_median"] is None
    assert stats["sample_post_score_observed"] == 0
    assert stats["sample_post_comments_median"] == 5


def test_deletion_overrides_prior_and_later_duplicates():
    deleted = post(body="[deleted]")
    result = a.analyse([post(), deleted, post()], {"include_records": True}, CTX)
    assert result.keywords == ()
    assert result.metadata["records"] == []
    assert result.metadata["removed_ids"] == ["t3_abc123"]


@pytest.mark.parametrize("extra", [{"body": "[removed]"}, {"removed": True}, {"deleted": True},
                                     {"removed_by_category": "moderator"}])
def test_removed_content_is_not_analyzed(extra):
    assert a.normalise(post(**extra)) is None


def test_conflicting_snapshots_flagged_without_combining_scores():
    result = a.analyse([post(score=5), post(score=10)], {"include_records": True}, CTX)
    assert result.metadata["conflicting_ids"] == ["t3_abc123"]
    assert values(result)[1]["sample_post_score_median"] == 5


def test_nsfw_stickied_and_future_timestamps():
    result = a.analyse([post(), post("t3_def", over_18=True), post("t3_ghi", stickied=True),
                        post("t3_jkl", created_at="2026-11-01")], {"exclude_stickied": True}, CTX)
    _, stats = values(result)
    assert stats["sample_post_count"] == 2
    assert stats["sample_posts_7d"] == 1
    assert result.metadata["excluded_records"] == 2


def test_normalized_records_drop_author_and_preserve_sources():
    row = post(author="private_name", author_fullname="t2_abc", author_flair_text="private",
               permalink="/r/Music/comments/abc123/", crosspost_parent="t3_def456")
    result = a.analyse([row], {"include_records": True}, CTX)
    encoded = json.dumps(result.to_dict())
    assert "private_name" not in encoded and "t2_abc" not in encoded
    assert result.metadata["records"][0]["crosspost_parent"] == "t3_def456"
    assert result.metadata["records"][0]["permalink"].startswith("https://www.reddit.com/")


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": True}, {"limit": 1.1}, {"limit": "oops"}, {"limit": 1001},
    {"max_words": 6}, {"min_words": 3, "max_words": 2}, {"max_records": 0},
    {"max_candidates": 0}, {"max_text_chars": 0}, {"min_occurrences": 0},
    {"include_keywords": "yes"}, {"include_records": 1}, {"stopwords": []},
    {"cooccurrence_limit": 101}, {"include_keywords": False, "max_words": 0},
])
def test_validate_even_before_network_or_empty_results(options):
    with pytest.raises(ConfigurationError):
        RedditPlugin().run(PluginRequest("search", keywords=("music",), options=options), ExecutionContext(None))


@pytest.mark.parametrize("options", [{"max_records": 1}, {"max_text_chars": 3}, {"max_candidates": 1}])
def test_analysis_resource_bounds_are_not_silent_truncation(options):
    with pytest.raises(InputError):
        a.analyse([post(), post("t3_other")], options, CTX)


def test_feature_selection_and_result_limit():
    result = a.analyse([post(flair="Original music")], {"include_keywords": False, "limit": 1}, CTX)
    assert result.metadata["output_truncated"]
    assert len(result.keywords) == 1
    raw = a.analyse([post(flair="Original music")], {"include_flair": False, "sort_by": "phrase"}, CTX)
    assert not any(k.relationship == "reddit-flair" for k in raw.keywords)
    assert [k.phrase for k in raw.keywords] == sorted(k.phrase for k in raw.keywords)
    assert a.signature({}) == a.signature({"include_keywords": True})


def test_competition_only_matching_post_scores_not_comment_or_search_totals():
    other = post("t3_other", title="Other topic", body="", score=10000, url="https://other.example/")
    report = a.competition([post(), other], "paper planes", {}, CTX)
    metrics = {e.metric: e.value for e in report.keywords[0].evidence}
    assert metrics["sample_posts"] == 2
    assert metrics["literal_matching_posts"] == 1
    assert metrics["matching_score_median"] == 10
    assert report.keywords[0].score is None
    assert report.keywords[0].metadata["linked_hosts"] == {"other.example": 1}


def test_community_counts_are_scoped_and_null_is_distinct():
    rows = [{"kind": "t5", "data": {"display_name": "Music", "subscribers": 500, "accounts_active": None}},
            {"kind": "t5", "data": {"display_name": "Music", "subscribers": 600}},
            {"kind": "t5", "data": {"display_name": "Other", "over18": True}}]
    result = a.communities(rows, CTX, {}, "community")
    assert len(result.keywords) == 1
    assert result.keywords[0].phrase == "r/music"
    assert result.keywords[0].evidence[1].value is None
    with pytest.raises(InputError):
        a.communities([{"display_name": "Music", "over18": "false"}], CTX, {}, "community")


@pytest.mark.parametrize("row", [[], {"kind": "t2", "id": "user"},
                                  {"kind": "t3", "data": "bad"}, post(score_hidden=1), post(title=42)])
def test_malformed_records(row):
    with pytest.raises((InputError, ConfigurationError)):
        a.normalise(row)


def test_inputs_are_not_mutated():
    rows = [post()]
    backup = deepcopy(rows)
    a.analyse(rows, {"include_records": True}, CTX)
    assert rows == backup
