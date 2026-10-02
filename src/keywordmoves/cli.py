from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .errors import ConfigurationError, KeywordMovesError
from .models import ExecutionContext, PluginRequest
from .registry import LLMRegistry, PluginRegistry


def _option(value: str) -> tuple[str, Any]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Options must use KEY=VALUE syntax.")
    key, raw = value.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("Option keys cannot be empty.")
    lowered = raw.casefold()
    if lowered in {"true", "false"}:
        parsed: Any = lowered == "true"
    else:
        try:
            parsed = int(raw)
        except ValueError:
            try:
                parsed = float(raw)
            except ValueError:
                parsed = raw
    return key, parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keywordmoves",
        description="Modular keyword discovery and evidence analysis.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.2.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plugins = subparsers.add_parser("plugins", help="List keyword or LLM plugins.")
    plugins.add_argument("--kind", choices=("keyword", "llm", "all"), default="all")
    plugins.add_argument("--json", action="store_true", dest="as_json")

    run = subparsers.add_parser("run", help="Run one keyword plugin operation.")
    run.add_argument("plugin")
    run.add_argument("--operation", required=True)
    run.add_argument("--keyword", action="append", default=[])
    run.add_argument("--input", action="append", type=Path, default=[])
    run.add_argument("--llm", help="Select an LLM plugin for LLM-backed operations.")
    run.add_argument("--model", help="Select the model within the chosen LLM plugin.")
    run.add_argument(
        "--openai-api-key",
        metavar="KEY",
        help="OpenAI API key (overrides OPENAI_API_KEY; requires --llm openai). "
        "Prefer the environment to avoid exposing keys in shell history or process arguments.",
    )
    run.add_argument("--option", action="append", type=_option, default=[])
    run.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def _descriptor_dict(item: Any, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": item.name,
        "summary": item.summary,
        "capabilities": list(item.capabilities),
        "operations": list(item.operations),
        "version": item.version,
    }


def _print_text(result: Any) -> None:
    for item in result.keywords:
        score = "" if item.score is None else f" ({item.score:.4f})"
        print(f"{item.phrase}\t{item.relationship}{score}")
    for note in result.notes:
        print(f"note: {note}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    keyword_plugins = PluginRegistry()
    llm_plugins = LLMRegistry()
    try:
        if args.command == "plugins":
            items: list[dict[str, Any]] = []
            if args.kind in {"keyword", "all"}:
                items.extend(_descriptor_dict(item, "keyword") for item in keyword_plugins.describe())
            if args.kind in {"llm", "all"}:
                items.extend(_descriptor_dict(item, "llm") for item in llm_plugins.describe())
            if args.as_json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                for item in items:
                    print(f"{item['kind']}\t{item['name']}\t{item['summary']}")
            return 0

        options = dict(args.option)
        if args.llm:
            options["llm"] = args.llm
        if args.model:
            options["llm_model"] = args.model
        if args.openai_api_key is not None:
            if options.get("llm") != "openai":
                raise ConfigurationError("--openai-api-key requires --llm openai.")
            options["llm_api_key"] = args.openai_api_key
        plugin = keyword_plugins.get(args.plugin)
        result = plugin.run(
            PluginRequest(
                operation=args.operation,
                keywords=tuple(args.keyword),
                inputs=tuple(args.input),
                options=options,
            ),
            ExecutionContext(llms=llm_plugins),
        )
        if args.format == "json":
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            _print_text(result)
        return 0
    except KeywordMovesError as exc:
        print(f"keywordmoves: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
