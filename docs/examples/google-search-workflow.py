"""Offline Google Search workflow with explicitly synthetic data; never overwrites."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Run from a checkout without requiring an editable install.
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from keywordmoves.models import ExecutionContext, PluginRequest
    from keywordmoves.online.google_search import GoogleSearchPlugin

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("google-search-demo"))
    args = parser.parse_args()
    fixtures = ROOT / "tests" / "fixtures" / "google-search"
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("Output directory already exists; choose a new --output directory.")
    plugin = GoogleSearchPlugin()
    outputs: dict[str, Path] = {}

    def run(name: str, operation: str, paths: tuple[Path, ...], **options: object) -> None:
        result = plugin.run(PluginRequest(operation, inputs=paths, options=options), ExecutionContext(None))
        if result.metadata.get("live_query_performed"):
            raise RuntimeError("This demonstration must never invoke a live source.")
        destination = args.output / (name + ".json")
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        outputs[name] = destination
        print(f"{name}: {len(result.keywords)} synthetic candidates -> {destination}")

    run("search-console", "gsc-import", (fixtures / "gsc-before.json",))
    run("opportunities", "gsc-opportunities", (outputs["search-console"],), target_ctr=0.04)
    run("query-page-overlap", "gsc-overlap", (outputs["search-console"],))
    run("gsc-period-comparison", "gsc-compare", (fixtures / "gsc-before.json", fixtures / "gsc-after.json"))
    run("competition", "import-serp", (fixtures / "serp-before.json",), target_host="example.com")
    run("rank-comparison", "serp-compare", (fixtures / "serp-before.json", fixtures / "serp-after.json"))
    run("demand-and-difficulty", "import-observations", (fixtures / "observations.json",))
    run("combined", "combine", (outputs["search-console"], outputs["competition"], outputs["demand-and-difficulty"]))
    print("All evidence is synthetic. No live requests or paid actions were performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
