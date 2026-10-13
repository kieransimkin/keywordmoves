import json

import pytest

from keywordmoves.builtin.candidate_focus import CandidateFocusPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginRequest,
    PluginResult,
)


def focus(tmp_path, seed="grief song", operation="related", rows=None, **options):
    if rows is None:
        rows = [
            KeywordCandidate("preschool song", score=99),
            KeywordCandidate(
                "grief song",
                score=None,
                evidence=(
                    KeywordEvidence(
                        "Example provider", "volume", "<100", "searches", "2026-10-08", "GB"
                    ),
                ),
            ),
            KeywordCandidate("song about grief", score=12),
            KeywordCandidate("college football", score=100),
        ]
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(PluginResult("example", "import", tuple(rows)).to_dict()), encoding="utf-8"
    )
    return CandidateFocusPlugin().run(
        PluginRequest(operation, (seed,), (path,), options), ExecutionContext(None)
    )


def test_explicit_context_exclusions_and_original_evidence(tmp_path):
    out = focus(tmp_path, include_any="song|music", exclude_any="preschool|college football")
    assert [k.phrase for k in out.keywords] == ["grief song", "song about grief"]
    assert out.keywords[0].score is None
    assert out.keywords[0].evidence[0].value == "<100"
    assert out.keywords[0].evidence[0].geography == "GB"
    assert out.keywords[1].score == 12
    assert out.metadata["matched_rows"] == 2
    assert {r["reason"] for r in out.metadata["rejected"]} == {"excluded_phrase"}
    assert len(out.metadata["inputs"][0]["sha256"]) == 64


def test_topic_and_listening_context_must_both_match(tmp_path):
    rows = [
        KeywordCandidate("what is political satire", score=100),
        KeywordCandidate("best songs", score=99),
        KeywordCandidate("political comedy music", score=None),
        KeywordCandidate(
            "political song", evidence=(KeywordEvidence("Example", "volume", 12, "searches"),)
        ),
    ]
    out = focus(
        tmp_path,
        seed="political songs",
        rows=rows,
        min_overlap=0,
        require_groups="song|songs|music;political|satire|comedy",
    )
    assert {k.phrase for k in out.keywords} == {"political comedy music", "political song"}
    assert len(out.metadata["rejected"]) == 2
    assert all(r["reason"] == "required_context_group_absent" for r in out.metadata["rejected"])
    assert out.keywords[0].score is None


@pytest.mark.parametrize("groups", ["song;", "song||music;grief", ["song"], ";grief"])
def test_invalid_required_context_groups_fail_before_filtering(tmp_path, groups):
    with pytest.raises(ConfigurationError):
        focus(tmp_path, require_groups=groups)


def test_exact_long_tail_requires_every_meaningful_seed_term(tmp_path):
    rows = [
        KeywordCandidate("grief"),
        KeywordCandidate("song about grief"),
        KeywordCandidate("grief songs"),
    ]
    out = focus(tmp_path, operation="matching-terms", rows=rows)
    assert [k.phrase for k in out.keywords] == ["song about grief"]
    assert len(out.metadata["rejected"]) == 2


def test_unicode_tokens_do_not_use_ascii_stemming_or_substrings(tmp_path):
    rows = [
        KeywordCandidate("آهنگ دوستی"),
        KeywordCandidate("آهنگ دوستی جدید"),
        KeywordCandidate("دوستان"),
    ]
    out = focus(tmp_path, seed="آهنگ دوستی", rows=rows, operation="matching-terms")
    assert len(out.keywords) == 2
    assert out.keywords[0].phrase == "آهنگ دوستی"


def test_limits_report_omissions_and_preserve_source_order_for_ties(tmp_path):
    out = focus(tmp_path, limit=1)
    assert out.keywords[0].phrase == "grief song"
    assert out.metadata["limit_omitted_rows"] == 2
    assert out.metadata["network_requests"] == 0


def test_missing_context_can_return_an_honest_empty_queue(tmp_path):
    out = focus(tmp_path, include_any="hip hop", min_overlap=0)
    assert out.keywords == ()
    assert out.metadata["input_rows"] == 4


def test_subjects_are_never_assigned_to_another_song(tmp_path):
    rows = [KeywordCandidate("grief song", metadata={"subject": "Other Song"})]
    out = focus(tmp_path, rows=rows, subject="Example Song")
    assert not out.keywords
    assert out.metadata["rejected"][0]["reason"] == "different_subject"


@pytest.mark.parametrize(
    "options", [{"limit": True}, {"min_overlap": -1}, {"include_any": []}, {"secret": "no"}]
)
def test_invalid_filter_options_fail(tmp_path, options):
    with pytest.raises(ConfigurationError):
        focus(tmp_path, **options)


def test_bad_input_does_not_acquire_a_score(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"plugin":"example","keywords":[{"phrase":null}]}', encoding="utf-8")
    with pytest.raises(InputError):
        CandidateFocusPlugin().run(
            PluginRequest("related", ("song",), (path,)), ExecutionContext(None)
        )
