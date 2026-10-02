"""Offline demonstration with fabricated data only; no requests or paid jobs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.tiktok import TikTokPlugin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tiktok-demo"))
    args = parser.parse_args()
    names = ("sample-analysis.json", "snapshot-before.json", "snapshot-after.json", "comparison.json")
    if any((args.output / name).exists() for name in names):
        raise SystemExit("Demo files already exist. Select a new --output directory; nothing was overwritten.")
    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tiktok"
    if not fixtures.is_dir():
        raise SystemExit("Run this example from a source checkout with the synthetic fixtures present.")
    args.output.mkdir(parents=True, exist_ok=True)
    plugin, context = TikTokPlugin(), ExecutionContext(None)
    provenance = {"source": "Synthetic demonstration, not observed TikTok data", "scope": "synthetic:GB:7:same-settings"}

    def save(name: str, request: PluginRequest) -> Path:
        result = plugin.run(request, context)
        path = args.output / name
        with path.open("x", encoding="utf-8") as file:
            json.dump(result.to_dict(), file, indent=2, ensure_ascii=False, allow_nan=False)
            file.write("\n")
        print(f"Synthetic result: {path}")
        return path

    save(names[0], PluginRequest("import-videos", inputs=(fixtures / "videos.json",),
                                options={**provenance, "observed_at": "2026-10-02", "include_keywords": True}))
    before = save(names[1], PluginRequest("import-hashtags", inputs=(fixtures / "hashtags-before.json",),
                                         options={**provenance, "observed_at": "2026-10-01"}))
    after = save(names[2], PluginRequest("import-hashtags", inputs=(fixtures / "hashtags-after.json",),
                                        options={**provenance, "observed_at": "2026-10-02"}))
    save(names[3], PluginRequest("compare", inputs=(before, after)))
    print("All data above is fabricated; no API or network was used.")


if __name__ == "__main__":
    main()
