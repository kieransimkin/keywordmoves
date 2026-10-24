"""Native text edits bind native identity and baseline, never fabricated media bytes."""
import hashlib
import json

import pytest
from test_hashtag_planner import fixture, run, save

from keywordmoves.errors import InputError
from keywordmoves.monitoring.validation import digest


def setup(tmp_path, platform="youtube"):
    _, pool_path, _, review = fixture(tmp_path, platform, text="#ExampleSong", kind="local_proposal")
    before = {"schema": "keywordmoves-native-metadata/v1", "subject": "Example Song", "platform": platform,
        "account_id": "synthetic-account", "object_id": "synthetic-object", "captured_at": "2026-10-23",
        "fields": {"title": "Original title", "description": "Original text #ExampleSong"}, "native_topic": None}
    baseline = save(tmp_path / "before.json", before)
    review["schema"] = "keywordmoves-hashtag-native-review/v1"
    review.pop("asset_sha256")
    review["native_target"] = {"account_id": before["account_id"], "object_id": before["object_id"],
        "before_metadata_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest()}
    return pool_path, review, baseline, before


def native_plan(tmp_path, pool, review, baseline):
    return run("plan-native", pool, save(tmp_path / "review-native.json", review), baseline)


def native_copy(tmp_path, result, baseline, **changes):
    pp = save(tmp_path / "plan-native.json", result.to_dict())
    meta = result.metadata
    copy = {"schema": "keywordmoves-hashtag-native-final-copy/v1", "subject": meta["target_subject"],
        "platform": meta["target_platform"], "native_target": dict(meta["native_target"]),
        "plan_sha256": hashlib.sha256(pp.read_bytes()).hexdigest(), "checked_at": "2026-10-24",
        "fields": {"title": "Clearer title", "description": "Natural copy. " + meta["caption_hashtags"]},
        "native_topic": meta["native_topic"]}
    copy.update(changes)
    return pp, baseline, save(tmp_path / "native-copy.json", copy)


def test_existing_native_audit_uses_exact_copy_hashes_without_media_claim(tmp_path):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    paths = native_copy(tmp_path, result, baseline)
    audit = run("audit-native", *paths).metadata
    assert audit["passed"]
    assert audit["media_provenance_checked"] is False
    assert audit["implementation_authorised"] is False
    assert audit["target_kind"] == "existing_native_metadata"
    assert "asset_sha256" not in audit and "asset_sha256" not in result.metadata
    copy = json.loads(paths[2].read_text(encoding="utf-8"))
    assert audit["fields_sha256"] == digest(copy["fields"])
    assert audit["field_sha256"]["title"] == hashlib.sha256(b"Clearer title").hexdigest()
    assert audit["final_copy_sha256"] == hashlib.sha256(paths[2].read_bytes()).hexdigest()
    assert audit["network_requests"] == 0


@pytest.mark.parametrize("key,value", [("account_id", "other-account"), ("object_id", "other-object"),
    ("before_metadata_sha256", "0" * 64)])
def test_native_identity_and_baseline_swaps_rejected_in_review_and_copy(tmp_path, key, value):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    target = {**review["native_target"], key: value}
    with pytest.raises(InputError):
        run("audit-native", *native_copy(tmp_path, result, baseline, native_target=target))
    review["native_target"] = target
    with pytest.raises(InputError):
        native_plan(tmp_path, pool, review, baseline)


@pytest.mark.parametrize("key,value", [("platform", "tiktok"), ("subject", "Other Song"),
    ("plan_sha256", "0" * 64)])
def test_final_native_scope_and_plan_swaps_rejected(tmp_path, key, value):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    with pytest.raises(InputError):
        run("audit-native", *native_copy(tmp_path, result, baseline, **{key: value}))


def test_baseline_is_checked_again_at_audit(tmp_path):
    pool, review, baseline, before = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    before["fields"]["title"] = "Concurrent change"
    save(baseline, before)
    with pytest.raises(InputError, match="baseline hash"):
        run("audit-native", *native_copy(tmp_path, result, baseline))


@pytest.mark.parametrize("fields", [{"title": "Omitted description"}, {"title": "A", "description": "B", "visibility": "Public"},
    {"title": "A", "description": "B", "video_tags": ["example"]}, {"title": "A", "description": 3}])
def test_native_audit_cannot_change_field_scope_or_accept_noncopy_controls(tmp_path, fields):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    with pytest.raises(InputError):
        run("audit-native", *native_copy(tmp_path, result, baseline, fields=fields))


def test_duplicate_or_unexpected_tags_in_title_are_not_hidden(tmp_path):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    fields = {"title": "Title #ExampleSong #Viral", "description": "Text #ExampleSong #Extra"}
    audit = run("audit-native", *native_copy(tmp_path, result, baseline, fields=fields)).metadata
    assert set(audit["issues"]) >= {"caption_tags_differ_from_plan", "duplicate_caption_tags", "editorial_ceiling_exceeded"}


def test_native_audit_keeps_context_and_baseline_freshness_holds(tmp_path):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    audit = run("audit-native", *native_copy(tmp_path, result, baseline, checked_at="2026-11-10")).metadata
    assert set(audit["issues"]) >= {"native_recheck_required", "native_baseline_recheck_required"}
    review["native_check"]["state"] = "unavailable"
    result = native_plan(tmp_path, pool, review, baseline)
    assert not run("audit-native", *native_copy(tmp_path, result, baseline)).metadata["passed"]


def test_native_plan_retains_fit_safety_and_generic_rejections(tmp_path):
    pool, review, baseline, _ = setup(tmp_path)
    review["decisions"][0]["safety"] = "hold"
    result = native_plan(tmp_path, pool, review, baseline)
    assert not result.keywords
    assert result.metadata["rejected"][0]["reason"] == "safety_hold_or_rejection"


def test_native_and_media_contracts_cannot_substitute_for_each_other(tmp_path):
    pool, review, baseline, _ = setup(tmp_path)
    result = native_plan(tmp_path, pool, review, baseline)
    paths = native_copy(tmp_path, result, baseline)
    with pytest.raises(InputError):
        run("audit", paths[0], paths[2])
    review["asset_sha256"] = "a" * 64
    with pytest.raises(InputError):
        native_plan(tmp_path, pool, review, baseline)


def test_threads_native_topic_is_still_reviewed_separately(tmp_path):
    pool, review, baseline, _ = setup(tmp_path, "threads")
    review["native_topic"] = "Music making"
    result = native_plan(tmp_path, pool, review, baseline)
    audit = run("audit-native", *native_copy(tmp_path, result, baseline, native_topic="Other topic")).metadata
    assert audit["issues"] == ["native_topic_differs_from_plan"]
