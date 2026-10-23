import hashlib
import json

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import PluginRequest
from keywordmoves.registry import PluginRegistry


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def run(op, *paths):
    return PluginRegistry().get("hashtag-planner").run(PluginRequest(op, inputs=paths), None)


def fixture(tmp_path, platform="youtube", text="#ExampleSong #IndiePop #MusicVideo #Viral #ExampleSong", kind="public_sample"):
    capture = {"schema": "keywordmoves-hashtag-capture/v1", "subject": "Example Song", "platform": platform,
        "captured_at": "2026-10-23", "source": "Synthetic permitted example", "source_kind": kind,
        "geography": "GB", "window": "supplied snapshot", "records": [{"id": "one", "text": text, "reference": "example://one"}]}
    path = save(tmp_path / "capture.json", capture)
    pool = json.loads(json.dumps(run("discover", path).to_dict()))
    pool_path = save(tmp_path / "pool.json", pool)
    review = {"schema": "keywordmoves-hashtag-review/v1", "subject": "Example Song", "platform": platform,
        "reviewed_at": "2026-10-23", "planned_for": "2026-10-24", "asset_sha256": "a" * 64,
        "source_sha256": hashlib.sha256(pool_path.read_bytes()).hexdigest(),
        "native_check": {"state": "checked", "checked_at": "2026-10-23", "reference": "example://native", "notes": "Synthetic caller assertion, not a real platform check."},
        "decisions": [{"phrase": c["phrase"], "fit": "direct", "role": "niche", "safety": "clear", "priority": i + 1,
            "rationale": "Synthetic fit review."} for i, c in enumerate(pool["keywords"])]}
    return path, pool_path, pool, review


def plan(tmp_path, pool_path, review):
    return run("plan", pool_path, save(tmp_path / "review.json", review))


def final_copy(tmp_path, result, **changes):
    path = save(tmp_path / "plan.json", result.to_dict())
    meta = result.metadata
    copy = {"schema": "keywordmoves-hashtag-final-copy/v1", "subject": meta["target_subject"], "platform": meta["target_platform"],
        "asset_sha256": meta["asset_sha256"], "plan_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "checked_at": "2026-10-24", "caption": meta["caption_hashtags"], "native_topic": meta["native_topic"]}
    copy.update(changes)
    return path, save(tmp_path / "copy.json", copy)


def test_literal_discovery_counts_records_not_repeated_occurrences(tmp_path):
    path, _, pool, _ = fixture(tmp_path)
    assert next(c for c in pool["keywords"] if c["phrase"] == "#ExampleSong")["evidence"][0]["value"] == 1
    assert pool["metadata"]["record_count"] == 1
    assert run("discover", path).metadata["network_requests"] == 0
    assert all(c["score"] is None for c in pool["keywords"])


def test_urls_headings_and_embedded_identifiers_are_not_tags(tmp_path):
    _, _, pool, _ = fixture(tmp_path, text="https://example.com/#URL word#Embedded ##Heading # Heading #RealTag")
    assert [c["phrase"] for c in pool["keywords"]] == ["#RealTag"]


def test_unicode_numeric_leading_and_source_case_retained(tmp_path):
    _, _, pool, _ = fixture(tmp_path, text="#3DT #آزادی #Cafe\u0301 #CAFÉ")
    assert [c["phrase"] for c in pool["keywords"]] == ["#3DT", "#Café", "#آزادی"]


@pytest.mark.parametrize("text", ["#123", "#___"])
def test_nonportable_number_only_tags_fail(tmp_path, text):
    with pytest.raises(InputError):
        fixture(tmp_path, text=text)


def test_duplicate_capture_records_cannot_inflate_count(tmp_path):
    path, _, _, _ = fixture(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"].append(dict(data["records"][0]))
    with pytest.raises(InputError, match="Duplicate record"):
        run("discover", save(path, data))


def test_explicit_review_no_filler_generic_bait_rejected(tmp_path):
    _, pp, _, review = fixture(tmp_path)
    result = plan(tmp_path, pp, review)
    assert len(result.keywords) == 2  # same-role cap, no filler
    assert {r["reason"] for r in result.metadata["rejected"]} >= {"generic_reach_bait", "role_cap"}
    assert result.metadata["implementation_authorised"] is False


@pytest.mark.parametrize("platform,cap", [("youtube", 3), ("instagram", 5), ("tiktok", 5), ("facebook", 2), ("x", 2), ("bluesky", 2), ("mastodon", 3), ("linkedin", 3)])
def test_platform_editorial_ceilings(tmp_path, platform, cap):
    _, pp, _, review = fixture(tmp_path, platform, "#Alpha #Beta #Gamma #Delta #Epsilon #Zeta")
    for row, role in zip(review["decisions"], ["brand", "niche", "niche", "topic", "topic", "format"]):
        row["role"] = role
    assert len(plan(tmp_path, pp, review).keywords) == cap


@pytest.mark.parametrize("platform", ["threads", "pinterest", "reddit", "wordpress", "spotify", "amazon-music", "apple-music", "bandcamp", "soundcloud"])
def test_native_fields_receive_no_hashtag_block(tmp_path, platform):
    _, pp, _, review = fixture(tmp_path, platform)
    assert not plan(tmp_path, pp, review).keywords


@pytest.mark.parametrize("field,value", [("subject", "Other Song"), ("platform", "tiktok"), ("source_sha256", "0" * 64), ("asset_sha256", "not a hash"), ("reviewed_at", "tomorrow")])
def test_scope_hash_and_date_guards(tmp_path, field, value):
    _, pp, _, review = fixture(tmp_path)
    review[field] = value
    with pytest.raises(InputError):
        plan(tmp_path, pp, review)


@pytest.mark.parametrize("field,value", [("fit", "adjacent"), ("fit", "reject"), ("safety", "hold"), ("safety", "reject")])
def test_fit_and_safety_beat_any_popularity(tmp_path, field, value):
    _, pp, _, review = fixture(tmp_path, text="#ExampleSong")
    review["decisions"][0][field] = value
    assert not plan(tmp_path, pp, review).keywords


def test_unreviewed_and_orphan_decisions(tmp_path):
    _, pp, _, review = fixture(tmp_path)
    removed = review["decisions"].pop()
    assert "unreviewed" in {r["reason"] for r in plan(tmp_path, pp, review).metadata["rejected"]}
    removed["phrase"] = "#NotInPool"
    review["decisions"].append(removed)
    with pytest.raises(InputError):
        plan(tmp_path, pp, review)


def test_stale_review_and_discovery_do_not_get_selected(tmp_path):
    _, pp, pool, review = fixture(tmp_path, text="#ExampleSong")
    pool["keywords"][0]["evidence"][0]["observed_at"] = "2026-09-01"
    save(pp, pool)
    review["source_sha256"] = hashlib.sha256(pp.read_bytes()).hexdigest()
    assert not plan(tmp_path, pp, review).keywords
    review["planned_for"] = "2026-11-15"
    result = plan(tmp_path, pp, review)
    assert not result.keywords and result.metadata["state"] == "needs_native_recheck"


def test_usage_counts_are_not_trend_evidence(tmp_path):
    _, pp, _, review = fixture(tmp_path, text="#ExampleSong")
    review["decisions"][0]["role"] = "trend"
    assert not plan(tmp_path, pp, review).keywords


def test_recent_native_trend_metric_required(tmp_path):
    _, pp, pool, review = fixture(tmp_path, text="#ExampleSong")
    pool["keywords"][0]["evidence"].append({"source": "Synthetic native trend", "metric": "native_trend_rank", "value": 3, "observed_at": "2026-10-23"})
    save(pp, pool)
    review["source_sha256"] = hashlib.sha256(pp.read_bytes()).hexdigest()
    review["decisions"][0]["role"] = "trend"
    assert len(plan(tmp_path, pp, review).keywords) == 1
    review["native_check"]["state"] = "unavailable"
    assert not plan(tmp_path, pp, review).keywords


def test_local_proposals_never_become_trends(tmp_path):
    _, pp, pool, review = fixture(tmp_path, text="#ExampleSong", kind="local_proposal")
    pool["keywords"][0]["evidence"][0]["metric"] = "native_trend_rank"
    save(pp, pool)
    review["source_sha256"] = hashlib.sha256(pp.read_bytes()).hexdigest()
    review["decisions"][0]["role"] = "trend"
    assert not plan(tmp_path, pp, review).keywords


def test_plain_keyword_cannot_silently_become_hashtag(tmp_path):
    _, pp, pool, review = fixture(tmp_path, text="#ExampleSong")
    pool["keywords"][0]["phrase"] = "ExampleSong"
    save(pp, pool)
    review["source_sha256"] = hashlib.sha256(pp.read_bytes()).hexdigest()
    with pytest.raises(InputError, match="literal #"):
        plan(tmp_path, pp, review)


def test_exact_final_copy_audit_and_unexpected_duplicate_tags(tmp_path):
    _, pp, _, review = fixture(tmp_path)
    result = plan(tmp_path, pp, review)
    audit = run("audit", *final_copy(tmp_path, result))
    assert audit.metadata["passed"] is True
    assert audit.metadata["caption_sha256"] == hashlib.sha256(result.metadata["caption_hashtags"].encode()).hexdigest()
    assert audit.metadata["asset_sha256"] == "a" * 64
    assert audit.metadata["target_platform"] == "youtube"
    audit = run("audit", *final_copy(tmp_path, result, caption=result.metadata["caption_hashtags"] + " #Viral #Viral"))
    assert set(audit.metadata["issues"]) >= {"caption_tags_differ_from_plan", "duplicate_caption_tags", "editorial_ceiling_exceeded"}


def test_unavailable_native_check_and_later_audit_remain_hold(tmp_path):
    _, pp, _, review = fixture(tmp_path)
    review["native_check"]["state"] = "unavailable"
    assert not run("audit", *final_copy(tmp_path, plan(tmp_path, pp, review))).metadata["passed"]
    review["native_check"]["state"] = "checked"
    assert not run("audit", *final_copy(tmp_path, plan(tmp_path, pp, review), checked_at="2026-11-10")).metadata["passed"]


def test_asset_swap_and_threads_topic_swap_fail(tmp_path):
    _, pp, _, review = fixture(tmp_path, "threads")
    review["native_topic"] = "Music production"
    result = plan(tmp_path, pp, review)
    assert run("audit", *final_copy(tmp_path, result, native_topic="Other topic")).metadata["issues"] == ["native_topic_differs_from_plan"]
    with pytest.raises(InputError):
        run("audit", *final_copy(tmp_path, result, asset_sha256="b" * 64))


def test_profiles_no_network_or_seed_side_effects():
    assert len(run("profiles").metadata["profiles"]) == 17
    with pytest.raises(ConfigurationError):
        run("unknown")
