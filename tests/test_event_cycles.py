"""Synthetic event dates/indices; never claims of measured native demand."""
import hashlib
import json
from datetime import date, timedelta

import pytest

from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import PluginRequest
from keywordmoves.registry import PluginRegistry

SCOPE = {"geography": "GB", "search_property": "web", "category": "All categories",
         "query_kind": "search_term", "temporal_basis": "UTC"}


def save(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def fixture(tmp_path, years=(2023, 2024, 2025), peaks=(0, 0, 0), base=10, peak=100, window=(-7, 7)):
    sources, inputs = [], []
    for i, year in enumerate(years):
        anchor = date(year, 12, 25)
        p = tmp_path / f"source-{i}.csv"
        p.write_text("Day,Example music\n" + "".join(
            f"{anchor + timedelta(days=d)},{peak if d == peaks[i] else base}\n"
            for d in range(window[0], window[1] + 1)), encoding="utf-8")
        inputs.append(p)
        sources.append({"id": f"source-{i}", "input_index": i + 1, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "observed_at": "2026-10-23", "event_date": anchor.isoformat(), "event_reference": f"example://event-{year}",
            "scope": dict(SCOPE), "source_kind": "synthetic", "reference": f"example://source-{i}"})
    data = {"schema": "keywordmoves-event-cycle-plan/v1", "subject": "Explicitly synthetic example",
        "planned_at": "2026-10-24", "event": {"name": "Synthetic yearly fixture", "next_date": "2026-12-25", "reference": "example://next"},
        "scope": dict(SCOPE), "window": list(window), "sources": sources,
        "reviews": [{"phrase": "Example music", "fit": "direct", "intent": "listening", "rationale": "Synthetic review only."}],
        "scheduling": {"platform": "youtube", "timezone": "Europe/London", "lead_days": 7,
            "not_before": "2026-11-01", "not_after": "2026-12-31", "blocked_dates": []}}
    return inputs, data


def run(tmp_path, inputs, data):
    p = save(tmp_path / "manifest.json", data)
    return PluginRegistry().get("google-trends").run(PluginRequest("plan-event-cycles", inputs=(p, *inputs)), None)


def profile(tmp_path, inputs, data):
    return run(tmp_path, inputs, data).keywords[0].metadata["event_profile"]


def rewrite(path, data, i, text):
    path.write_text(text, encoding="utf-8")
    data["sources"][i]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_three_cycles_have_real_whole_cycle_holdout_and_only_date_proposals(tmp_path):
    inputs, data = fixture(tmp_path)
    result = run(tmp_path, inputs, data)
    p = result.keywords[0].metadata["event_profile"]
    assert p["independent_usable_events"] == 3
    assert p["forecast"]["peak_envelope"] == [0, 0]
    assert p["whole_cycle_chronological_holdouts"][0]["training_events"] == ["2023-12-25", "2024-12-25"]
    assert p["whole_cycle_chronological_holdouts"][0]["held_out_event"] == "2025-12-25"
    assert p["holdout_hits"] == p["holdout_predictions"] == 1
    assert p["timing_support"] == "backtested_limited_sample"
    assert p["schedule_proposal"]["dates"] == ["2026-12-18"]
    assert p["schedule_proposal"]["optimal_hour"] is None
    assert p["schedule_proposal"]["lead_days_are_editorial_not_learned"]
    assert p["music_demand_validated"] is False and p["absolute_monthly_searches"] is None
    assert result.metadata["network_requests"] == 0 and not result.metadata["implementation_authorised"]
    assert result.keywords[0].score is None


def test_two_cycles_stay_limited_and_one_is_insufficient(tmp_path):
    inputs, data = fixture(tmp_path, years=(2024, 2025), peaks=(0, 0))
    p = profile(tmp_path, inputs, data)
    assert p["timing_support"] == "limited_two_cycles" and not p["whole_cycle_chronological_holdouts"]
    data["sources"].pop()
    p = profile(tmp_path, inputs[:1], data)
    assert p["timing_support"] == "insufficient" and p["forecast"] is None


def test_future_cycles_do_not_leak_into_held_out_predictions(tmp_path):
    inputs, data = fixture(tmp_path, peaks=(0, 1, 2))
    p = profile(tmp_path, inputs, data)
    held = p["whole_cycle_chronological_holdouts"][0]
    assert held["predicted_peak_envelope"] == [0, 1]  # excludes held-out day2
    assert held["hit"] is False and held["minimum_calendar_distance_days"] == 1
    assert p["timing_support"] == "unstable_held_out_timing" and p["schedule_proposal"] is None


def test_censoring_stays_distinct_from_threshold_zero_and_missing(tmp_path):
    inputs, data = fixture(tmp_path)
    text = inputs[0].read_text().replace("2023-12-18,10", "2023-12-18,<1").replace("2023-12-19,10", "2023-12-19,0")
    rewrite(inputs[0], data, 0, text)
    p = profile(tmp_path, inputs, data)
    c = p["cycles"][0]
    assert c["censored_bins"] == 1 and c["threshold_zero_bins"] == 1 and c["missing_bins"] == 0
    first = c["samples"][0]
    assert first["raw_value"] == "<1" and first["value"] is None and first["censored"] is True
    assert first["index_bounds"]["lower"] == 0 and first["index_bounds"]["upper"] == 1
    assert first["index_bounds"]["upper_exclusive"] is True
    rewrite(inputs[0], data, 0, text.replace("2023-12-20,10", "2023-12-20,"))
    c = profile(tmp_path, inputs, data)["cycles"][0]
    assert c["missing_bins"] == 1 and "missing_values" in c["quality_issues"] and not c["used_for_timing"]


@pytest.mark.parametrize("base,peak,issue", [(0, 0, "threshold_zero_or_censored_only"),
    (0, 100, "sparse_exact_positive_bins"), (1, 3, "low_original_scale_resolution_request_standalone"),
    (100, 100, "flat_or_wide_peak")])
def test_low_quality_sources_abstain_without_rescaling(tmp_path, base, peak, issue):
    inputs, data = fixture(tmp_path, base=base, peak=peak)
    p = profile(tmp_path, inputs, data)
    assert p["forecast"] is None and p["schedule_proposal"] is None
    assert all(issue in c["quality_issues"] for c in p["cycles"])
    assert all(c["original_peak_index"] == peak for c in p["cycles"])
    assert not p["cross_request_amplitude_comparison"]


def test_tied_peaks_stay_an_interval_not_an_invented_day(tmp_path):
    inputs, data = fixture(tmp_path)
    for i, path in enumerate(inputs):
        text = path.read_text()
        y = data["sources"][i]["event_date"][:4]
        rewrite(path, data, i, text.replace(f"{y}-12-24,10", f"{y}-12-24,100").replace(f"{y}-12-26,10", f"{y}-12-26,100"))
    p = profile(tmp_path, inputs, data)
    assert p["forecast"]["peak_envelope"] == [-1, 1]
    assert p["schedule_proposal"]["dates"] == ["2026-12-17", "2026-12-18", "2026-12-19"]


def test_weekly_bin_straddling_anchor_keeps_its_full_uncertainty(tmp_path):
    inputs, data = fixture(tmp_path, window=(-14, 14))
    for i, path in enumerate(inputs):
        anchor = date.fromisoformat(data["sources"][i]["event_date"])
        text = "Week,Example music\n" + "".join(f"{anchor + timedelta(days=d)},{100 if d == -3 else 10}\n" for d in (-17, -10, -3, 4, 11))
        rewrite(path, data, i, text)
    p = profile(tmp_path, inputs, data)
    assert p["cycles"][0]["bin_precision_days"] == 7
    assert p["cycles"][0]["peak_intervals"] == [[-3, 3]]
    assert p["forecast"]["peak_envelope"] == [-3, 3]


def test_source_partial_and_future_bins_do_not_count_as_complete_cycles(tmp_path):
    inputs, data = fixture(tmp_path)
    text = inputs[0].read_text().replace("Day,Example music", "Day,Example music,IsPartial")
    lines = text.splitlines()
    lines = [lines[0]] + [line + (",true" if line.startswith("2023-12-25") else ",false") for line in lines[1:]]
    rewrite(inputs[0], data, 0, "\n".join(lines) + "\n")
    data["sources"][1]["observed_at"] = "2024-12-24"
    p = profile(tmp_path, inputs, data)
    assert all("incomplete_alignment_window" in c["quality_issues"] for c in p["cycles"][:2])
    assert p["independent_usable_events"] == 1 and p["forecast"] is None


def test_multiple_requests_of_one_event_are_not_multiple_cycles(tmp_path):
    inputs, data = fixture(tmp_path, years=(2024, 2024, 2025), peaks=(0, 0, 0))
    p = profile(tmp_path, inputs, data)
    assert p["independent_usable_events"] == 1
    assert p["source_selection_issues"][0]["issue"] == "duplicate_eligible_sources_require_explicit_preference"
    data["reviews"][0]["preferred_sources"] = {"2024-12-25": "source-1"}
    p = profile(tmp_path, inputs, data)
    assert p["independent_usable_events"] == 2
    assert sum(c["used_for_timing"] for c in p["cycles"] if c["event_date"] == "2024-12-25") == 1


def test_overlapping_windows_are_not_independent_cycles(tmp_path):
    inputs, data = fixture(tmp_path, years=(2024, 2024), peaks=(0, 0))
    data["sources"][1]["event_date"] = "2024-12-26"
    # Move the complete second capture with its anchor, so this tests overlap rather than missing coverage.
    lines = inputs[1].read_text().splitlines()
    text = lines[0] + "\n" + "".join(
        f"{date.fromisoformat(line.split(',')[0]) + timedelta(days=1)},{line.split(',')[1]}\n"
        for line in lines[1:])
    rewrite(inputs[1], data, 1, text)
    p = profile(tmp_path, inputs, data)
    assert not p["independent_usable_events"]
    assert p["source_selection_issues"][0]["issue"] == "overlapping_event_windows_are_not_independent"


@pytest.mark.parametrize("key,value", [("geography", "US"), ("search_property", "youtube"),
    ("category", "Music"), ("query_kind", "topic"), ("temporal_basis", "Europe/London")])
def test_changed_scope_is_rejected_instead_of_silently_combined(tmp_path, key, value):
    inputs, data = fixture(tmp_path)
    data["sources"][0]["scope"][key] = value
    with pytest.raises(InputError, match="scope differs"):
        run(tmp_path, inputs, data)


def test_hash_input_and_duplicate_period_guards(tmp_path):
    inputs, data = fixture(tmp_path)
    old = data["sources"][0]["sha256"]
    data["sources"][0]["sha256"] = "0" * 64
    with pytest.raises(InputError, match="hash differs"):
        run(tmp_path, inputs, data)
    data["sources"][0]["sha256"] = old
    rewrite(inputs[0], data, 0, inputs[0].read_text() + "2023-12-25,100\n")
    with pytest.raises(InputError, match="chronological"):
        run(tmp_path, inputs, data)


def test_constraints_block_dates_without_moving_outside_evidence(tmp_path):
    inputs, data = fixture(tmp_path)
    data["scheduling"]["blocked_dates"] = ["2026-12-18"]
    p = profile(tmp_path, inputs, data)
    assert p["schedule_proposal"]["state"] == "held_no_available_future_date"
    assert p["schedule_proposal"]["dates"] == []


@pytest.mark.parametrize("key,value", [("fit", "reject"), ("intent", "unvalidated")])
def test_data_quality_does_not_override_editorial_rejection_or_unvalidated_intent(tmp_path, key, value):
    inputs, data = fixture(tmp_path)
    data["reviews"][0][key] = value
    p = profile(tmp_path, inputs, data)
    assert p["forecast"] is not None and p["schedule_proposal"] is None


def test_queries_can_select_an_explicit_subset_without_changing_source_hash(tmp_path):
    inputs, data = fixture(tmp_path)
    for i, path in enumerate(inputs):
        lines = path.read_text().splitlines()
        rewrite(path, data, i, lines[0] + ",Unrelated query\n" + "\n".join(line + ",0" for line in lines[1:]) + "\n")
        data["sources"][i]["queries"] = ["Example music"]
    result = run(tmp_path, inputs, data)
    assert [k.phrase for k in result.keywords] == ["example music"]
    assert all(s["excluded_queries"] == ["unrelated query"] for s in result.metadata["sources"])


def test_empty_exports_remain_explicitly_unavailable(tmp_path):
    inputs, data = fixture(tmp_path)
    for i, path in enumerate(inputs):
        rewrite(path, data, i, "Day,Example music\n")
    data["reviews"] = []
    result = run(tmp_path, inputs, data)
    assert not result.keywords
    assert all(s["state"] == "empty_unavailable" for s in result.metadata["sources"])


def test_one_native_export_can_supply_disjoint_event_cycles_without_new_normalization(tmp_path):
    inputs, data = fixture(tmp_path)
    text = "Day,Example music\n" + "".join(p.read_text().split("\n", 1)[1] for p in inputs)
    rewrite(inputs[0], data, 0, text)
    for source in data["sources"]:
        source["input_index"] = 1
        source["sha256"] = data["sources"][0]["sha256"]
    p = profile(tmp_path, inputs[:1], data)
    assert p["independent_usable_events"] == 3
    assert len({c["normalization_id"] for c in p["cycles"]}) == 1


def test_seed_options_or_missing_csv_cannot_expand_collection(tmp_path):
    inputs, data = fixture(tmp_path)
    manifest = save(tmp_path / "manifest.json", data)
    plugin = PluginRegistry().get("google-trends")
    for request in (PluginRequest("plan-event-cycles", inputs=(manifest,)),
        PluginRequest("plan-event-cycles", keywords=("extra",), inputs=(manifest, *inputs)),
        PluginRequest("plan-event-cycles", inputs=(manifest, *inputs), options={"collect": True})):
        with pytest.raises(ConfigurationError):
            plugin.run(request, None)
