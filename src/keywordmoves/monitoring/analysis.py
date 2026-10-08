"""Source-compatible changes, collection due dates and explicit coverage gaps."""
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .store import MonitorStore
from .validation import DEMAND_KINDS, digest, matches, normal, now_stamp, stamp


def _age(capture: str, now: str) -> float:
    return (datetime.fromisoformat(now) - datetime.fromisoformat(capture)).total_seconds() / 86400


def _groups(store: MonitorStore, now: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in store.observations(as_of=now):
        groups[(normal(row["subject"]), normal(row["platform"]))].append(row)
    return groups


def _rows(watched: dict[str, Any], grouped: dict) -> list[dict[str, Any]]:
    return [row for row in grouped.get((normal(watched["subject"]), normal(watched["platform"])), [])
            if matches(watched, row)]


def _conflicting_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    signatures: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        signatures[(row["identity"], row["observed_at"])].add(digest({
            key: row[key] for key in ("value", "availability", "period_start", "period_end",
                                     "approximate", "censored", "completeness")}))
    return {key for key, values in signatures.items() if len(values) > 1}


def coverage(store: MonitorStore, *, now: str | None = None) -> dict[str, Any]:
    timestamp = now_stamp(now)
    grouped = _groups(store, timestamp)
    attempts = store.attempts()
    rows = []
    for watched in store.watches():
        measured = _rows(watched, grouped)
        latest = measured[-1] if measured else None
        usable = [row for row in measured if row["availability"] == "observed"
                  and row["value"] is not None]
        good = usable[-1] if usable else None
        conflicts = _conflicting_keys(measured)
        demand_rows = [row for row in usable if row["evidence_kind"] in DEMAND_KINDS
                       and (row["identity"], row["observed_at"]) not in conflicts]
        last_demand = demand_rows[-1] if demand_rows else None
        evidence_counts: dict[str, int] = defaultdict(int)
        for row in measured:
            evidence_counts[row["evidence_kind"]] += 1
        captures = [row for row in attempts
                    if row["watch_id"] == watched["id"] and row["started_at"] <= timestamp]
        attempt = captures[-1] if captures else None
        state = "missing"
        capture_age = report_age = None
        if latest:
            state = latest["availability"]
            if state == "observed":
                if latest["value"] is None:
                    state = "missing-value"
                elif latest["completeness"] == "partial":
                    state = "partial"
                else:
                    state = "current"
        if good:
            capture_age = _age(good["observed_at"], timestamp)
            report_age = _age(stamp(good["period_end"]), timestamp) if good["period_end"] else None
            if max(capture_age, report_age or 0) > watched["freshness_days"]:
                state = "stale" if state in {"current", "partial"} else state
        if (attempt and (latest is None or attempt["started_at"] > latest["observed_at"])
                and attempt["state"] in {"error", "interrupted", "running"}):
            state = attempt["state"]
        if conflicts:
            state = "conflicting-captures"
        rows.append({
            "watch_id": watched["id"], "subject": watched["subject"],
            "platform": watched["platform"], "phrase": watched["phrase"],
            "status": state, "latest": latest, "last_observed": good,
            "capture_age_days": round(capture_age, 2) if capture_age is not None else None,
            "report_age_days": round(report_age, 2) if report_age is not None else None,
            "has_demand_signal": bool(last_demand),
            "last_demand_signal": last_demand, "evidence_counts": dict(evidence_counts),
            "conflicting_capture_groups": len(conflicts),
            "evidence_kind": good["evidence_kind"] if good else None,
            "last_attempt": attempt, "observation_count": len(measured),
        })
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["status"]] += 1
    return {
        "schema": "keywordmoves-monitor-coverage/v1", "as_of": timestamp,
        "watch_count": len(rows), "status_counts": dict(counts), "rows": rows,
        "note": "Demand signals, owned exposure, content supply and language remain separate. "
                "A current capture can still describe an old reporting period.",
    }


def comparable(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    if before["identity"] != after["identity"]:
        return "source-or-dimensions-changed"
    if before["observed_at"] >= after["observed_at"]:
        return "capture-not-chronological"
    for row in (before, after):
        if row["availability"] != "observed":
            return "unavailable-or-failed"
        if not isinstance(row["value"], (int, float)) or isinstance(row["value"], bool):
            return "non-numeric-or-missing"
        if row["approximate"] or row["censored"]:
            return "approximate-or-censored"
        if row["completeness"] != "complete":
            return "partial-or-unknown-completeness"
        if any(row[key] == "unspecified" for key in ("scope", "window", "unit", "geography")):
            return "missing-comparison-context"
        if row["evidence_kind"] in {"proposal", "text_salience", "language_suggestion", "unknown"}:
            return "not-a-comparable-demand-or-performance-measurement"
    if (after["evidence_kind"] in {"relative_interest", "platform_search_interest"}
            or "index" in after["unit"].casefold()):
        if not before["normalization_id"] or not after["normalization_id"]:
            return "relative-index-needs-shared-normalization"
    a, b = before["period_start"], after["period_start"]
    if bool(a) != bool(b):
        return "reporting-period-missing"
    if a and b:
        start_a, end_a = date.fromisoformat(a), date.fromisoformat(before["period_end"])
        start_b, end_b = date.fromisoformat(b), date.fromisoformat(after["period_end"])
        if end_a >= start_b:
            return "reporting-periods-overlap-or-are-revisions"
        if (end_a - start_a).days != (end_b - start_b).days:
            return "reporting-period-length-changed"
    elif after["evidence_kind"] in {"property_performance", "channel_referral"}:
        return "performance-needs-reporting-periods"
    return None


def alerts(store: MonitorStore, *, now: str | None = None,
           include_acknowledged: bool = False) -> dict[str, Any]:
    timestamp = now_stamp(now)
    grouped = _groups(store, timestamp)
    acknowledged = {row[0] for row in store.db.execute("SELECT id FROM acknowledged")}
    changes, blocked = [], []
    for watched in store.watches():
        identities: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _rows(watched, grouped):
            identities[row["identity"]].append(row)
        for rows in identities.values():
            conflicts = _conflicting_keys(rows)
            if conflicts:
                blocked.append({"watch_id": watched["id"], "phrase": rows[-1]["phrase"],
                                "reason": "conflicting-capture-timestamps"})
                continue
            # Same-time copies with identical measurements are not a second sample.
            by_time = {row["observed_at"]: row for row in rows}
            rows = list(by_time.values())
            after = rows[-1]
            if len(rows) < 2:
                blocked.append({"watch_id": watched["id"], "phrase": after["phrase"],
                                "reason": "needs-two-observations"})
                continue
            before = rows[-2]
            reason = comparable(before, after)
            if reason == "reporting-periods-overlap-or-are-revisions":
                for candidate in reversed(rows[:-2]):
                    if comparable(candidate, after) is None:
                        before, reason = candidate, None
                        break
            if reason:
                blocked.append({"watch_id": watched["id"], "phrase": after["phrase"],
                                "reason": reason})
                continue
            delta = after["value"] - before["value"]
            percent = delta / abs(before["value"]) * 100 if before["value"] else None
            if not delta:
                continue
            if (watched["min_absolute_change"] is not None
                    and abs(delta) < watched["min_absolute_change"]):
                continue
            if watched["min_percent_change"] is not None and (
                    percent is None or abs(percent) < watched["min_percent_change"]):
                continue
            identifier = digest({"watch": watched["id"], "before": before["id"], "after": after["id"]})
            if identifier in acknowledged and not include_acknowledged:
                continue
            changes.append({
                "id": identifier, "watch_id": watched["id"], "subject": watched["subject"],
                "platform": after["platform"], "phrase": after["phrase"],
                "source": after["source"], "metric": after["metric"], "unit": after["unit"],
                "evidence_kind": after["evidence_kind"], "before": before["value"],
                "after": after["value"], "delta": delta, "percent_change": percent,
                "before_observed_at": before["observed_at"], "observed_at": after["observed_at"],
                "acknowledged": identifier in acknowledged,
                "interpretation": "Change in this source's compatible measurement; no causal uplift claim.",
            })
    return {"schema": "keywordmoves-monitor-alerts/v1", "as_of": timestamp,
            "changes": changes, "blocked_comparisons": blocked,
            "notifications_sent": False}


def due(store: MonitorStore, *, now: str | None = None,
        limit: int = 100) -> dict[str, Any]:
    timestamp = now_stamp(now)
    grouped = _groups(store, timestamp)
    attempts = store.attempts()
    planned = []
    for watched in store.watches():
        measured = _rows(watched, grouped)
        attempts_for_watch = [row for row in attempts if row["watch_id"] == watched["id"]
                              and row["started_at"] <= timestamp]
        if any(row["state"] == "running" for row in attempts_for_watch):
            continue
        dates = [row["observed_at"] for row in measured]
        dates.extend(row["started_at"] for row in attempts_for_watch)
        last = max(dates) if dates else None
        next_due = (datetime.fromisoformat(last) + timedelta(hours=watched["cadence_hours"])
                    ).isoformat() if last else timestamp
        if next_due > timestamp:
            continue
        planned.append({
            "watch_id": watched["id"], "watch_sha256": digest(watched),
            "subject": watched["subject"], "platform": watched["platform"],
            "phrase": watched["phrase"], "due_at": next_due,
            "collector": watched["collector"],
            "gate": "reviewed-network-opt-in" if watched["collector"] else "manual-export-or-observation",
        })
    return {"schema": "keywordmoves-collection-plan/v1", "store_id": store.identifier,
            "created_at": timestamp, "entries": planned[:limit],
            "total_due": len(planned), "output_truncated": len(planned) > limit,
            "network_started": False}


def render_html(report: dict[str, Any], change_report: dict[str, Any]) -> str:
    def escape(value: Any) -> str:
        return html.escape(str(value) if value is not None else "Unavailable", quote=True)
    rows = []
    for item in report["rows"]:
        current = item["latest"] or {}
        rows.append("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in (
            item["subject"], item["platform"], item["phrase"], item["status"],
            current.get("metric"), current.get("value"), current.get("unit"),
            item["evidence_kind"], current.get("observed_at"))) + "</tr>")
    change_rows = []
    for item in change_report["changes"]:
        change_rows.append("<li>" + escape(
            f'{item["subject"]}: {item["platform"]} {item["phrase"]} — '
            f'{item["metric"]} {item["before"]} → {item["after"]} {item["unit"]}'
        ) + "</li>")
    counts = json.dumps(report["status_counts"], ensure_ascii=False)
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>KeywordMoves evidence coverage</title>'
        '<style>body{font:16px system-ui;margin:2rem;background:#0d1421;color:#edf4ff}'
        'h1{font-size:2rem}table{border-collapse:collapse;width:100%}'
        'td,th{text-align:left;padding:.7rem;border-bottom:1px solid #34445b}'
        '.scroll{overflow:auto}input{padding:.6rem;max-width:100%;font:inherit}'
        'small{color:#b8c6d9}</style><main><h1>KeywordMoves evidence coverage</h1>'
        '<p>Demand, owned exposure, content supply and language are shown separately.</p>'
        '<p>As of ' + escape(report["as_of"]) + ' · ' + escape(counts) + '</p>'
        '<label>Filter rows <input id="filter" type="search" placeholder="Song, platform or status"></label>'
        '<div class="scroll"><table><thead><tr>'
        '<th>Subject</th><th>Platform</th><th>Phrase</th><th>Status</th><th>Metric</th>'
        '<th>Value</th><th>Unit</th><th>Evidence</th><th>Captured</th>'
        '</tr></thead><tbody id="rows">' + "".join(rows) + '</tbody></table></div>'
        '<h2>Comparable changes</h2><ul>' + "".join(change_rows) + '</ul>'
        '<small>Local report. No notifications sent. Suggestions and missing results are not volume.</small>'
        '</main><script>document.getElementById("filter").addEventListener("input",function(){'
        'const q=this.value.toLocaleLowerCase();document.querySelectorAll("#rows tr").forEach('
        'r=>r.hidden=!r.textContent.toLocaleLowerCase().includes(q));});</script></html>'
    )
