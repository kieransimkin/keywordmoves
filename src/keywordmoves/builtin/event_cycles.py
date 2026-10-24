"""Offline, source-bound event timing. Relative indices never become volume."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date, timedelta

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginRequest, PluginResult
from ..monitoring.validation import canonical, label, read_json, reject_secrets, stamp
from .google_trends import GoogleTrendsPlugin, _clean_series_name, _header_index, _rows

SCOPE = {"geography", "search_property", "category", "query_kind", "temporal_basis"}
POLICY = {"minimum_exact_positive_bins": 4, "minimum_original_peak_index": 10,
          "maximum_peak_envelope_days": 14, "high_interest_fraction_of_cycle_peak": 0.75}


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or not set(required) <= set(obj) or set(obj) - set(required) - set(optional):
        raise InputError("Unexpected or missing event-cycle document fields.")


def day(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except (TypeError, ValueError) as exc:
        raise InputError("Event anchors/constraints require exact ISO calendar dates.") from exc


def integer(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise InputError(f"Expected an integer in {lower}..{upper}.")
    return value


def scope(obj):
    fields(obj, SCOPE)
    result = {k: label(obj[k], k) for k in SCOPE}
    if result["search_property"] not in {"web", "youtube"} or result["query_kind"] not in {"search_term", "topic"}:
        raise InputError("Declare web/youtube and search_term/topic exactly; no cross-scope calibration.")
    return result


def intervals(points):
    """Collapse integer calendar offsets without pretending coarse bins are daily measures."""
    result = []
    for point in sorted(set(points)):
        if result and point == result[-1][1] + 1:
            result[-1][1] = point
        else:
            result.append([point, point])
    return result


def union_days(windows):
    return {v for lo, hi in windows for v in range(lo, hi + 1)}


def prediction(cycles):
    if len(cycles) < 2:
        return None
    peak_days = union_days([w for c in cycles for w in c["peak_intervals"]])
    if not peak_days:
        return None
    bounds = [min(peak_days), max(peak_days)]
    if bounds[1] - bounds[0] + 1 > POLICY["maximum_peak_envelope_days"]:
        return None
    counts = Counter(v for c in cycles for v in union_days(c["high_interest_intervals"]))
    majority = [v for v, count in counts.items() if count > len(cycles) / 2]
    return {"peak_envelope": bounds, "high_interest_majority_intervals": intervals(majority),
            "peak_envelope_basis": "range of historical peak-bin positions; not a statistical confidence interval",
            "independent_cycles": len(cycles)}


def aligned(candidate, source, window):
    anchor = day(source["event_date"])
    samples, covered, exact, positive = [], set(), [], []
    widths, missing, censored, zero, partial = [], 0, 0, 0, 0
    for p in candidate.metadata["series"]:
        start, end = day(p["period"]), day(p["period_end"])
        lo, hi = (start - anchor).days, (end - anchor).days
        if hi < window[0] or lo > window[1]:
            continue
        # Keep the complete bin interval: a weekly anchor is never assigned an invented daily peak.
        sample = {"start_day": lo, "end_day": hi, **p,
                  "index_bounds": {"lower": 0, "upper": 1, "upper_exclusive": True,
                                   "basis": "native <1 relative index display, not search counts"} if p["censored"] else None}
        samples.append(sample)
        if not p["eligible_for_summary"]:
            partial += 1
            continue
        covered.update(range(max(lo, window[0]), min(hi, window[1]) + 1))
        widths.append((end - start).days + 1)
        censored += p["censored"]
        missing += p["value"] is None and not p["censored"]
        zero += p["value"] == 0
        if p["value"] is not None:
            exact.append(sample)
            if p["value"] > 0:
                positive.append(sample)
    peak = max((p["value"] for p in exact), default=None)
    peaks = [[p["start_day"], p["end_day"]] for p in exact if peak and p["value"] == peak]
    high = [[p["start_day"], p["end_day"]] for p in exact
            if peak and p["value"] >= peak * POLICY["high_interest_fraction_of_cycle_peak"]]
    issues = []
    if covered != set(range(window[0], window[1] + 1)):
        issues.append("incomplete_alignment_window")
    if missing:
        issues.append("missing_values")
    if not positive:
        issues.append("threshold_zero_or_censored_only")
    elif len(positive) < POLICY["minimum_exact_positive_bins"]:
        issues.append("sparse_exact_positive_bins")
    if peak is not None and 0 < peak < POLICY["minimum_original_peak_index"]:
        issues.append("low_original_scale_resolution_request_standalone")
    if peaks and max(hi for _, hi in peaks) - min(lo for lo, _ in peaks) + 1 > POLICY["maximum_peak_envelope_days"]:
        issues.append("flat_or_wide_peak")
    return {"source_id": source["id"], "event_date": source["event_date"], "event_reference": source["event_reference"],
            "source_sha256": source["sha256"], "normalization_id": source["sha256"],
            "source_kind": source["source_kind"], "reference": source["reference"],
            "scope": source["scope"], "observed_at": source["observed_at"],
            "eligible_for_timing": not issues, "quality_issues": issues,
            "original_peak_index": peak, "exact_positive_bins": len(positive), "threshold_zero_bins": zero,
            "censored_bins": censored, "missing_bins": missing, "excluded_incomplete_bins": partial,
            "covered_days": len(covered), "required_days": window[1] - window[0] + 1,
            "bin_precision_days": max(widths, default=None), "peak_intervals": peaks,
            "high_interest_intervals": high, "samples": samples,
            "raw_censoring_preserved": True, "absolute_searches": None}


def plan_event_cycles(request):
    if request.keywords or request.options or not 2 <= len(request.inputs) <= 61:
        raise ConfigurationError("plan-event-cycles takes manifest then 1-60 explicit CSV inputs; no seeds/options.")
    raw, manifest = read_json(request.inputs[0])
    canonical(manifest)
    reject_secrets(manifest)
    fields(manifest, {"schema", "subject", "planned_at", "event", "scope", "window", "sources", "reviews"}, {"scheduling"})
    if manifest["schema"] != "keywordmoves-event-cycle-plan/v1":
        raise InputError("Expected keywordmoves-event-cycle-plan/v1.")
    subject = label(manifest["subject"], "subject")
    planned = stamp(manifest["planned_at"])
    planned_day = day(planned[:10])
    target_scope = scope(manifest["scope"])
    fields(manifest["event"], {"name", "next_date", "reference"})
    event = manifest["event"]
    label(event["name"], "event name")
    label(event["reference"], "event reference")
    next_date = day(event["next_date"])
    if next_date <= planned_day:
        raise InputError("The forecast event must follow the analysis date; historical anchors belong in sources.")
    if not isinstance(manifest["window"], list) or len(manifest["window"]) != 2:
        raise InputError("window is [first_relative_day,last_relative_day].")
    window = [integer(v, -366, 366) for v in manifest["window"]]
    if window[0] > 0 or window[1] < 0 or window[0] > window[1]:
        raise InputError("The bounded alignment window must include event day0.")
    sources = manifest["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 60:
        raise InputError("Supply 1-60 source records.")
    indices, identities, anchors, grouped, source_records = set(), set(), set(), defaultdict(list), []
    input_bytes = {}
    total_bytes, total_points = 0, 0
    for source in sources:
        fields(source, {"id", "input_index", "sha256", "observed_at", "event_date", "event_reference", "scope", "source_kind", "reference"}, {"queries"})
        sid = label(source["id"], "source id")
        index = integer(source["input_index"], 1, len(request.inputs) - 1)
        anchor = day(source["event_date"])
        if sid in identities or (index, anchor) in anchors:
            raise InputError("Source ids and input/event pairs must be unique; do not count one capture/event twice.")
        identities.add(sid)
        indices.add(index)
        anchors.add((index, anchor))
        if scope(source["scope"]) != target_scope:
            raise InputError("Source scope differs in geography/property/category/query kind/temporal basis.")
        if not isinstance(source["source_kind"], str) or source["source_kind"] not in {"native_csv", "native_table_transcription", "synthetic"}:
            raise InputError("Declare native_csv, native_table_transcription or synthetic accurately.")
        label(source["reference"], "source reference")
        label(source["event_reference"], "historical event reference")
        observed = stamp(source["observed_at"])
        if observed > planned:
            raise InputError("Source observation cannot postdate analysis.")
        if index not in input_bytes:
            try:
                if request.inputs[index].stat().st_size > 5_000_000:
                    raise InputError("Each event CSV must fit the existing5MB import bound.")
                input_bytes[index] = request.inputs[index].read_bytes()
            except OSError as exc:
                raise InputError("Event CSV inputs must be readable files.") from exc
            total_bytes += len(input_bytes[index])
        source_raw = input_bytes[index]
        if total_bytes > 30_000_000 or hashlib.sha256(source_raw).hexdigest() != source["sha256"]:
            raise InputError("CSV source hash differs or aggregate input exceeds30MB.")
        rows, _ = _rows(None, captured_bytes=source_raw)
        head = _header_index(rows, {"week", "day", "date", "month"})
        names = [_clean_series_name(n).casefold() for n in rows[head][1:] if n.casefold() != "ispartial"]
        periods = [r[0] for r in rows[head + 1:] if r and any(r)]
        if len(names) != len(set(names)) or periods != sorted(set(periods)):
            raise InputError("Series names/periods must be unique and chronological.")
        wanted = source.get("queries", names)
        if not isinstance(wanted, list) or not wanted or not all(isinstance(v, str) for v in wanted):
            raise InputError("queries must name a nonempty explicit source subset.")
        wanted = [label(v, "query").casefold() for v in wanted]
        if len(set(wanted)) != len(wanted) or set(wanted) - set(names):
            raise InputError("Source query selection contains duplicates or absent columns.")
        source_records.append({**source, "normalization_id": source["sha256"], "included_queries": wanted,
                               "excluded_queries": sorted(set(names) - set(wanted)), "rows": len(periods),
                               "state": "captured" if periods else "empty_unavailable"})
        if not periods:
            continue
        parsed = GoogleTrendsPlugin().import_csv(PluginRequest("import-interest", options={
            **target_scope, "observed_at": observed, "completed_periods_only": True}), captured_bytes=source_raw)
        for candidate in parsed.keywords:
            if candidate.phrase not in wanted:
                continue
            total_points += len(candidate.metadata["series"])
            if total_points > 50000 or (candidate.phrase not in grouped and len(grouped) >= 100):
                raise InputError("Event analysis exceeds50000 bins or100 phrases.")
            grouped[candidate.phrase].append(aligned(candidate, source, window))
    if indices != set(range(1, len(request.inputs))):
        raise InputError("Every explicit CSV input must be addressed by a manifest source record.")
    reviews = manifest["reviews"]
    if not isinstance(reviews, list) or len(reviews) > 100:
        raise InputError("Supply at most100 explicit phrase reviews.")
    reviewed = {}
    for review in reviews:
        fields(review, {"phrase", "fit", "intent", "rationale"}, {"preferred_sources"})
        phrase = label(review["phrase"], "review phrase").casefold()
        if phrase in reviewed or phrase not in grouped:
            raise InputError("Duplicate or absent phrase review.")
        if not isinstance(review["fit"], str) or not isinstance(review["intent"], str) or review["fit"] not in {"direct", "context", "reject"} or review["intent"] not in {"listening", "navigation", "information", "mixed", "unvalidated"}:
            raise InputError("Declare direct/context/reject fit and listening/navigation/information/mixed/unvalidated intent.")
        label(review["rationale"], "review rationale")
        preferred = review.get("preferred_sources", {})
        if not isinstance(preferred, dict) or any(day(k) is None or v not in identities for k, v in preferred.items()):
            raise InputError("preferred_sources maps exact event dates to supplied source ids.")
        reviewed[phrase] = review
    scheduling = manifest.get("scheduling")
    if scheduling is not None:
        fields(scheduling, {"platform", "timezone", "lead_days", "not_before", "not_after", "blocked_dates"})
        label(scheduling["platform"], "target platform")
        label(scheduling["timezone"], "native timezone")
        integer(scheduling["lead_days"], 0, 90)
        first, last = day(scheduling["not_before"]), day(scheduling["not_after"])
        if first > last or (last - first).days > 732 or not isinstance(scheduling["blocked_dates"], list) or len(scheduling["blocked_dates"]) > 733:
            raise InputError("Scheduling needs ordered bounded dates and at most733 blocked dates.")
        blocked = {day(v) for v in scheduling["blocked_dates"]}
    results = []
    for phrase, cycles in sorted(grouped.items()):
        review = reviewed.get(phrase, {"fit": "unreviewed", "intent": "unvalidated", "rationale": "No explicit analyst review."})
        selected, selection_issues = [], []
        by_event = defaultdict(list)
        for cycle in cycles:
            by_event[cycle["event_date"]].append(cycle)
        for event_day, alternatives in sorted(by_event.items()):
            eligible = [c for c in alternatives if c["eligible_for_timing"]]
            chosen = review.get("preferred_sources", {}).get(event_day)
            if chosen:
                match = [c for c in alternatives if c["source_id"] == chosen]
                if not match:
                    raise InputError("A preferred source does not contain that phrase/event.")
                eligible = match if match[0]["eligible_for_timing"] else []
            if len(eligible) == 1:
                selected.append(eligible[0])
            elif len(eligible) > 1:
                selection_issues.append({"event_date": event_day, "issue": "duplicate_eligible_sources_require_explicit_preference"})
            for c in alternatives:
                c["used_for_timing"] = c in selected
        overlaps = set()
        for earlier, later in zip(selected, selected[1:]):
            if (day(later["event_date"]) - day(earlier["event_date"])).days <= window[1] - window[0]:
                overlaps.update((earlier["event_date"], later["event_date"]))
        if overlaps:
            selection_issues.append({"events": sorted(overlaps), "issue": "overlapping_event_windows_are_not_independent"})
            selected = [c for c in selected if c["event_date"] not in overlaps]
            for c in cycles:
                c["used_for_timing"] = c in selected
        forecast = prediction(selected)
        tests = []
        for i in range(2, len(selected)):
            training, held = selected[:i], selected[i]
            pred = prediction(training)
            actual = union_days(held["peak_intervals"])
            forecast_days = union_days([pred["peak_envelope"]]) if pred else set()
            tests.append({"training_events": [c["event_date"] for c in training], "held_out_event": held["event_date"],
                "predicted_peak_envelope": pred["peak_envelope"] if pred else None,
                "actual_peak_intervals": held["peak_intervals"], "hit": bool(actual & forecast_days) if pred else None,
                "minimum_calendar_distance_days": min((abs(a - b) for a in actual for b in forecast_days), default=None)})
        tested = [t for t in tests if t["hit"] is not None]
        support = "insufficient" if not forecast else "limited_two_cycles" if len(selected) == 2 else "backtested_limited_sample"
        if tested and not all(t["hit"] for t in tested):
            support = "unstable_held_out_timing"
        proposal = None
        if scheduling and forecast and review["fit"] in {"direct", "context"} and review["intent"] != "unvalidated" and support != "unstable_held_out_timing":
            lo, hi = forecast["peak_envelope"]
            dates = [next_date + timedelta(days=v - scheduling["lead_days"]) for v in range(lo, hi + 1)]
            dates = [d for d in dates if first <= d <= last and d > planned_day and d not in blocked]
            proposal = {"state": "proposed_date_window" if dates else "held_no_available_future_date",
                "platform": scheduling["platform"], "timezone": scheduling["timezone"],
                "dates": [d.isoformat() for d in dates], "declared_lead_days": scheduling["lead_days"],
                "lead_days_are_editorial_not_learned": True, "optimal_hour": None,
                "cross_platform_demand_inferred": False, "requires_native_schedule_review_and_readback": True,
                "basis": "historical source-query timing used as editorial context; no publication or reschedule executed"}
        profile = {"scope": target_scope, "analyst_review": review, "cycles": cycles,
            "independent_usable_events": len(selected), "source_selection_issues": selection_issues,
            "forecast": forecast, "timing_support": support, "whole_cycle_chronological_holdouts": tests,
            "holdout_hits": sum(t["hit"] for t in tested), "holdout_predictions": len(tested),
            "sample_size_limit": "Descriptive historical support only; small counts and holdout hits are not a calibrated probability.",
            "holdout_basis": "Chronological whole-event separation in retained retrospective data; not an archived real-time forecast or causal test.",
            "forecast_dates": [[(next_date + timedelta(days=v)).isoformat() for v in forecast["peak_envelope"]]] if forecast else [],
            "high_interest_forecast_dates": [[(next_date + timedelta(days=v)).isoformat() for v in pair]
                                             for pair in forecast["high_interest_majority_intervals"]] if forecast else [],
            "schedule_proposal": proposal, "music_demand_validated": False, "absolute_monthly_searches": None,
            "cross_request_amplitude_comparison": False, "implementation_authorised": False}
        results.append(KeywordCandidate(phrase, relationship="event-cycle-timing", score=None,
            evidence=(KeywordEvidence("Supplied Google Trends-compatible event-cycle CSVs", "usable_event_cycles", len(selected), "cycles", planned,
                target_scope["geography"], "Timing support, not search counts, audience size or attributable results."),),
            metadata={"subject": subject, "event_profile": profile, "implementation_authorised": False}))
    return PluginResult("google-trends", "plan-event-cycles", tuple(results), (
        "Input scope/event/source assertions require independent native filter and date evidence.",
        "Independent normalization permits timing comparison only; it does not permit cross-request amplitude comparison.",
        "Raw <1 censoring, missing values and threshold zeros remain distinct; no rescaling recovers lost resolution.",
        "Policy cutoffs are conservative analysis heuristics, not Google thresholds, proven optima or demand estimates.",
        "Date-window proposals require editorial/native review; no best hour, native save, schedule, paid call or register write.",
    ), {"schema": "keywordmoves-event-cycle-result/v1", "subject": subject, "planned_at": planned,
        "event": event, "scope": target_scope, "window": window, "policy": POLICY,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(), "sources": source_records,
        "source_count": len(sources), "contains_synthetic_sources": any(s["source_kind"] == "synthetic" for s in sources),
        "network_requests": 0, "implementation_authorised": False})
