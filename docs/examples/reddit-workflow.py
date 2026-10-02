"""Run a synthetic, non-network Reddit workflow. Never overwrites an output directory."""
from __future__ import annotations

import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.reddit import RedditPlugin


def main() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "reddit"
    output = Path("reddit-demo")
    if output.exists():
        raise SystemExit("reddit-demo already exists; choose a new working directory to preserve it.")
    output.mkdir()
    plugin = RedditPlugin()
    context = ExecutionContext(llms=None)
    settings = {"source": "Synthetic local examples", "scope": "same-synthetic-sample",
                "observed_at": "2026-10-02T12:00:00Z", "limit": 1000, "include_records": True}

    def run(operation: str, paths: tuple[Path, ...], name: str, keywords: tuple[str, ...] = ()) -> Path:
        result = plugin.run(PluginRequest(operation, keywords, paths, settings), context)
        target = output / name
        # Exclusive creation protects files if the workflow is interrupted/re-entered.
        with target.open("x", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        return target

    posts = run("import-posts", (fixtures / "posts.json",), "post-analysis.json")
    run("import-comments", (fixtures / "comments.json",), "comment-analysis.json")
    run("competition", (fixtures / "posts.json",), "attention-context.json", ("paper planes",))
    before = run("import-observations", (fixtures / "observations-before.json",), "before.json")
    after = run("import-observations", (fixtures / "observations-after.json",), "after.json")
    run("compare", (before, after), "comparison.json")
    run("redact", (posts,), "redacted-analysis.json", ("t3_syntheticb",))
    print(f"Wrote seven synthetic outputs to {output.resolve()}. No network requests were made.")


if __name__ == "__main__":
    main()
