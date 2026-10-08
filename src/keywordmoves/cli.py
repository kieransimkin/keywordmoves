from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .credentials import (
    OSCredentialStore,
    credential_context,
    credential_settings,
    credential_status,
    read_credential,
)
from .errors import ConfigurationError, KeywordMovesError
from .models import ExecutionContext, PluginRequest
from .registry import LLMRegistry, PluginRegistry


def _option(value: str) -> tuple[str, Any]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Options must use KEY=VALUE syntax.")
    key, raw = value.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("Option keys cannot be empty.")
    # Credentials are opaque text, including numeric or boolean-looking values.
    if key in {"api_key", "access_token", "login", "password", "developer_token",
               "serpapi_key", "apify_token", "llm_api_key"}:
        return key, raw
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
    parser.add_argument("--version", action="version", version="%(prog)s 0.4.4")
    parser.add_argument("--credential-store", choices=("environment", "os-keyring"),
                        help="Optional OS keyring fallback after explicit options and environment.")
    parser.add_argument("--credential-service", help="OS keyring service/account profile (non-secret).")
    subparsers = parser.add_subparsers(dest="command", required=True)
    credentials = subparsers.add_parser("credentials", help="Manage optional OS credentials without printing values.")
    actions = credentials.add_subparsers(dest="credential_action", required=True)
    setting = actions.add_parser("set", help="Store a credential using hidden input or a secure pipe.")
    setting.add_argument("name")
    setting.add_argument("--stdin", action="store_true", dest="credential_stdin")
    status = actions.add_parser("status", help="Show presence only; no API validation or network requests.")
    status.add_argument("names", nargs="*")
    deleting = actions.add_parser("delete", help="Remove one credential from the selected OS profile.")
    deleting.add_argument("name")

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
    from .monitoring.cli import configure

    configure(subparsers)
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
    try:
        selected_store = args.credential_store
        if args.command == "credentials" and selected_store is None:
            selected_store = "os-keyring"
        with credential_context(store=selected_store, service=args.credential_service):
            return _dispatch(args)
    except KeywordMovesError as exc:
        print(f"keywordmoves: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "credentials":
        settings = credential_settings()
        if args.credential_action == "status":
            result = credential_status(args.names) if args.names else credential_status()
            print(json.dumps(result, indent=2))
            return 0
        if settings.store != "os-keyring":
            raise ConfigurationError("Use --credential-store os-keyring to store or remove credentials.")
        store = OSCredentialStore(settings.service)
        if args.credential_action == "set":
            store.set(args.name, read_credential(from_stdin=args.credential_stdin))
            print("Credential stored and readback verified; value not displayed.")
        else:
            store.delete(args.name)
            print("Credential absent from the selected OS store.")
        return 0
    keyword_plugins = PluginRegistry()
    llm_plugins = LLMRegistry()
    try:
        if args.command == "monitor":
            from .monitoring.cli import dispatch

            return dispatch(args)
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
