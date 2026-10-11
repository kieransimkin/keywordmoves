"""Synthetic offline capture and source-preserving phrase focus."""
import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, default=Path("vidiq-demo"))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
capture = args.output_dir / "synthetic-capture.json"
capture.write_text(json.dumps({"mode": "research", "country": "GB", "seedKeyword": {
    "keyword": "example song", "estimatedMonthlySearch": 0, "monthlySearchesDisplay": "<750", "metricsAsOf": None},
    "relatedKeywords": [{"keyword": "example music", "estimatedMonthlySearch": 1200, "countryVolume": 80,
                         "metricsAsOf": "2026-07-01T00:00:00Z"}, {"keyword": "preschool song", "estimatedMonthlySearch": 5000}]}), encoding="utf-8")
registry, context = PluginRegistry(), ExecutionContext(None)
imported = registry.get("vidiq").run(PluginRequest("import-json", inputs=(capture,), options={"observed_at": "2026-10-11"}), context)
result_path = args.output_dir / "imported.json"
result_path.write_text(json.dumps(imported.to_dict(), indent=2), encoding="utf-8")
focused = registry.get("candidate-focus").run(PluginRequest("related", ("example song",), (result_path,), {"include_any": "song|music", "exclude_any": "preschool"}), context)
(args.output_dir / "focused.json").write_text(json.dumps(focused.to_dict(), indent=2), encoding="utf-8")
print(f"Synthetic, no-network demonstration: {len(imported.keywords)} imported, {len(focused.keywords)} retained.")
