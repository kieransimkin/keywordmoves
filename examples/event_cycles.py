"""Real offline event-cycle analysis of expressly synthetic data, not native measurements."""
import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    scope = {"geography": "GB", "search_property": "web", "category": "All categories",
             "query_kind": "search_term", "temporal_basis": "UTC"}
    sources, paths = [], []
    for year, peak in zip((2023, 2024, 2025), (-1, 0, 0)):
        anchor = date(year, 12, 25)
        path = args.output / f"source-{year}.csv"
        path.write_text("Day,Example winter music\n" + "".join(
            f"{anchor + timedelta(days=d)},{100 if d == peak else 10}\n" for d in range(-7, 8)), encoding="utf-8")
        paths.append(path)
        sources.append({"id": f"synthetic-{year}", "input_index": len(paths),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "observed_at": "2026-10-23",
            "event_date": anchor.isoformat(), "event_reference": f"example://synthetic-event-{year}",
            "scope": dict(scope), "source_kind": "synthetic", "reference": "example://made-up-indices"})
    manifest = {"schema": "keywordmoves-event-cycle-plan/v1", "subject": "Explicitly synthetic winter example",
        "planned_at": "2026-10-24", "event": {"name": "Synthetic yearly fixture", "next_date": "2026-12-25", "reference": "example://synthetic-next-event"},
        "scope": scope, "window": [-7, 7], "sources": sources,
        "reviews": [{"phrase": "Example winter music", "fit": "direct", "intent": "listening", "rationale": "Synthetic fit assertion, not a measured native suggestion or audience."}],
        "scheduling": {"platform": "youtube", "timezone": "Europe/London", "lead_days": 7,
            "not_before": "2026-11-01", "not_after": "2026-12-31", "blocked_dates": []}}
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result = PluginRegistry().get("google-trends").run(
        PluginRequest("plan-event-cycles", inputs=(manifest_path, *paths)), ExecutionContext(llms=None))
    (args.output / "result.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    profile = result.keywords[0].metadata["event_profile"]
    assert profile["independent_usable_events"] == 3 and profile["holdout_hits"] == 1
    assert profile["schedule_proposal"]["optimal_hour"] is None
    print(json.dumps({"synthetic": True, "usable_events": 3, "timing_support": profile["timing_support"],
        "held_out_peak_bin_hits": profile["holdout_hits"], "proposed_dates": profile["schedule_proposal"]["dates"],
        "optimal_hour": None, "network_requests": 0, "native_mutations": 0}))


if __name__ == "__main__":
    main()
