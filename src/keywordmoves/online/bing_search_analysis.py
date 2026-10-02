"""Pure Bing evidence validation and analysis; no network or optional imports.

Counts, planning estimates, paid competition and bounded organic observations
remain different metrics. Missing observations are never imputed as zero.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate
from .common import OnlineSourceError, array, candidate, integer, number, obj, phrase

SERP_SCHEMA = "keywordmoves-bing-serp/v1"
BWT_SCHEMA = "keywordmoves-bing-webmaster/v1"
OBS_SCHEMA = "keywordmoves-bing-observations/v1"
SERP_NOTE = (
    "A bounded Bing result sample for the recorded market, device and source; "
    "not an exhaustive index or an organic ranking probability."
)
BWT_NOTE = (
    "Authorised Bing Webmaster property performance, not market search volume. "
    "Top-row selection, refresh delays and unspecified native bucket coverage may omit data."
)
BWT_METRICS = {
    "clicks": "count", "impressions": "count", "ctr": "fraction_0_1",
    "average_click_position": "position_1_based",
    "average_impression_position": "position_1_based",
}
OBS_UNITS = {
    "estimated_search_volume": "searches_per_month",
    "last_month_search_volume": "searches_last_month",
    "keyword_impressions": "impressions_in_reported_period",
    "broad_keyword_impressions": "broad_impressions_in_reported_period",
    "organic_difficulty": "provider_index_0_100",
    "paid_competition": "provider_index_0_1",
    "trend_interest": "provider_relative_index",
    "referring_domains": "count", "backlinks": "count",
}


def numeric(value: Any, *, integer_only: bool = False, high: float | None = None) -> Any:
    # Preserve integral counters exactly instead of first rounding through float.
    if isinstance(value, int) and not isinstance(value, bool):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value):
        result = int(value)
    else:
        result = number(value)
    if result is not None and (result < 0 or (high is not None and result > high) or
                               (integer_only and int(result) != result)):
        raise OnlineSourceError("A Bing metric has an invalid range or count precision.")
    return result


def option_float(o: Mapping[str, Any], key: str, default: float,
                 low: float = 0, high: float = 1) -> float:
    try:
        v = numeric(o.get(key, default), high=high)
        if v is None or v < low:
            raise OnlineSourceError("range")
        return float(v)
    except OnlineSourceError:
        raise ConfigurationError(f"{key} must be a finite number between {low} and {high}.") from None


def iso(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InputError("A date must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise InputError("Invalid calendar date.") from None


def captured(value: Any) -> str:
    if not isinstance(value, str):
        raise InputError("observed_at must be an ISO date or timezone-aware timestamp.")
    if len(value) == 10:
        return iso(value)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError
        return dt.isoformat()
    except ValueError:
        raise InputError("observed_at must include a timezone for timestamps.") from None


def _instant(value: str) -> datetime:
    dt = datetime.fromisoformat(captured(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def native_date(value: Any) -> str | None:
    """Preserve the WCF source offset, including a bucket's local calendar date."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise OnlineSourceError("Invalid Bing date value.")
    match = re.fullmatch(r"/Date\((-?\d+)([+-]\d{4})?\)/", value)
    try:
        if match:
            ms = int(match[1])
            if ms == -62135596800000:
                 # .NET DateTime.MinValue means unavailable.
                return None
            offset = match[2]
            zone = timezone.utc
            if offset:
                hours, minutes = int(offset[1:3]), int(offset[3:5])
                if hours > 23 or minutes > 59:
                    raise ValueError
                zone = timezone(timedelta(minutes=(hours * 60 + minutes) *
                                          (1 if offset[0] == "+" else -1)))
            dt = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms))
            return dt.astimezone(zone).isoformat()
        if len(value) == 10:
            return iso(value)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.year == 1:
            return None
        # Native naive ISO dates are retained, not falsely assigned the user's timezone.
        return dt.isoformat()
    except (ValueError, OverflowError, InputError):
        raise OnlineSourceError("Invalid Bing date or WCF timestamp.") from None


def url(value: Any) -> str:
    value = phrase(value)
    if any(ord(c) < 33 for c in value):
        raise OnlineSourceError("A URL must not contain whitespace or control characters.")
    try:
        p = urlsplit(value)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            raise ValueError
        _ = p.port
    except ValueError:
        raise OnlineSourceError("Expected an absolute HTTP(S) URL without credentials.") from None
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def target_host(value: Any) -> str:
    value = phrase(value).lower().rstrip(".")
    if "://" in value:
        return urlsplit(url(value)).hostname or ""
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) or ".." in value:
        raise ConfigurationError("target_host must be a hostname or HTTP(S) URL.")
    return value


def period(start: Any, end: Any) -> tuple[str, str]:
    a, b = iso(start), iso(end)
    if a > b:
        raise InputError("The reporting period must be chronological.")
    return a, b


def validate_serp(data: Any) -> dict[str, Any]:
    data = obj(data)
    if data.get("schema") != SERP_SCHEMA or data.get("engine") != "Bing":
        raise InputError("Expected a keywordmoves-bing-serp/v1 Bing snapshot.")
    ctx = obj(data.get("context"))
    # Only documented context is retained; arbitrary credentials/options are never copied.
    ctx = {k: ctx.get(k) for k in ("query", "market", "language", "device", "location",
                                  "safe_search", "time_filter", "depth", "first", "requested_pages")}
    for k in ("query", "market", "device"):
        ctx[k] = phrase(ctx[k])
    for k in ("language", "location", "safe_search", "time_filter"):
        if ctx[k] is not None:
            ctx[k] = phrase(ctx[k])
    ctx["depth"] = numeric(ctx["depth"], integer_only=True)
    for k in ("first", "requested_pages"):
        ctx[k] = numeric(1 if ctx[k] is None else ctx[k], integer_only=True)
        if ctx[k] < 1:
            raise InputError("Bing collection-window offsets/counts must be positive.")
    observed = captured(data.get("observed_at"))
    organic, seen, ranks = [], set(), set()
    raw_rows = array(data.get("organic"))
    if len(raw_rows) > 2000:
        raise InputError("A Bing snapshot may contain at most 2000 organic rows.")
    def checked_rank(raw):
        rank = numeric(obj(raw).get("rank"), integer_only=True)
        if rank is None or rank < 1:
            raise InputError("Organic ranks must be positive integers.")
        return rank
    for raw in sorted(raw_rows, key=checked_rank):
        row = obj(raw)
        link = url(row.get("url"))
        rank = numeric(row.get("rank"), integer_only=True)
        if rank is None or rank < 1:
            raise InputError("Organic ranks must be positive integers.")
        if link in seen:
            continue  # Across overlapping pages retain the first, best observation.
        if rank in ranks:
            raise InputError("Different organic results share a rank; review pagination.")
        seen.add(link)
        ranks.add(rank)
        organic.append({"rank": rank, "url": link, "title": str(row.get("title") or ""),
                        "snippet": str(row.get("snippet") or ""),
                        "absolute_feature_rank": numeric(row.get("absolute_feature_rank"),
                                                         integer_only=True)})
    organic.sort(key=lambda r: r["rank"])
    return {"schema": SERP_SCHEMA, "engine": "Bing", "source": phrase(data.get("source")),
            "observed_at": observed, "context": ctx, "organic": organic,
            "related": list(dict.fromkeys(phrase(x) for x in array(data.get("related", [])))),
            "questions": list(dict.fromkeys(phrase(x) for x in array(data.get("questions", [])))),
            "features": list(dict.fromkeys(phrase(x) for x in array(data.get("features", [])))),
            "total_results_estimate": numeric(data.get("total_results_estimate"), integer_only=True),
            "next_first": numeric(data.get("next_first"), integer_only=True),
            "more_available": data.get("more_available") is True,
            "complete_inventory": False}


def competition(snapshot: dict[str, Any], o: Mapping[str, Any]) -> KeywordCandidate:
    n = integer(o, "top_n", 10, 1, 100)
    rows = [r for r in snapshot["organic"] if r["rank"] <= n]
    hosts = Counter(urlsplit(r["url"]).hostname for r in rows)
    query = snapshot["context"]["query"]
    word_pattern = r"(?<!\w)" + re.escape(query.casefold()) + r"(?!\w)"
    title_matches = sum(bool(re.search(word_pattern, r["title"].casefold())) for r in rows)
    target = target_host(o["target_host"]) if "target_host" in o else None
    from .common import boolean
    subdomains = boolean(o, "include_subdomains")
    matches = [r for r in snapshot["organic"] if target and (
        urlsplit(r["url"]).hostname == target or
        (subdomains and (urlsplit(r["url"]).hostname or "").endswith("." + target)))]
    metrics = {"observed_organic_results_in_top_n": (len(rows), "count"),
               "distinct_hostnames_in_top_n": (len(hosts), "count"),
               "largest_hostname_share_of_observed_top_n":
                   (max(hosts.values()) / len(rows) if rows else None, "fraction_0_1"),
               "literal_title_matches_in_observed_top_n": (title_matches, "count")}
    if target:
        metrics["target_best_observed_organic_rank"] = (
            min((r["rank"] for r in matches), default=None), "position_1_based")
    return candidate(snapshot["source"], query, metrics,
                     observed_at=snapshot["observed_at"], geography=snapshot["context"]["market"],
                     relationship="organic-competition-observation",
                     metadata={"context": snapshot["context"], "top_n": n,
                               "observed_ranks": [r["rank"] for r in rows],
                               "top_n_fully_observed": set(range(1, n + 1)).issubset({r["rank"] for r in rows}),
                               "competing_hostnames": dict(hosts), "target_host": target,
                               "target_observed": bool(matches) if target else None,
                               "target_results": matches, "serp_features": snapshot["features"]},
                     note=SERP_NOTE + " Result-count estimates and paid competition are not difficulty scores.")


def serp_candidates(snapshot: dict[str, Any]) -> list[KeywordCandidate]:
    items = []
    for key, relationship in (("related", "related-search"), ("questions", "related-question")):
        for i, word in enumerate(snapshot[key], 1):
            items.append(candidate(snapshot["source"], word, {"returned_order": (i, "ordinal")},
                                   observed_at=snapshot["observed_at"],
                                   geography=snapshot["context"]["market"], relationship=relationship,
                                   metadata={"seed": snapshot["context"]["query"],
                                             "context": snapshot["context"]}, note=SERP_NOTE))
    return items


def compare_serps(before: dict[str, Any], after: dict[str, Any]) -> tuple[list, dict]:
    if before["context"] != after["context"] or before["source"] != after["source"]:
        raise InputError("Bing rank comparisons require the same source and complete search context.")
    if _instant(before["observed_at"]) >= _instant(after["observed_at"]):
        raise InputError("Rank snapshots must be supplied in chronological order.")
    a = {r["url"]: r["rank"] for r in before["organic"]}
    b = {r["url"]: r["rank"] for r in after["organic"]}
    changes = [{"url": k, "before_rank": a.get(k), "after_rank": b.get(k),
                "positions_gained": a[k] - b[k] if k in a and k in b else None,
                "status": "observed-both" if k in a and k in b else
                          "not-observed-after" if k in a else "not-observed-before"}
               for k in sorted(a.keys() | b.keys())]
    items = [candidate("Bing SERP comparison", before["context"]["query"], {
        "common_observed_urls": (len(a.keys() & b.keys()), "count"),
        "observed_url_jaccard": (len(a.keys() & b.keys()) / len(a.keys() | b.keys()) if a or b else None,
                                  "fraction_0_1")}, observed_at=after["observed_at"],
        metadata={"context": after["context"]}, note=SERP_NOTE)]
    return items, {"rank_changes": changes, "before": before["observed_at"],
                   "after": after["observed_at"]}


def validate_bwt(data: Any) -> dict[str, Any]:
    data = obj(data)
    if data.get("schema") != BWT_SCHEMA or data.get("engine") != "Bing":
        raise InputError("Expected a keywordmoves-bing-webmaster/v1 report.")
    ctx = obj(data.get("context"))
    ctx = {k: ctx.get(k) for k in ("site_url", "report_type", "scope", "granularity",
                                  "start_date", "end_date", "query", "page")}
    ctx["site_url"] = url(ctx["site_url"])
    for k in ("report_type", "scope", "granularity"):
        ctx[k] = phrase(ctx[k])
    if ctx["granularity"] not in {"native-buckets", "period"}:
        raise InputError("BWT granularity must be native-buckets or period.")
    if ctx["granularity"] == "period":
        ctx["start_date"], ctx["end_date"] = period(ctx["start_date"], ctx["end_date"])
    elif ctx["start_date"] is not None or ctx["end_date"] is not None:
        raise InputError("Do not assign an invented reporting period to native BWT buckets.")
    for k in ("query", "page"):
        if ctx[k] is not None:
            ctx[k] = phrase(ctx[k])
    rows, seen = [], set()
    for raw in array(data.get("rows")):
        r = obj(raw)
        clean = {k: r.get(k) for k in ("query", "page", "date", "position_bucket")}
        for k in ("query", "page"):
            if clean[k] is not None:
                clean[k] = phrase(clean[k])
        if clean["page"] is not None:
            clean["page"] = url(clean["page"])
        if clean["date"] is not None:
            clean["date"] = native_date(clean["date"])
        if ctx["granularity"] == "native-buckets" and clean["date"] is None:
            raise InputError("Native BWT buckets require their original date.")
        if ctx["granularity"] == "period" and clean["date"] is not None:
            raise InputError("Period-total rows must not also contain a bucket date.")
        clean["position_bucket"] = numeric(clean["position_bucket"], integer_only=True)
        key = tuple(clean.values())
        if key in seen:
            raise InputError("Duplicate BWT dimensional rows; do not sum overlapping reports.")
        seen.add(key)
        for k in BWT_METRICS:
            clean[k] = numeric(r.get(k), integer_only=k in {"clicks", "impressions"},
                               high=1 if k == "ctr" else None)
        c, i = clean["clicks"], clean["impressions"]
        if c is not None and i is not None:
            if c > i:
                raise InputError("Clicks exceed impressions; review the imported units/aggregation.")
            rate = c / i if i else None
            if clean["ctr"] is not None and rate is not None and abs(clean["ctr"] - rate) > 0.0001:
                raise InputError("CTR disagrees with clicks/impressions; use fractions, not percentages.")
            clean["ctr"] = rate
        rows.append(clean)
    if len(rows) > 50000:
        raise InputError("A BWT report supports at most 50000 rows.")
    return {"schema": BWT_SCHEMA, "engine": "Bing", "source": phrase(data.get("source")),
            "observed_at": captured(data.get("observed_at")), "context": ctx, "rows": rows,
            "complete_inventory": False}


def bwt_candidates(report: dict[str, Any]) -> list[KeywordCandidate]:
    out = []
    for r in report["rows"]:
        label = r["query"] or r["page"] or report["context"]["site_url"]
        out.append(candidate(report["source"], label,
                             {k: (r[k], unit) for k, unit in BWT_METRICS.items()},
                             observed_at=report["observed_at"], relationship="property-performance",
                             metadata={"context": report["context"],
                                       "dimensions": {k: r[k] for k in
                                                      ("query", "page", "date", "position_bucket")}},
                             note=BWT_NOTE))
    return out


def bwt_opportunities(report: dict[str, Any], o: Mapping[str, Any]) -> list[KeywordCandidate]:
    minimum = integer(o, "min_impressions", 100, 0, 10**12)
    low = option_float(o, "min_position", 4, 0, 1000)
    high = option_float(o, "max_position", 20, low, 1000)
    max_ctr = option_float(o, "max_ctr", 1)
    target = option_float(o, "target_ctr", 0) if "target_ctr" in o else None
    selected = []
    for r in report["rows"]:
        impressions, position, ctr = r["impressions"], r["average_impression_position"], r["ctr"]
        if not r["query"] or None in (impressions, position, ctr):
            continue
        if impressions < minimum or not low <= position <= high or ctr > max_ctr:
            continue
        item = bwt_candidates({**report, "rows": [r]})[0]
        if target is not None:
            from dataclasses import replace

            from ..models import KeywordEvidence
            item = replace(item, evidence=(*item.evidence, KeywordEvidence(
                "BWT conditional arithmetic", "additional_clicks_at_target_ctr_same_impressions",
                max(0, (target - ctr) * impressions), "hypothetical_count",
                observed_at=report["observed_at"], notes="Not a forecast; holds impressions constant.")))
        selected.append(item)
    return selected


def bwt_overlap(report: dict[str, Any]) -> tuple[list, dict]:
    groups: dict[tuple, list] = defaultdict(list)
    for r in report["rows"]:
        if r["query"] and r["page"]:
            groups[(r["query"], r["date"], r["position_bucket"])].append(r)
    items = []
    for (query, bucket, pos), rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        pages = sorted({r["page"] for r in rows})
        if len(pages) > 1:
            items.append(candidate("BWT query/page overlap", query,
                                   {"observed_pages_for_query": (len(pages), "count")},
                                   observed_at=report["observed_at"],
                                   metadata={"context": report["context"], "date": bucket,
                                             "position_bucket": pos, "pages": pages, "rows": rows},
                                   note="Overlapping visibility, not proof of harmful cannibalisation."))
    return items, {"groups_examined": len(groups)}


def compare_bwt(before: dict[str, Any], after: dict[str, Any]) -> tuple[list, dict]:
    a_ctx, b_ctx = before["context"], after["context"]
    if a_ctx["granularity"] != "period" or b_ctx["granularity"] != "period":
        raise InputError("Period comparisons require explicitly scoped period-total exports, not native buckets.")
    keys = set(a_ctx) - {"start_date", "end_date"}
    if any(a_ctx[k] != b_ctx[k] for k in keys) or before["source"] != after["source"]:
        raise InputError("BWT period comparisons require matching source, property and scope.")
    a, b, c, d = map(date.fromisoformat, (a_ctx["start_date"], a_ctx["end_date"],
                                       b_ctx["start_date"], b_ctx["end_date"]))
    if b >= c or b - a != d - c:
        raise InputError("Use equal-length, chronological, non-overlapping reporting periods.")
    def index(report):
        return {(r["query"], r["page"], r["position_bucket"]): r for r in report["rows"]}
    old, new = index(before), index(after)
    changes, items = [], []
    for key in sorted(old.keys() | new.keys(), key=str):
        first, last = old.get(key), new.get(key)
        changes.append({"query": key[0], "page": key[1], "position_bucket": key[2],
                        "before": first, "after": last,
                        "status": "observed-both" if first and last else "missing-row-unknown"})
        metrics = {}
        for k, unit in BWT_METRICS.items():
            x, y = first.get(k) if first else None, last.get(k) if last else None
            delta = y - x if x is not None and y is not None else None
            metrics[k + "_change"] = (delta * 100 if k == "ctr" and delta is not None else delta,
                                       "percentage_points" if k == "ctr" else unit)
        items.append(candidate("BWT period comparison", key[0] or key[1] or b_ctx["site_url"],
                               metrics, observed_at=after["observed_at"],
                               metadata={"query": key[0], "page": key[1], "before_period": [str(a), str(b)],
                                         "after_period": [str(c), str(d)], "context": b_ctx},
                               note="Missing rows remain unknown; lower position is better, not guaranteed."))
    return items, {"changes": changes}


def observations(data: Any) -> dict[str, Any]:
    data = obj(data)
    if data.get("schema") != OBS_SCHEMA or data.get("engine") != "Bing":
        raise InputError("Expected a keywordmoves-bing-observations/v1 report.")
    out, seen = [], set()
    for r in array(data.get("observations")):
        r = obj(r)
        clean = {k: phrase(r.get(k)) for k in ("phrase", "source", "scope", "metric", "unit")}
        clean["observed_at"] = captured(r.get("observed_at"))
        clean["geography"] = phrase(r["geography"]) if r.get("geography") is not None else None
        for k in ("period_start", "period_end"):
            clean[k] = iso(r[k]) if r.get(k) is not None else None
        if (clean["period_start"] is None) != (clean["period_end"] is None):
            raise InputError("Supply both observation-period boundaries or neither.")
        if clean["period_start"] is not None:
            period(clean["period_start"], clean["period_end"])
        clean["approximate"] = r.get("approximate", False)
        if not isinstance(clean["approximate"], bool):
            raise InputError("approximate must be a JSON boolean.")
        clean["value"] = numeric(r.get("value"))
        if clean["metric"] in OBS_UNITS and clean["unit"] != OBS_UNITS[clean["metric"]]:
            raise InputError("Observation units do not match the documented metric.")
        if clean["metric"] in {"organic_difficulty", "paid_competition"}:
            numeric(clean["value"], high=100 if clean["metric"] == "organic_difficulty" else 1)
        key = tuple(clean[k] for k in clean if k not in {"value", "approximate"})
        if key in seen:
            raise InputError("Duplicate observation identity; do not merge conflicting measurements.")
        seen.add(key)
        out.append(clean)
    if len(out) > 50000:
        raise InputError("Observation import exceeds 50000 rows.")
    return {"schema": OBS_SCHEMA, "engine": "Bing", "observations": out}


def observation_candidates(report: dict[str, Any]) -> list[KeywordCandidate]:
    return [candidate(r["source"], r["phrase"], {r["metric"]: (r["value"], r["unit"])},
                      observed_at=r["observed_at"], geography=r["geography"],
                      relationship="measured-observation",
                      metadata={k: r[k] for k in ("scope", "approximate", "period_start", "period_end")},
                      note="A source-scoped Bing measurement; not a universal ranking score.")
            for r in report["observations"]]


def compare_observations(before: dict, after: dict) -> tuple[list, dict]:
    def key(r):
        return tuple(r[k] for k in ("phrase", "source", "scope", "metric", "unit", "geography",
                                    "period_start", "period_end"))
    a = {key(r): r for r in before["observations"]}
    b = {key(r): r for r in after["observations"]}
    if len(a) != len(before["observations"]) or len(b) != len(after["observations"]):
        raise InputError("Supply one observation per comparable identity per snapshot.")
    items, skipped = [], []
    for identity in sorted(a.keys() | b.keys(), key=str):
        first, last = a.get(identity), b.get(identity)
        if not first or not last or first["approximate"] or last["approximate"] or \
                first["value"] is None or last["value"] is None:
            skipped.append({"phrase": identity[0], "reason": "missing, differently scoped or approximate"})
            continue
        da = datetime.fromisoformat(first["observed_at"].replace("Z", "+00:00"))
        db = datetime.fromisoformat(last["observed_at"].replace("Z", "+00:00"))
        if da.tzinfo is None:
            da = da.replace(tzinfo=timezone.utc)
        if db.tzinfo is None:
            db = db.replace(tzinfo=timezone.utc)
        days = (db - da).total_seconds() / 86400
        if days <= 0:
            raise InputError("Observation snapshots must be chronological.")
        change = last["value"] - first["value"]
        items.append(candidate("Bing observation comparison", identity[0], {
            "net_change": (change, last["unit"]), "change_per_day": (change / days, last["unit"] + "_per_day"),
            "relative_change_percent": (100 * change / first["value"] if first["value"] else None, "percent")},
            observed_at=last["observed_at"], geography=last["geography"],
            metadata={"source_metric": identity[3], "source": identity[1], "scope": identity[2]},
            note="Comparable observations only; count changes are not ranking forecasts."))
    return items, {"skipped_comparisons": skipped}


def combine_reports(reports: list[dict], *, gap: bool = False) -> tuple[list, dict]:
    """Keep each original evidence/metadata envelope; never add unlike scores."""
    grouped = defaultdict(list)
    left, right = set(), set()
    from ..models import KeywordEvidence
    for n, report in enumerate(reports):
        if not isinstance(report, dict) or report.get("plugin") != "bing-search":
            raise InputError("Combine/gap requires bing-search PluginResult JSON files.")
        for raw in array(report.get("keywords")):
            raw = obj(raw)
            word = phrase(raw.get("phrase"))
            k = word.casefold()
            (left if n == 0 else right).add(k)
            evidence = []
            for e in array(raw.get("evidence", [])):
                e = obj(e)
                v = e.get("value")
                if v is not None and not isinstance(v, str):
                    v = number(v)
                evidence.append(KeywordEvidence(phrase(e.get("source")), phrase(e.get("metric")),
                                                v, e.get("unit"), e.get("observed_at"),
                                                e.get("geography"), e.get("notes")))
            grouped[k].append((word, evidence, obj(raw.get("metadata", {}))))
    keys = left - right if gap else set(grouped)
    items = [KeywordCandidate(grouped[k][0][0], "list-gap" if gap else "combined-evidence",
                              evidence=tuple(e for _, es, _ in grouped[k] for e in es),
                              metadata={"source_entries": [{"phrase": w, "metadata": m}
                                                            for w, _, m in grouped[k]]})
             for k in sorted(keys)]
    return items, {"comparison": "left-minus-right supplied candidates" if gap else "evidence-preserving union",
                   "absence_is_not_no_demand_or_no_ranking": True}
