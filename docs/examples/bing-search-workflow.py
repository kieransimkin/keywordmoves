"""Offline synthetic Bing workflow; refuses to overwrite an existing directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.bing_search import BingSearchPlugin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("bing-search-demo"))
    args = parser.parse_args()
    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "bing-search"
    if not fixtures.is_dir():
        parser.error("Run this example from a source checkout containing the synthetic fixtures.")
    if args.output.exists():
        parser.error("Output directory already exists; choose a new --output directory.")
    args.output.mkdir(parents=True)
    plugin = BingSearchPlugin()
    context = ExecutionContext(None)

    def run(name: str, operation: str, files: tuple[str, ...], **options) -> Path:
        paths = tuple(fixtures / f for f in files)
        result = plugin.run(PluginRequest(operation, inputs=paths, options=options), context)
        output = args.output / (name + ".json")
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        return output

    performance = run("performance", "bwt-import", ("bwt-before.json",))
    run("opportunities", "bwt-opportunities", ("bwt-before.json",), min_impressions=500, target_ctr=0.05)
    run("query-page-overlap", "bwt-overlap", ("bwt-before.json",))
    run("performance-change", "bwt-compare", ("bwt-before.json", "bwt-after.json"))
    competition = run("competition", "import-serp", ("serp-before.json",), target_host="example.com", top_n=3)
    run("rank-change", "serp-compare", ("serp-before.json", "serp-after.json"))
    demand = run("demand", "import-observations", ("observations-before.json",))
    run("observation-change", "compare", ("observations-before.json", "observations-after.json"))
    combined = plugin.run(PluginRequest("combine", inputs=(performance, competition, demand)), context)
    with (args.output / "combined.json").open("x", encoding="utf-8") as handle:
        json.dump(combined.to_dict(), handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(f"Wrote 9 synthetic reports to {args.output}. No network requests were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
