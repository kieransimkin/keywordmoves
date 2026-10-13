import hashlib
import json

import pytest

from keywordmoves.builtin.candidate_shortlist import CandidateShortlistPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginRequest,
    PluginResult,
)
from keywordmoves.registry import PluginRegistry


def fixture(tmp_path, *, phrases=None, mutate_review=None, mutate_pool=None):
    phrases = phrases or ["friendship songs", "songs about friends", "friendship song lyrics", "seaside music", "summer memories song", "childrens songs"]
    candidates = [KeywordCandidate(p, score=None, evidence=(KeywordEvidence("Example provider", "volume", "<100", "display", "2026-10-13", "GB"),),
        metadata={"native_platform": "Google", "unknown_demand": True}) for p in phrases]
    pool = PluginResult("example-pool", "combine", tuple(candidates), metadata={"target_subject": "Example Song", "target_platform": "YouTube"}).to_dict()
    if mutate_pool:
        mutate_pool(pool)
    source = tmp_path / "pool.json"
    source.write_text(json.dumps(pool), encoding="utf-8")
    review = {"schema": "keywordmoves-shortlist-review/v1", "subject": "Example Song", "platform": "YouTube",
        "reviewed_at": "2026-10-13", "reviewer_kind": "analyst", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "decisions": [{"phrase": p, "fit": "reject" if "childrens" in p else "direct", "intent": "listening",
            "clarity": "natural", "group": "friendship" if i < 3 else f"group-{i}", "priority": i + 1,
            "rationale": "Synthetic explicit song-fit review; not a demand measurement."} for i, p in enumerate(phrases)]}
    if mutate_review:
        mutate_review(review)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    return path, source


def run(paths, **options):
    return CandidateShortlistPlugin().run(PluginRequest("select", inputs=paths, options=options), ExecutionContext(None))


def test_diversity_preserves_original_metrics_and_reports_shortfall(tmp_path):
    result = run(fixture(tmp_path))
    assert [k.phrase for k in result.keywords] == ["friendship songs", "songs about friends", "seaside music", "summer memories song"]
    assert result.metadata["shortfall"] == 1 and result.metadata["coverage_state"] == "shortfall"
    assert result.keywords[0].evidence[0].value == "<100"
    assert result.keywords[0].evidence[0].geography == "GB"
    assert result.keywords[0].metadata["native_platform"] == "Google"
    assert result.keywords[0].score is None
    assert result.keywords[0].metadata["shortlist_review"]["implementation_authorised"] is False
    assert {r["reason"] for r in result.metadata["rejected"]} == {"fit_ineligible", "intent_group_cap"}
    assert result.metadata["network_requests"] == 0


def test_loud_source_score_cannot_rescue_rejected_fit(tmp_path):
    result = run(fixture(tmp_path, mutate_pool=lambda p: p["keywords"][-1].update(score=100)))
    assert "childrens songs" not in [k.phrase for k in result.keywords]


def test_shortlist_can_meet_minimum_without_duplicate_cluster_padding(tmp_path):
    result = run(fixture(tmp_path), max_per_group=3)
    assert len(result.keywords) == 5 and result.metadata["coverage_state"] == "minimum_met"


def test_unknown_volume_and_source_score_are_retained_without_ranking_by_them(tmp_path):
    result = run(fixture(tmp_path, mutate_pool=lambda p: p["keywords"][3].update(score=999)))
    assert result.keywords[0].phrase == "friendship songs" and result.keywords[2].score == 999
    assert result.keywords[2].metadata["shortlist_review"]["not_a_demand_score"] is True


def test_unicode_review_join_retains_persian_and_case_insensitive_matching(tmp_path):
    phrases = ["آهنگ آزادی", "Song About Friends"]
    paths = fixture(tmp_path, phrases=phrases, mutate_review=lambda r: r["decisions"][1].update(phrase="song about friends"))
    result = run(paths, minimum=1)
    assert [k.phrase for k in result.keywords] == phrases


def test_unreviewed_rows_are_not_silently_accepted(tmp_path):
    result = run(fixture(tmp_path, mutate_review=lambda r: r["decisions"].pop(0)))
    assert result.metadata["reviewed_rows"] == 5
    assert {r["reason"] for r in result.metadata["rejected"]} >= {"unreviewed"}


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64), ("platform", "TikTok"), ("subject", "Other Song"),
    ("reviewer_kind", []), ("reviewed_at", "unknown")])
def test_stale_scope_and_invalid_review_fail(tmp_path, field, value):
    with pytest.raises(InputError):
        run(fixture(tmp_path, mutate_review=lambda r: r.update({field: value})))


@pytest.mark.parametrize("field,value", [("fit", []), ("intent", "therapy"), ("clarity", []), ("priority", True),
    ("priority", 0), ("rationale", ""), ("phrase", "absent source phrase")])
def test_invalid_or_orphan_decisions_fail(tmp_path, field, value):
    with pytest.raises(InputError):
        run(fixture(tmp_path, mutate_review=lambda r: r["decisions"][0].update({field: value})))


def test_normalized_source_duplicates_and_review_duplicates_fail(tmp_path):
    with pytest.raises(InputError):
        run(fixture(tmp_path, phrases=["Friendship Song", "friendship song"]))
    with pytest.raises(InputError):
        run(fixture(tmp_path, mutate_review=lambda r: r["decisions"].append(dict(r["decisions"][0]))))


@pytest.mark.parametrize("options", [{"limit": True}, {"limit": 0}, {"limit": 3}, {"minimum": 6},
    {"max_per_group": 0}, {"allow_adjacent": "yes"}, {"intent": []}, {"demand_weight": 10}])
def test_invalid_selection_options_fail(tmp_path, options):
    with pytest.raises(ConfigurationError):
        run(fixture(tmp_path), **options)


def test_navigation_and_adjacent_reviews_require_explicit_selection(tmp_path):
    paths = fixture(tmp_path, mutate_review=lambda r: r["decisions"][0].update(fit="adjacent", intent="navigation"))
    assert "friendship songs" not in [k.phrase for k in run(paths).keywords]
    selected = run(paths, intent="navigation", allow_adjacent=True, minimum=1)
    assert [k.phrase for k in selected.keywords] == ["friendship songs"]


def test_builtin_registry_discovers_shortlist_without_optional_dependencies():
    assert PluginRegistry().get("candidate-shortlist").descriptor.operations == ("select",)


def test_shortlist_composes_offline_through_research_batch(tmp_path):
    review, pool = fixture(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": [{
        "id": "review", "subject": "Example Song", "plugin": "candidate-shortlist",
        "operation": "select", "inputs": [str(review), str(pool)],
        "options": {"max_per_group": 3}}]}), encoding="utf-8")
    result = PluginRegistry().get("research-batch").run(
        PluginRequest("collect", inputs=(plan,)), ExecutionContext(None))
    assert len(result.keywords) == 5
    assert result.metadata["unavailable_tasks"] == 0
    assert all(k.metadata["shortlist_review"]["implementation_authorised"] is False for k in result.keywords)
