"""Explicit, bounded composition of existing first-party read operations."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..errors import InputError
from ..models import ExecutionContext, PluginRequest
from ..registry import PluginRegistry
from .imports import decode_payload
from .store import MonitorStore
from .validation import canonical, digest, now_stamp, observation, read_json, reject_secrets

# No paid providers, job submission, browser scraping or account setup.
READ_OPERATIONS = {
    "google-search": {"gsc-query", "gsc-pages", "gsc-query-pages"},
    "youtube": {"analytics-search", "analytics-hashtags"},
    "bing-search": {"bwt-queries", "bwt-query-pages", "bwt-keyword",
                    "bwt-related", "bwt-keyword-history"},
}


def resolved_collector(watched: dict[str, Any], now: str) -> dict[str, Any] | None:
    collector = watched["collector"]
    if collector is None:
        return None
    days = watched["dimensions"].get("period_days", 28)
    lag = watched["dimensions"].get("settle_lag_days", 3)
    for value, maximum in ((days, 366), (lag, 90)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise InputError("Collector period_days and settle_lag_days need positive integer bounds.")
    end = date.fromisoformat(now[:10]) - timedelta(days=lag)
    start = end - timedelta(days=days-1)
    values = {"{period_start}": start.isoformat(), "{period_end}": end.isoformat()}
    options = {key: values.get(value, value) if isinstance(value, str) else value
               for key, value in collector["options"].items()}
    return {**collector, "options": options}


def collect(store: MonitorStore, plan_path: Path, *, approved_sha256: str,
            allow_network: bool, account: str, max_runs: int = 5,
            max_requests_per_run: int = 5, daily_runs: int = 20,
            daily_requests: int = 100, now: str | None = None,
            registry: Any = None) -> dict[str, Any]:
    if not allow_network:
        raise InputError("Collection requires explicit --allow-network and the reviewed plan hash.")
    if not account.strip() or len(account) > 200:
        raise InputError("Use a non-secret account label shared by all collectors for this account.")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (
            max_runs, max_requests_per_run)) or not (
            1 <= max_runs <= 100 and 1 <= max_requests_per_run <= 20):
        raise InputError("Collection bounds are max_runs 1..100 and requests per run 1..20.")
    raw, plan = read_json(plan_path)
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise InputError("The plan differs from the exact reviewed SHA-256.")
    if (not isinstance(plan, dict) or plan.get("schema") != "keywordmoves-collection-plan/v1"
            or plan.get("store_id") != store.identifier):
        raise InputError("The collection plan belongs to another store or schema.")
    timestamp = now_stamp(now)
    if not isinstance(plan.get("created_at"), str):
        raise InputError("Collection plans need an explicit review timestamp.")
    created = now_stamp(plan.get("created_at"))
    if created > timestamp or (date.fromisoformat(timestamp[:10])
                               - date.fromisoformat(created[:10])).days > 1:
        raise InputError("Review a fresh collection plan, no more than one UTC day old.")
    entries = plan.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= max_runs:
        raise InputError("Plan entry count exceeds the exact invocation's finite max_runs.")
    watches = {row["id"]: row for row in store.watches()}
    prepared = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("watch_id") not in watches:
            raise InputError("A plan entry references an unknown or inactive watch.")
        watched = watches[entry["watch_id"]]
        expected = resolved_collector(watched, created)
        if digest(watched) != entry.get("watch_sha256") or entry.get("collector") != expected:
            raise InputError("The watch or collector changed after plan review.")
        collector = entry["collector"]
        if (collector is None or collector["operation"]
                not in READ_OPERATIONS.get(collector["plugin"], set())):
            raise InputError("This collector is not a supported first-party read; import reviewed files.")
        for key in ("phrase", "source", "metric", "unit", "geography", "scope", "window", "evidence_kind"):
            if not watched[key] or watched[key] == "unspecified":
                raise InputError("Live collectors need an exact phrase, source, metric, unit and scope.")
        prepared.append((watched, collector))
    if len({row[0]["id"] for row in prepared}) != len(prepared):
        raise InputError("A collection plan cannot repeat a watch.")
    results = []
    plugins = registry or PluginRegistry()
    for watched, collector in prepared:
        try:
            run = store.reserve(watched["id"], account, request_count=max_requests_per_run,
                                daily_runs=daily_runs, daily_requests=daily_requests,
                                now=timestamp, plan_sha=approved_sha256)
        except InputError:
            results.append({"watch_id": watched["id"], "state": "blocked",
                            "reason": "quota-running-or-already-attempted"})
            break
        try:
            options = {**collector["options"], "max_requests": max_requests_per_run}
            result = plugins.get(collector["plugin"]).run(
                PluginRequest(operation=collector["operation"],
                              keywords=(watched["phrase"],), options=options),
                ExecutionContext(llms=None)).to_dict()
            defaults = {key: watched[key] for key in (
                "subject", "platform", "geography", "scope", "window", "evidence_kind", "dimensions")}
            defaults.update(observed_at=timestamp)
            if options.get("start_date") and options.get("end_date"):
                defaults.update(period_start=options["start_date"], period_end=options["end_date"])
            candidates = []
            for item in result["keywords"]:
                if item["phrase"].casefold() == watched["phrase"].casefold():
                    evidence = [{**e, "observed_at": timestamp} for e in item["evidence"] if
                                e["source"] == watched["source"] and e["metric"] == watched["metric"]
                                and e["unit"] == watched["unit"]]
                    if evidence:
                        candidates.append({**item, "evidence": evidence})
            selected = {**result, "keywords": candidates}
            rows = decode_payload(selected, defaults, now=timestamp) if candidates else [
                observation({**defaults, "phrase": watched["phrase"], "source": watched["source"],
                             "metric": watched["metric"], "unit": watched["unit"], "value": None,
                             "availability": "no-data",
                             "notes": "No matching returned row; omitted or suppressed data are possible."},
                            now=timestamp)]
            reject_secrets(result)
            raw_result = canonical(result).encode("utf-8")
            store.save_snapshot(raw_result, hashlib.sha256(raw_result).hexdigest(), rows,
                                "first-party-collector:" + str(run), defaults, now=timestamp)
            state = "success" if candidates else "no-data"
            store.finish(run, state, now=timestamp)
            results.append({"watch_id": watched["id"], "attempt": run, "state": state,
                            "reserved_requests": max_requests_per_run, "rows": len(rows)})
        except Exception as exc:
            # Messages/response bodies may contain request URLs. Persist only the
            # exception class; keep source diagnostics outside the monitor.
            store.finish(run, "error", error_kind=type(exc).__name__, now=timestamp)
            results.append({"watch_id": watched["id"], "attempt": run, "state": "error",
                            "error_kind": type(exc).__name__,
                            "reserved_requests": max_requests_per_run})
    return {"schema": "keywordmoves-monitor-collection/v1", "runs": results,
            "no_paid_routes": True, "no_retries": True,
            "quota_policy": "Worst-case request reservations remain charged after failure or interruption."}


def plan_bytes(plan: dict[str, Any]) -> bytes:
    return (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
