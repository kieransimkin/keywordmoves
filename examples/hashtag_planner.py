"""Reproducible offline demo using synthetic evidence, never real demand."""
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
        p = args.output / name
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    capture = save("capture.json", {"schema": "keywordmoves-hashtag-capture/v1", "subject": "Example Song", "platform": "youtube",
        "captured_at": "2026-10-23", "source": "Synthetic local proposal", "source_kind": "local_proposal", "geography": "not measured",
        "window": "example only", "records": [{"id": "example", "text": "#ExampleSong #IndiePop #MusicVideo #Viral", "reference": "example://synthetic-brief"}]})
    pool = plugin.run(PluginRequest("discover", inputs=(capture,)), None)
    pool_path = save("pool.json", pool.to_dict())
    roles = {"#ExampleSong": "brand", "#IndiePop": "niche", "#MusicVideo": "format", "#Viral": "topic"}
    review = save("review.json", {"schema": "keywordmoves-hashtag-review/v1", "subject": "Example Song", "platform": "youtube",
        "reviewed_at": "2026-10-23", "planned_for": "2026-10-24", "asset_sha256": "a" * 64,
        "source_sha256": hashlib.sha256(pool_path.read_bytes()).hexdigest(), "native_check": {"state": "checked", "checked_at": "2026-10-23",
            "reference": "example://synthetic-native-check", "notes": "Synthetic assertion only; no real platform check occurred."},
        "decisions": [{"phrase": c.phrase, "fit": "direct", "role": roles[c.phrase], "safety": "clear", "priority": i + 1,
            "rationale": "Synthetic exact-asset fit review; not demand."} for i, c in enumerate(pool.keywords)]})
    plan = plugin.run(PluginRequest("plan", inputs=(pool_path, review)), None)
    plan_path = save("plan.json", plan.to_dict())
    final = save("final-copy.json", {"schema": "keywordmoves-hashtag-final-copy/v1", "subject": "Example Song", "platform": "youtube",
        "asset_sha256": "a" * 64, "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "checked_at": "2026-10-24",
        "caption": "Example Song — synthetic demonstration. " + plan.metadata["caption_hashtags"], "native_topic": None})
    audit = plugin.run(PluginRequest("audit", inputs=(plan_path, final)), None)
    save("audit.json", audit.to_dict())
    assert audit.metadata["passed"]
    print(json.dumps({"synthetic": True, "selected": [c.phrase for c in plan.keywords], "audit_passed": True, "network_requests": 0}))


if __name__ == "__main__":
    main()
