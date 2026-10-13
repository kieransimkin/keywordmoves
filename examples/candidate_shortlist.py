"""Synthetic offline editorial review; no real campaign or demand claims."""
import argparse
import hashlib
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, default=Path("shortlist-demo"))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
pool = args.output_dir / "pool.json"
review = args.output_dir / "review.json"
phrases = [
    ("songs about seaside memories", "coastal memories"),
    ("songs about growing up", "growing up"),
    ("summer friendship songs", "friendship"),
    ("songs about old friends", "friendship"),
    ("nostalgic songs", "nostalgia"),
    ("friendship songs for preschool", "children"),
]
pool.write_text(json.dumps({"plugin": "synthetic-pool", "operation": "import",
    "metadata": {"target_subject": "Example Song", "target_platform": "YouTube"},
    "keywords": [{"phrase": phrase, "score": None, "evidence": [],
                  "metadata": {"synthetic": True}} for phrase, _ in phrases]}, indent=2), encoding="utf-8")
review.write_text(json.dumps({"schema": "keywordmoves-shortlist-review/v1",
    "subject": "Example Song", "platform": "YouTube", "reviewed_at": "2026-10-13",
    "reviewer_kind": "analyst", "source_sha256": hashlib.sha256(pool.read_bytes()).hexdigest(),
    "decisions": [{"phrase": phrase, "fit": "reject" if group == "children" else "direct",
                   "intent": "listening", "clarity": "natural", "group": group,
                   "priority": index + 1, "rationale": "Synthetic explicit song-fit judgment."}
                  for index, (phrase, group) in enumerate(phrases)]}, indent=2), encoding="utf-8")
result = PluginRegistry().get("candidate-shortlist").run(
    PluginRequest("select", inputs=(review, pool)), ExecutionContext(None))
(args.output_dir / "shortlist.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
assert len(result.keywords) == 5 and result.metadata["shortfall"] == 0
print("Synthetic, offline shortlist: five selected; preschool mismatch excluded.")
