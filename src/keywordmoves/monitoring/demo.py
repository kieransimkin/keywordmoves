"""Reproducible synthetic monitoring demo; no song/account data or network calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import alerts, coverage, render_html
from .store import MonitorStore

AS_OF = "2025-10-08T12:00:00+00:00"
PLATFORMS = ("Google", "Bing", "DuckDuckGo", "Yahoo", "YouTube", "Instagram",
             "TikTok", "Facebook", "X", "Reddit", "Pinterest", "LinkedIn")


def generate(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    watched = {"schema": "keywordmoves-watchlist/v1", "watches": [
        {"id": "demo-" + name.lower(), "subject": "Example Song", "platform": name,
         "freshness_days": 30} for name in PLATFORMS]}
    base = {
        "subject": "Example Song", "phrase": "example song", "geography": "GB",
        "scope": "Synthetic demonstration only", "window": "7 days",
        "completeness": "complete", "observed_at": "2025-10-07",
        "approximate": False,
    }
    rows = [
        {**base, "platform": "Google", "source": "Synthetic website report",
         "metric": "impressions", "unit": "count", "evidence_kind": "property_performance",
         "period_start": "2025-09-21", "period_end": "2025-09-27",
         "observed_at": "2025-09-30", "value": 10},
        {**base, "platform": "Google", "source": "Synthetic website report",
         "metric": "impressions", "unit": "count", "evidence_kind": "property_performance",
         "period_start": "2025-09-28", "period_end": "2025-10-04", "value": 20},
        {**base, "platform": "Bing", "source": "Synthetic provider estimate",
         "metric": "estimated_searches", "unit": "monthly_searches",
         "evidence_kind": "search_volume_estimate", "window": "previous month",
         "approximate": True, "censored": True, "value": "<100"},
        {**base, "platform": "YouTube", "source": "Synthetic channel report",
         "metric": "search_referrals", "unit": "views", "evidence_kind": "channel_referral",
         "value": None, "availability": "unavailable"},
        {**base, "platform": "Instagram", "source": "Synthetic caption sample",
         "metric": "caption_language", "unit": "observation",
         "evidence_kind": "language_suggestion", "value": "Related wording, no measured demand"},
        {**base, "platform": "TikTok", "source": "Synthetic native interest report",
         "metric": "search_interest", "unit": "native_index",
         "evidence_kind": "platform_search_interest", "value": 42,
         "completeness": "unknown"},
    ]
    watch_file = output_dir / "synthetic-watchlist.json"
    input_file = output_dir / "synthetic-observations.json"
    watch_file.write_text(json.dumps(watched, indent=2), encoding="utf-8")
    input_file.write_text(json.dumps({"schema": "keywordmoves-monitor-observations/v1",
                                     "observations": rows}, indent=2), encoding="utf-8")
    with MonitorStore(output_dir / "synthetic.sqlite", create=True) as store:
        store.add_watchlist(watch_file, now=AS_OF)
        store.ingest(input_file, now=AS_OF)
        report = coverage(store, now=AS_OF)
        changes = alerts(store, now=AS_OF)
        html = render_html(report, changes).replace(
            "<main>", "<main><p><strong>Synthetic demonstration.</strong> "
            "Every measurement below is invented sample data, not demand for a real song.</p>")
        (output_dir / "coverage.html").write_text(html, encoding="utf-8")
        for name, value in (("coverage", report), ("alerts", changes), ("integrity", store.verify())):
            (output_dir / (name + ".json")).write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(output_dir), "synthetic": True, "watch_count": 12,
            "network_requests": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.output_dir), indent=2))


if __name__ == "__main__":
    main()
