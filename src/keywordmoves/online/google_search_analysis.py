"""Pure, provenance-preserving analysis of Google Search evidence.

Organic competition, advertising competition, Search Console impressions and
market-wide demand are deliberately different measurements. No ranking forecast
or universal difficulty score is calculated here.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, PluginResult
from .common import OnlineSourceError, array, candidate, integer, number, obj, phrase

GSC_SCHEMA = "keywordmoves-google-search-console/v1"
SERP_SCHEMA = "keywordmoves-google-serp/v1"
GSC_DIMENSIONS = ("query", "page", "country", "device", "date", "hour", "searchAppearance")
GSC_NOTE = (
    "Authorised-property performance, not market search volume. Privacy filtering, "
    "aggregation and API row limits can omit queries; missing rows do not mean zero."
)
SERP_NOTE = (
    "A bounded, provider/location/device-specific SERP snapshot, not the whole index. "
    "Observed competition signals are not a probability or guarantee of ranking."
)


def numeric(value: Any, *, low: float | None = 0, high: float | None = None) -> int | float | None:
    result = number(value)
    if result is not None and ((low is not None and result < low) or
                               (high is not None and result > high)):
        raise OnlineSourceError("A numeric metric is outside its documented range.")
    return result


def option_float(options: Mapping[str, Any], key: str, default: float,
                 low: float = 0, high: float = 1) -> float:
    try:
        value = numeric(options.get(key, default), low=low, high=high)
    except OnlineSourceError:
        raise ConfigurationError(f"{key} must be a finite number between {low} and {high}.") from None
    if value is None:
        raise ConfigurationError(f"{key} must be a finite number between {low} and {high}.")
    return float(value)


def normal_url(value: Any) -> str:
    value = phrase(value)
    try:
        p = urlsplit(value)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            raise ValueError
        _ = p.port
    except ValueError:
        raise OnlineSourceError("Expected an absolute HTTP(S) result URL without credentials.") from None
    # Query strings and paths remain significant; do not invent canonical equivalence.
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ""))


def hostname(value: str) -> str:
    return (urlsplit(normal_url(value)).hostname or "").removeprefix("www.")


def finish(operation: str, items: list[KeywordCandidate], options: Mapping[str, Any],
           metadata: Mapping[str, Any] | None = None, notes: list[str] | None = None) -> PluginResult:
    limit = integer(options, "limit", 100, 1, 50000)
    return PluginResult("google-search", operation, tuple(items[:limit]), tuple(notes or []),
                        {"platform": "Google Search", **dict(metadata or {}),
                         "candidate_count": len(items), "output_truncated": len(items) > limit})


def gsc_report(rows: list[Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Validate canonical dimensional rows; preserve Google query spelling/case."""
    for name in ("site_url", "source_scope"):
        if not isinstance(metadata.get(name), str) or not metadata[name].strip():
            raise InputError(f"Search Console metadata requires {name}.")
    try:
        for name in ("start_date", "end_date", "observed_at"):
            raw_date = metadata[name]
            if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
                raise ValueError
            date.fromisoformat(raw_date)
        if metadata["start_date"] > metadata["end_date"]:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise InputError("Search Console metadata requires valid dates and a chronological reporting period.") from None
    if not isinstance(rows, list) or len(rows) > 50000:
        raise InputError("A Search Console report requires at most 50000 rows.")
    dims = metadata.get("dimensions", ["query"])
    if not isinstance(dims, list) or any(not isinstance(d, str) or d not in GSC_DIMENSIONS for d in dims) or len(set(dims)) != len(dims):
        raise InputError("Invalid or duplicate Search Console dimensions.")
    clean, seen = [], set()
    for raw in rows:
        raw = obj(raw)
        if "keys" in raw:
            keys = array(raw["keys"])
            if len(keys) != len(dims):
                raise OnlineSourceError("Search Console returned an unexpected number of dimension keys.")
            dimension_values = dict(zip(dims, keys))
        else:
            dimension_values = obj(raw.get("dimensions"))
            if set(dimension_values) != set(dims):
                raise InputError("Every Search Console row must contain exactly the declared dimensions.")
        if any(not isinstance(v, str) for v in dimension_values.values()):
            raise InputError("Search Console dimension values must be strings.")
        key = tuple(dimension_values[d] for d in dims)
        if key in seen:
            raise InputError("Duplicate dimensional rows; aggregate raw bulk exports in SQL before importing.")
        seen.add(key)
        clicks, impressions = numeric(raw.get("clicks")), numeric(raw.get("impressions"))
        ctr = numeric(raw.get("ctr"), high=1)
        if ctr is None and clicks is not None and impressions is not None and impressions > 0:
            ctr = clicks / impressions
        if clicks is not None and impressions is not None and clicks > impressions:
            raise InputError("Clicks exceed impressions; check units and aggregation before importing.")
        if ctr is not None and not 0 <= ctr <= 1:
            raise InputError("CTR must be a fraction from 0 to 1, not a percentage.")
        position = numeric(raw.get("position"))
        clean.append({"dimensions": dimension_values, "clicks": clicks, "impressions": impressions,
                      "ctr": ctr, "position": position})
    return {**dict(metadata), "schema": GSC_SCHEMA, "dimensions": dims, "rows": clean,
            "date_timezone": "America/Los_Angeles"}


def gsc_candidates(report: dict[str, Any], observed: str) -> list[KeywordCandidate]:
    result = []
    for row in report["rows"]:
        d = row["dimensions"]
        label = d.get("query") or d.get("page") or " | ".join(d.values()) or report["site_url"]
        if "query" in d and not d["query"]:
            continue  # Never turn anonymised queries into a keyword.
        result.append(candidate("Google Search Console", label, {
            "clicks": (row["clicks"], "count"), "impressions": (row["impressions"], "count"),
            "ctr": (row["ctr"], "fraction_0_1"), "average_position": (row["position"], "position_1_based"),
        }, observed_at=observed, geography=d.get("country"), relationship="observed-query",
            metadata={"dimensions": d, "site_url": report["site_url"],
                      "start_date": report["start_date"], "end_date": report["end_date"],
                      "aggregation_type": report.get("aggregation_type"),
                      "search_type": report.get("search_type", "web")}, note=GSC_NOTE))
    return result


def gsc_opportunities(report: dict[str, Any], options: Mapping[str, Any], observed: str) -> list[KeywordCandidate]:
    """User-defined filters, not a promise of additional traffic."""
    if "query" not in report["dimensions"]:
        raise ConfigurationError("Opportunity analysis requires the query dimension.")
    low = option_float(options, "min_position", 4, 0, 1000)
    high = option_float(options, "max_position", 20, low, 1000)
    minimum = integer(options, "min_impressions", 100, 0, 10**12)
    ctr_max = option_float(options, "max_ctr", 1)  # No universal expected CTR assumption.
    target = option_float(options, "target_ctr", 0) if "target_ctr" in options else None
    items = []
    for row in report["rows"]:
        i, p, c = row["impressions"], row["position"], row["ctr"]
        if i is None or p is None or c is None or i < minimum or not low <= p <= high or c > ctr_max:
            continue
        base = gsc_candidates({**report, "rows": [row]}, observed)
        if not base:
            continue
        item = base[0]
        metrics = {"observed_impressions": (i, "count"), "observed_ctr": (c, "fraction_0_1"),
                   "observed_average_position": (p, "position_1_based")}
        if target is not None:
            metrics["additional_clicks_if_target_ctr_at_same_impressions"] = (max(0, (target - c) * i), "hypothetical_count")
        items.append(candidate("Google Search Console analysis", item.phrase, metrics,
                               observed_at=observed, geography=item.metadata["dimensions"].get("country"),
                               relationship="review-candidate", metadata={**item.metadata, "target_ctr": target},
                               note="Meets user-defined review thresholds. A CTR scenario is not a traffic forecast."))
    return sorted(items, key=lambda x: (-next(e.value for e in x.evidence if e.metric == "observed_impressions"), x.phrase))


def query_page_overlap(report: dict[str, Any], observed: str) -> list[KeywordCandidate]:
    if not {"query", "page"}.issubset(report["dimensions"]):
        raise ConfigurationError("Query/page overlap requires query and page dimensions.")
    other = [d for d in report["dimensions"] if d != "page"]
    grouped: dict[tuple, list] = defaultdict(list)
    for row in report["rows"]:
        grouped[tuple(row["dimensions"][d] for d in other)].append(row)
    items = []
    for key, rows in grouped.items():
        if len(rows) < 2 or not rows[0]["dimensions"]["query"]:
            continue
        known = [r["impressions"] for r in rows if r["impressions"] is not None]
        total = sum(known) if len(known) == len(rows) else None
        pages = [{"page": r["dimensions"]["page"], "clicks": r["clicks"],
                  "impressions": r["impressions"], "position": r["position"],
                  "share_of_returned_page_impressions": r["impressions"] / total if total else None}
                 for r in rows]
        items.append(candidate("Google Search Console analysis", rows[0]["dimensions"]["query"],
                               {"distinct_returned_pages": (len(rows), "count"),
                                "returned_page_impressions": (total, "page_impressions")},
                               observed_at=observed, relationship="query-page-overlap",
                               metadata={"dimensions": dict(zip(other, key)), "pages": pages},
                               note="Multiple ranking pages warrant review, not a proven cannibalisation problem. Page impressions are not property impressions."))
    return items


def unwrap(value: Any, schema: str, key: str) -> dict[str, Any]:
    value = obj(value)
    if value.get("schema") != schema:
        value = obj(obj(value.get("metadata")).get(key))
    if value.get("schema") != schema:
        raise InputError(f"Expected schema {schema} or a plugin result containing {key}.")
    return value


def compare_gsc(before: dict, after: dict, options: Mapping[str, Any], observed: str) -> list[KeywordCandidate]:
    fields = ("site_url", "dimensions", "search_type", "data_state", "aggregation_type", "filters", "source_scope")
    if any(before.get(k) != after.get(k) for k in fields):
        raise InputError("Search Console comparison requires identical property, dimensions, filters, data state, source scope and aggregation.")
    try:
        a0, a1, b0, b1 = [date.fromisoformat(v) for v in
                          (before["start_date"], before["end_date"], after["start_date"], after["end_date"])]
    except (ValueError, KeyError, TypeError):
        raise InputError("Comparison periods must have valid start_date and end_date.") from None
    if not a0 <= a1 < b0 <= b1 or (a1-a0).days != (b1-b0).days:
        raise InputError("Use chronological, non-overlapping periods with equal durations.")
    if {"date", "hour"}.intersection(before["dimensions"]):
        raise InputError("Compare period totals, not date/hour-keyed rows from different periods.")
    dims = before["dimensions"]
    a = {tuple(r["dimensions"][d] for d in dims): r for r in before["rows"]}
    b = {tuple(r["dimensions"][d] for d in dims): r for r in after["rows"]}
    results = []
    for key in sorted(a.keys() | b.keys()):
        old, new = a.get(key), b.get(key)
        d = (new or old)["dimensions"]
        if "query" in d and not d["query"]:
            continue
        label = d.get("query") or d.get("page") or " | ".join(d.values()) or after["site_url"]
        metrics = {}
        for name in ("clicks", "impressions", "ctr", "position"):
            x, y = old.get(name) if old else None, new.get(name) if new else None
            metrics[name + "_before"] = (x, "fraction_0_1" if name == "ctr" else "position_1_based" if name == "position" else "count")
            metrics[name + "_after"] = (y, metrics[name + "_before"][1])
            delta = y-x if x is not None and y is not None else None
            metrics[name + "_change"] = (delta * 100 if delta is not None and name == "ctr" else delta,
                                          "percentage_points" if name == "ctr" else "position_change_lower_is_better" if name == "position" else "count_change")
        results.append(candidate("Google Search Console comparison", label, metrics, observed_at=observed,
                                 relationship="period-comparison", metadata={"dimensions": d,
                                     "presence": "both" if old and new else "only_before" if old else "only_after"},
                                 note="Unreturned rows remain unknown, not zero. CTR change uses percentage points; lower position is better."))
    return results


def validate_serp(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("schema") != SERP_SCHEMA:
        raise InputError(f"Expected {SERP_SCHEMA}.")
    for key in ("query", "source", "observed_at", "scope"):
        phrase(report.get(key))
    try:
        datetime.fromisoformat(report["observed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise InputError("A SERP snapshot requires an ISO observation date or timestamp.") from None
    features = array(report.get("features_returned", []))
    if any(not isinstance(v, str) for v in features):
        raise InputError("SERP feature names must be strings.")
    raw = array(report.get("organic"))
    if len(raw) > 1000:
        raise InputError("SERP snapshot exceeds 1000 organic rows.")
    organic, seen_urls, seen_positions = [], set(), set()
    for row in raw:
        row = obj(row)
        url = normal_url(row.get("url"))
        rank = numeric(row.get("position"), low=1)
        if rank is None or int(rank) != rank:
            raise InputError("Organic ranks must be positive integers.")
        if url in seen_urls:
            continue
        if rank in seen_positions:
            raise InputError("Two different organic results have the same rank; check pagination.")
        seen_urls.add(url)
        seen_positions.add(rank)
        organic.append({"url": url, "position": int(rank), "title": str(row.get("title") or ""),
                        "snippet": str(row.get("snippet") or ""), "host": hostname(url),
                        "absolute_position": numeric(row.get("absolute_position"), low=1)})
    return {**dict(report), "organic": sorted(organic, key=lambda x: x["position"]),
            "features_returned": sorted(set(features))}


def serp_competition(report: dict[str, Any], options: Mapping[str, Any]) -> list[KeywordCandidate]:
    report = validate_serp(report)
    top = integer(options, "top_n", 10, 1, 100)
    rows = [r for r in report["organic"] if r["position"] <= top]
    counts = Counter(r["host"] for r in rows)
    n = len(rows)
    query = " ".join(report["query"].casefold().split())
    pattern = re.compile(r"(?<!\w)" + re.escape(query) + r"(?!\w)")
    matches = sum(bool(pattern.search(" ".join(r["title"].casefold().split()))) for r in rows)
    target = options.get("target_host")
    target_ranks: list[int] = []
    if target is not None:
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", target) or "." not in target:
            raise ConfigurationError("target_host must be a hostname without a URL, port or path.")
        target = target.casefold().removeprefix("www.")
        target_ranks = [r["position"] for r in report["organic"]
                        if r["host"] == target or r["host"].endswith("." + target)]
    metrics = {"organic_results_observed": (len(report["organic"]), "count"),
               "top_n_results_observed": (n, "count"),
               "top_n_distinct_hostnames": (len(counts), "count"),
               "top_n_largest_hostname_share": (max(counts.values()) / n if n else None, "fraction_0_1"),
               "top_n_title_phrase_matches": (matches, "count"),
               "approximate_reported_results": (numeric(report.get("approximate_total_results")), "approximate_results_not_competition")}
    if target is not None:
        metrics["target_best_observed_organic_rank"] = (min(target_ranks) if target_ranks else None, "position_1_based")
    return [candidate(report["source"], report["query"], metrics, observed_at=report["observed_at"],
                      geography=report.get("country"), relationship="organic-competition-evidence",
                      metadata={"scope": report["scope"], "organic_results": report["organic"],
                                "features_returned": report["features_returned"],
                                "hostname_counts_in_top_n": dict(counts), "top_n": top,
                                "target_host": target, "target_found_in_sample": bool(target_ranks) if target else None,
                                "difficulty_verdict": None}, note=SERP_NOTE)]


def serp_suggestions(report: dict[str, Any]) -> list[KeywordCandidate]:
    seen, items = set(), []
    for row in array(report.get("suggestions", [])):
        row = obj(row)
        word = phrase(row.get("phrase"))
        kind = phrase(row.get("kind", "related-search"))
        if (word.casefold(), kind) in seen:
            continue
        seen.add((word.casefold(), kind))
        items.append(candidate(report["source"], word, {"returned_order": (len(items)+1, "ordinal")},
                               observed_at=report["observed_at"], geography=report.get("country"),
                               relationship=kind, metadata={"seed": report["query"], "scope": report["scope"],
                                         "next_page_token": row.get("next_page_token")},
                               note="A returned suggestion or question, not a volume or difficulty measurement."))
    return items


def compare_serps(before: dict, after: dict, options: Mapping[str, Any]) -> tuple[list, dict]:
    a, b = validate_serp(before), validate_serp(after)
    keys = ("query", "source", "scope", "country", "language", "device", "location", "engine", "html_selection")
    if any(a.get(k) != b.get(k) for k in keys):
        raise InputError("SERP snapshots must share query, source, scope, engine and location/device settings.")
    try:
        old_time = datetime.fromisoformat(a["observed_at"].replace("Z", "+00:00"))
        new_time = datetime.fromisoformat(b["observed_at"].replace("Z", "+00:00"))
        if new_time <= old_time:
            raise ValueError
    except (ValueError, TypeError):
        raise InputError("SERP snapshots must have comparable, chronological observation timestamps.") from None
    top = integer(options, "top_n", 10, 1, 100)
    old = {r["url"]: r for r in a["organic"] if r["position"] <= top}
    new = {r["url"]: r for r in b["organic"] if r["position"] <= top}
    shared, union = old.keys() & new.keys(), old.keys() | new.keys()
    items = []
    for url in sorted(union):
        x = old[url]["position"] if url in old else None
        y = new[url]["position"] if url in new else None
        items.append(candidate("Google SERP snapshot comparison", url,
                               {"rank_before": (x, "position_1_based"), "rank_after": (y, "position_1_based"),
                                "rank_improvement": (x-y if x is not None and y is not None else None, "positions_higher_is_better")},
                               observed_at=b["observed_at"], relationship="observed-rank-change",
                               metadata={"query": b["query"], "presence": "both" if url in shared else "only_before" if url in old else "only_after"},
                               note="Missing from this bounded sample is not proof of deindexing or a known lower rank."))
    return items, {"top_n": top, "shared_urls": len(shared),
                   "url_jaccard_overlap": len(shared) / len(union) if union else None,
                   "features_before": a["features_returned"], "features_after": b["features_returned"]}


def merge_keywords(results: list[dict]) -> list[KeywordCandidate]:
    """Union evidence, without averaging incompatible volume/difficulty estimates."""
    from ..models import KeywordEvidence

    merged: dict[str, dict] = {}
    for result in results:
        for raw in array(obj(result).get("keywords")):
            raw = obj(raw)
            word = phrase(raw.get("phrase"))
            item = merged.setdefault(word.casefold(), {"phrase": word, "evidence": [], "origins": []})
            origin_evidence = []
            for e in array(raw.get("evidence", [])):
                e = obj(e)
                allowed = {k: e.get(k) for k in ("source", "metric", "value", "unit", "observed_at", "geography", "notes")}
                phrase(allowed["source"])
                phrase(allowed["metric"])
                if isinstance(allowed["value"], (dict, list, bool)):
                    raise InputError("Evidence values must be scalars, not nested objects or booleans.")
                if isinstance(allowed["value"], float) and not math.isfinite(allowed["value"]):
                    raise InputError("Evidence cannot contain non-finite numbers.")
                ev = KeywordEvidence(**allowed)
                origin_evidence.append(allowed)
                if ev not in item["evidence"]:
                    item["evidence"].append(ev)
            # Keep the evidence-to-row association: equal numbers from different
            # pages/devices/reporting periods are not one interchangeable observation.
            context_keys = ("source", "scope", "source_scope", "platform", "provider", "site_url",
                            "start_date", "end_date", "observed_at", "retrieved_at", "country",
                            "language", "device", "location", "location_code", "engine",
                            "search_type", "data_state", "aggregation_type", "filters", "dimensions")
            meta = obj(result.get("metadata", {}))
            contexts = {"result": {k: meta[k] for k in context_keys if k in meta}}
            for report_key in ("gsc_report", "serp_report"):
                if report_key in meta:
                    report = obj(meta[report_key])
                    contexts[report_key] = {k: report[k] for k in context_keys if k in report}
            item["origins"].append({"plugin": result.get("plugin"), "operation": result.get("operation"),
                                    "relationship": raw.get("relationship"), "evidence": origin_evidence,
                                    "candidate_metadata": dict(obj(raw.get("metadata", {}))),
                                    "source_context": contexts})
    return [KeywordCandidate(item["phrase"], "combined-evidence", evidence=tuple(item["evidence"]),
                             metadata={"origins": item["origins"], "metrics_not_averaged": True})
            for item in merged.values()]
