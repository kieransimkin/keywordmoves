"""Command-line access to private monitoring; scheduling is explicit and external."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..errors import InputError
from . import analysis
from .runner import collect, resolved_collector
from .store import MonitorStore
from .validation import matches, now_stamp, stamp


def configure(subparsers: Any) -> None:
    parser = subparsers.add_parser("monitor", help="Store and monitor source-specific evidence.")
    parser.add_argument("--store", type=Path, required=True, help="Private local SQLite database.")
    commands = parser.add_subparsers(dest="monitor_command", required=True)
    commands.add_parser("init", help="Initialise an empty private store.")
    watched = commands.add_parser("watch", help="Merge a watchlist, preserving earlier versions.")
    watched.add_argument("--input", type=Path, required=True)
    imported = commands.add_parser("import", help="Import saved evidence without network access.")
    imported.add_argument("--input", type=Path, required=True)
    for key in ("subject", "platform", "geography", "scope", "window", "evidence-kind",
                "observed-at", "period-start", "period-end", "normalization-id"):
        imported.add_argument("--" + key)
    imported.add_argument("--dimensions-json")
    imported.add_argument("--completeness", choices=("complete", "partial", "unknown"))
    for name in ("coverage", "history", "alerts", "due", "report", "health", "verify"):
        command = commands.add_parser(name)
        if name in {"coverage", "history", "alerts", "due", "report"}:
            command.add_argument("--as-of", help="Explicit UTC analysis date/time; collection uses real time.")
        if name == "history":
            command.add_argument("--watch-id")
            command.add_argument("--limit", type=int, default=1000)
        if name == "alerts":
            command.add_argument("--include-acknowledged", action="store_true")
        if name == "verify":
            command.add_argument("--capture-root", type=Path)
        if name == "due":
            command.add_argument("--limit", type=int, default=100)
    acknowledged = commands.add_parser("acknowledge")
    acknowledged.add_argument("--alert-id", action="append", required=True)
    recovered = commands.add_parser("recover", help="Mark an interrupted run; its quota stays reserved.")
    recovered.add_argument("--attempt-id", type=int, required=True)
    pruned = commands.add_parser("prune", help="Preview or confirm retention cleanup.")
    pruned.add_argument("--before", required=True)
    pruned.add_argument("--platform")
    pruned.add_argument("--confirm", action="store_true")
    collected = commands.add_parser("collect", help="Run an exact reviewed bounded first-party plan.")
    collected.add_argument("--plan", type=Path, required=True)
    collected.add_argument("--plan-sha256", required=True)
    collected.add_argument("--allow-network", action="store_true")
    collected.add_argument("--account", required=True, help="Shared non-secret account quota label.")
    for flag, default in (("max-runs", 5), ("max-requests-per-run", 5),
                          ("daily-runs", 20), ("daily-requests", 100)):
        collected.add_argument("--" + flag, type=int, default=default)
    for command in commands.choices.values():
        command.add_argument("--output", type=Path, help="UTF-8 JSON output, or HTML for report.")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".keywordmoves-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def dispatch(args: argparse.Namespace) -> int:
    command = args.monitor_command
    output = args.output
    if output is not None:
        sources = [args.store, getattr(args, "input", None), getattr(args, "plan", None)]
        if any(path is not None and path.resolve() == output.resolve() for path in sources):
            raise InputError("Output cannot overwrite the monitor store or original input evidence.")
    try:
        with MonitorStore(args.store, create=command == "init") as store:
            result: dict[str, Any]
            as_of = getattr(args, "as_of", None)
            if command == "init":
                result = {"store_id": store.identifier, "schema_version": 1, "network_started": False}
            elif command == "watch":
                result = store.add_watchlist(args.input)
            elif command == "import":
                defaults = {}
                for key in ("subject", "platform", "geography", "scope", "window", "evidence_kind",
                            "observed_at", "period_start", "period_end", "normalization_id",
                            "completeness"):
                    if getattr(args, key, None) is not None:
                        defaults[key] = getattr(args, key)
                if args.dimensions_json:
                    try:
                        defaults["dimensions"] = json.loads(args.dimensions_json)
                    except ValueError as exc:
                        raise InputError("dimensions-json must be a JSON object.") from exc
                result = store.ingest(args.input, defaults)
            elif command == "coverage":
                result = analysis.coverage(store, now=as_of)
            elif command == "history":
                if not 1 <= args.limit <= 50000:
                    raise InputError("History limit must be between 1 and 50000.")
                rows = store.observations(as_of=as_of)
                if args.watch_id:
                    watched = next((row for row in store.watches(active_only=False)
                                    if row["id"] == args.watch_id), None)
                    if watched is None:
                        raise InputError("Unknown history watch ID.")
                    rows = [row for row in rows if matches(watched, row)]
                result = {"schema": "keywordmoves-monitor-history/v1", "rows": rows[-args.limit:],
                          "total_rows": len(rows), "output_truncated": len(rows) > args.limit}
            elif command == "alerts":
                result = analysis.alerts(store, now=as_of,
                                         include_acknowledged=args.include_acknowledged)
            elif command == "acknowledge":
                identifiers = args.alert_id
                if any(not re.fullmatch(r"[a-f0-9]{64}", item) for item in identifiers):
                    raise InputError("Alert IDs must be SHA-256 hex identifiers.")
                known = {row["id"] for row in analysis.alerts(
                    store, include_acknowledged=True)["changes"]}
                if not set(identifiers) <= known:
                    raise InputError("Acknowledge only actual current change alerts.")
                with store.db:
                    store.db.executemany("INSERT OR IGNORE INTO acknowledged VALUES (?,?)",
                                         [(item, now_stamp()) for item in identifiers])
                result = {"acknowledged": identifiers, "notifications_sent": False}
            elif command == "due":
                if not 1 <= args.limit <= 1000:
                    raise InputError("Due-plan limit must be between 1 and 1000.")
                result = analysis.due(store, now=as_of, limit=args.limit)
                watches = {row["id"]: row for row in store.watches()}
                for entry in result["entries"]:
                    entry["collector"] = resolved_collector(
                        watches[entry["watch_id"]], result["created_at"])
            elif command == "report":
                if output is None:
                    raise InputError("report requires an explicit local HTML --output.")
                report = analysis.coverage(store, now=as_of)
                html = analysis.render_html(report, analysis.alerts(store, now=as_of))
                _write(output, html)
                print(json.dumps({"report": str(output), "watch_count": report["watch_count"],
                                  "status_counts": report["status_counts"]}, ensure_ascii=False))
                return 0
            elif command == "health":
                result = store.health()
            elif command == "verify":
                result = store.verify()
                result["database_verified"] = result["verified"]
                if args.capture_root:
                    from .captures import verify_captures

                    result["captures"] = verify_captures(store.observations(), args.capture_root)
                    result["verified"] = (result["database_verified"]
                                          and result["captures"]["all_references_verified"])
            elif command == "recover":
                store.finish(args.attempt_id, "interrupted", error_kind="explicit-recovery")
                result = {"attempt": args.attempt_id, "state": "interrupted", "quota_refunded": False}
            elif command == "prune":
                cutoff = stamp(args.before)
                selected = [row for row in store.observations() if row["observed_at"] < cutoff
                            and (args.platform is None or
                                 row["platform"].casefold() == args.platform.casefold())]
                result = store.prune(args.before, platform=args.platform) if args.confirm else {
                    "preview": True, "observations_to_delete": len(selected),
                    "note": "Confirming also removes raw bytes from affected mixed-source snapshots. "
                            "Original export files and external copies are outside this store."}
            elif command == "collect":
                result = collect(store, args.plan, approved_sha256=args.plan_sha256,
                                 allow_network=args.allow_network, account=args.account,
                                 max_runs=args.max_runs, max_requests_per_run=args.max_requests_per_run,
                                 daily_runs=args.daily_runs, daily_requests=args.daily_requests)
            else:
                raise InputError("Unknown monitoring command.")
            content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            if output is not None:
                _write(output, content)
                print(json.dumps({"output": str(output), "sha256": hashlib.sha256(
                    content.encode("utf-8")).hexdigest()}, ensure_ascii=False))
            else:
                print(content, end="")
            if command == "verify" and not result["verified"]:
                return 2
            if command == "collect" and any(row["state"] in {"error", "blocked"} for row in result["runs"]):
                return 2
            return 0
    except (sqlite3.Error, OSError) as exc:
        raise InputError("Monitoring storage or file access failed; preserve the store and inspect "
                         "permissions, free space and active writers before retrying.") from exc
