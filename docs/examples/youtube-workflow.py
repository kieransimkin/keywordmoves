"""Offline synthetic YouTube evidence workflow; never uses credentials or network."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.youtube import YouTubePlugin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("youtube-demo"))
    args = parser.parse_args()
    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "youtube"
    if not fixtures.is_dir():
        parser.error("Run the example from a source checkout with tests/fixtures/youtube.")
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("The output directory already exists; nothing was overwritten.")
    plugin = YouTubePlugin()
    options = {"source": "SYNTHETIC demonstration", "scope": "synthetic:fixed-cohort:v1",
               "observed_at": "2026-10-02", "limit": 100, "include_keywords": True}

    def run(operation: str, inputs: tuple[Path, ...], filename: str, **extra):
        result = plugin.run(PluginRequest(operation, inputs=inputs, options={**options, **extra}),
                            ExecutionContext(None))
        destination = args.output / filename
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        return destination

    run("import-videos", (fixtures / "videos.json",), "sample-analysis.json")
    before = run("import-observations", (fixtures / "before.json",), "before.json", observed_at="2026-10-01")
    after = run("import-observations", (fixtures / "after.json",), "after.json", observed_at="2026-10-03")
    run("compare", (before, after), "changes.json")
    run("import-transcript", (fixtures / "subtitles.vtt",), "transcript-analysis.json")
    print(f"Wrote five synthetic outputs to {args.output}. No live data or network calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
