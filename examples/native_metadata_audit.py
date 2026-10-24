"""Run real native metadata checks using explicitly synthetic local evidence."""
import argparse
import hashlib
import json
from pathlib import Path

from keywordmoves.models import PluginRequest
from keywordmoves.registry import PluginRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plugin = PluginRegistry().get("hashtag-planner")

    def save(name, data):
        path = args.output / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    capture = save("capture.json", {"schema": "keywordmoves-hashtag-capture/v1", "subject": "Example Song", "platform": "youtube",
        "captured_at": "2026-10-23", "source": "Explicitly synthetic local proposal", "source_kind": "local_proposal",
        "geography": "not measured", "window": "example only", "records": [{"id": "example", "text": "#ExampleSong", "reference": "example://synthetic-brief"}]})
    pool = plugin.run(PluginRequest("discover", inputs=(capture,)), None)
    pool_path = save("pool.json", pool.to_dict())
    before = save("before.json", {"schema": "keywordmoves-native-metadata/v1", "subject": "Example Song", "platform": "youtube",
        "account_id": "synthetic-account", "object_id": "synthetic-existing-object", "captured_at": "2026-10-23",
        "fields": {"title": "Original synthetic title", "description": "Original synthetic description #ExampleSong"}, "native_topic": None})
    target = {"account_id": "synthetic-account", "object_id": "synthetic-existing-object", "before_metadata_sha256": hashlib.sha256(before.read_bytes()).hexdigest()}
    review = save("native-review.json", {"schema": "keywordmoves-hashtag-native-review/v1", "subject": "Example Song", "platform": "youtube",
        "reviewed_at": "2026-10-23", "planned_for": "2026-10-24", "native_target": target,
        "source_sha256": hashlib.sha256(pool_path.read_bytes()).hexdigest(),
        "native_check": {"state": "checked", "checked_at": "2026-10-23", "reference": "example://synthetic-native-context",
            "notes": "Synthetic caller assertion, not a real native search or ownership check."},
        "decisions": [{"phrase": "#ExampleSong", "fit": "direct", "role": "brand", "safety": "clear", "priority": 1,
            "rationale": "Synthetic navigational fit; external demand is unvalidated."}]})
    plan = plugin.run(PluginRequest("plan-native", inputs=(pool_path, review, before)), None)
    plan_path = save("native-plan.json", plan.to_dict())
    final = save("native-final-copy.json", {"schema": "keywordmoves-hashtag-native-final-copy/v1", "subject": "Example Song", "platform": "youtube",
        "native_target": target, "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "checked_at": "2026-10-24",
        "fields": {"title": "Example Song — synthetic lyric video", "description": "Clearer synthetic description. #ExampleSong"}, "native_topic": None})
    audit = plugin.run(PluginRequest("audit-native", inputs=(plan_path, before, final)), None)
    save("native-audit.json", audit.to_dict())
    assert audit.metadata["passed"] and audit.metadata["media_provenance_checked"] is False
    print(json.dumps({"synthetic": True, "audit_passed": True, "media_provenance_checked": False, "network_requests": 0}))


if __name__ == "__main__":
    main()
