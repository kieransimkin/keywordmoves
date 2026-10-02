"""Offline, reproducible demonstration using SYNTHETIC data (not live Instagram).

Run from a checkout, after installing KeywordMoves:
    python docs/examples/instagram-workflow.py

Writes UTF-8 JSON to the local ./instagram-demo directory. Does not use an API,
create paid jobs, download models, or claim that fixture counts are real.
"""
from __future__ import annotations

import json
from pathlib import Path

from keywordmoves import PluginRegistry, PluginRequest
from keywordmoves.models import ExecutionContext


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = root / "tests" / "fixtures" / "instagram"
    output = Path("instagram-demo")
    output.mkdir(exist_ok=True)
    plugin = PluginRegistry().get("instagram")
    context = ExecutionContext(llms=None)

    def save(name: str, request: PluginRequest) -> Path:
        path = output / name
        result = plugin.run(request, context)
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
        return path

    save("sample-analysis.json", PluginRequest("import-media", inputs=(fixture / "media.json",), options={
        "source": "SYNTHETIC demo media", "scope": "synthetic-reference-posts",
        "observed_at": "2026-10-02T12:00:00Z", "include_keywords": True,
    }))
    before, after = [save(f"snapshot-{day}.json", PluginRequest("import-hashtags", inputs=(fixture / filename,), options={
        "source": "SYNTHETIC demo counts", "scope": "same-synthetic-surface",
        "observed_at": day, "sort_by": "reported_post_count",
    })) for filename, day in (("hashtag-stats.json", "2026-10-01"), ("hashtag-stats-next.json", "2026-10-02"))]
    save("changes.json", PluginRequest("compare", inputs=(before, after)))


if __name__ == "__main__":
    main()
