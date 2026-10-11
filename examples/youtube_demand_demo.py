"""Synthetic offline YouTube demand imports; no account or network access."""
import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, default=Path("demand-demo"))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
csv = args.output_dir / "synthetic-trends.csv"
csv.write_bytes(b"Week,example song: (Worldwide)\n2026-10-04,20\n2026-10-11,90\n")
provider = args.output_dir / "synthetic-keywordtool.json"
provider.write_text(json.dumps({"results": {"example song": {"volume": 1200,
    "cpc": 0.5, "cmp": 0.25, "trend": -0.1, "m1": 1000, "m1_month": 9, "m1_year": 2026}}}), encoding="utf-8")
registry, context = PluginRegistry(), ExecutionContext(None)
trends = registry.get("youtube").run(PluginRequest("trends-import", inputs=(csv,), options={
    "search_property": "youtube", "geography": "Worldwide", "observed_at": "2026-10-11",
    "completed_periods_only": True, "scope": "synthetic single-keyword comparison"}), context)
keywordtool = registry.get("keywordtool").run(PluginRequest("import-json", inputs=(provider,), options={
    "platform": "youtube", "country": "GLB", "observed_at": "2026-10-11"}), context)
assert trends.keywords[0].evidence[0].value == 20
assert trends.keywords[0].metadata["excluded_incomplete_periods"] == 1
assert keywordtool.keywords[0].evidence[0].value == 1200
assert keywordtool.metadata["network_requests"] == 0
for name, result in (("trends", trends), ("keywordtool", keywordtool)):
    (args.output_dir / (name + "-result.json")).write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
print("Synthetic imports passed: relative interest and independent provider estimates remain separate.")
