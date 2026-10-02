"""Local YouTube keyword evidence. A video's counters are not hashtag attribution.

No network, model imports, credentials or persistence. See docs/youtube.md for
sampling, API-derived metric controls and the distinction between tags/hashtags.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import mean, median
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginResult
from .common import boolean, choice, integer

SCHEMA = "keywordmoves-youtube-result/v1"
COUNTERS = ("views", "likes", "comments", "shares", "dislikes")
STOPWORDS = frozenset("a an and are as at be been but by can do for from has have he her his i in is it its me my of on or our she so that the their them there these they this to was we were what when where which who will with you your".split())
WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
URL = re.compile(r"https?://\S+", re.I)


def timestamp(value: Any) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise InputError("Use ISO dates or timezone-aware ISO timestamps, not relative dates.") from None


def string(value: Any, maximum: int = 100_000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise InputError("Invalid or oversized YouTube text field.")
    return value


def count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputError("Counts must be nonnegative integers or null.")
    if isinstance(value, float) and (not math.isfinite(value) or abs(value) > 2**53):
        raise InputError("Unsafe floating-point count; retain the original integer string.")
    raw = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.0+)?", raw):
        raise InputError("Counts must be exact nonnegative integers, not rounded displays.")
    try:
        v = Decimal(raw)
        if v > Decimal("1e30"):
            raise ValueError
        return int(v)
    except (ValueError, InvalidOperation):
        raise InputError("Count outside the supported range.") from None


def display_count(value: Any) -> tuple[int | None, bool]:
    if isinstance(value, str):
        value = value.strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMB])", value, re.I)
        if m:
            return int(Decimal(m[1]) * {"K": 1000, "M": 10**6, "B": 10**9}[m[2].upper()]), True
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", value):
            value = value.replace(",", "")
    return count(value), False


def hashtag(value: Any) -> str:
    raw = unicodedata.normalize("NFC", string(value, 101).strip().removeprefix("#"))
    if not raw or len(raw) > 100 or not all(
        c == "_" or unicodedata.category(c)[0] in "LMN" for c in raw
    ):
        raise InputError("Supply a literal hashtag, not a spaced phrase or URL.")
    return "#" + raw.casefold()


def hashtag_spans(text: str) -> list[tuple[str, int, int]]:
    urls = [(m.start(), m.end()) for m in URL.finditer(text)]
    spans = []
    for m in re.finditer("#", text):
        start = m.start()
        if (start and (text[start - 1].isalnum() or text[start - 1] in "_#")) or any(
            a <= start < b for a, b in urls
        ):
            continue
        end = start + 1
        while end < len(text) and (text[end] == "_" or unicodedata.category(text[end])[0] in "LMN"):
            end += 1
        if 1 < end - start <= 101:
            spans.append((hashtag(text[start:end]), start, end))
    return spans


def video_id(value: Any) -> str:
    value = string(value, 2000).strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    try:
        p = urlsplit(value)
        if (p.scheme != "https" or p.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
                or p.username or p.password or p.port not in (None, 443) or p.fragment):
            raise ValueError
        if p.hostname == "youtu.be":
            result = p.path.strip("/")
        elif p.path == "/watch":
            ids = parse_qs(p.query).get("v", [])
            if len(ids) != 1:
                raise ValueError
            result = ids[0]
        elif re.fullmatch(r"/(?:shorts|live|embed)/[A-Za-z0-9_-]{11}/?", p.path):
            result = p.path.strip("/").split("/")[1]
        else:
            raise ValueError
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", result):
            return result
    except ValueError:
        pass
    raise ConfigurationError("Supply an 11-character YouTube video ID or canonical HTTPS video URL.")


def records(value: Any, maximum: int = 10000) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v, dict) for v in value):
        raise InputError("Expected a bounded array of YouTube record objects.")
    return value


def first(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    return next((row[k] for k in keys if k in row and row[k] is not None), default)


def normalise(row: Mapping[str, Any], kind: str = "videos") -> dict[str, Any]:
    if row.get("error") or row.get("errors"):
        raise InputError("A source record reports an error; unavailable data is not zero.")
    if row.get("platform") and str(row["platform"]).casefold() != "youtube":
        raise InputError("Cannot relabel another platform's records as YouTube.")
    snippet = row.get("snippet", {})
    stats = row.get("statistics", {})
    if not isinstance(snippet, dict) or not isinstance(stats, dict):
        raise InputError("Invalid snippet/statistics object.")
    raw_id = first(row, "id", "cid") if kind == "comments" else row.get("id")
    if kind == "videos":
        raw_id = first(row, "id", "video_id", "videoId", "url", "link")
        if isinstance(raw_id, dict):
            raw_id = raw_id.get("videoId")
        rid = video_id(raw_id)
    else:
        if not isinstance(raw_id, (str, int)) or isinstance(raw_id, bool):
            raise InputError("Non-video records need an explicit stable id.")
        rid = str(raw_id)
        if not rid or len(rid) > 1000:
            raise InputError("Invalid record id.")
    title = string(first(snippet, "title", default=row.get("title")))
    description = string(first(snippet, "description", default=first(row, "description", "text", "comment")))
    if kind == "comments":
        title = ""  # A scraper may repeat the parent video title on every comment.
        description = string(first(snippet, "textOriginal", "textDisplay", default=first(row, "text", "comment")))
    tags = first(snippet, "tags", default=row.get("tags", [])) or []
    if isinstance(tags, str):
        raise InputError("Video tags must be an array; set tags_column/delimiter for a CSV.")
    if not isinstance(tags, list) or len(tags) > 1000 or any(not isinstance(t, str) for t in tags):
        raise InputError("Invalid video tag array.")
    counters, approximations = {}, []
    aliases = {"views": ("viewCount", "view_count", "views"), "likes": ("likeCount", "likes", "like_count"),
               "comments": ("commentCount", "numberOfComments", "comments_count", "comment_count"),
               "shares": ("shareCount", "shares", "share_count"), "dislikes": ("dislikeCount", "dislikes")}
    for metric, keys in aliases.items():
        # A list of comment objects must not be confused with a comment counter.
        v = first(stats, *keys, default=first(row, *keys))
        if kind == "comments" and metric == "likes":
            v = first(snippet, "likeCount", default=first(row, *keys, "voteCount"))
        value, approximate = display_count(v)
        counters[metric] = value
        if approximate:
            approximations.append(metric)
    details = row.get("contentDetails") or {}
    if not isinstance(details, dict):
        raise InputError("Invalid contentDetails object.")
    published = first(snippet, "publishedAt", default=first(row, "published_at", "date", "publishedAt"))
    if published:
        published = timestamp(published).isoformat()
    channel = first(snippet, "channelId", default=first(row, "channel_id", "channelId"))
    # Shorts are never inferred from duration or a #shorts tag.
    content_type = first(row, "content_type", "type", default="unknown")
    content_type = {"short": "shorts", "shorts": "shorts", "stream": "live", "live": "live",
                    "video": "video", "videos": "video"}.get(content_type, "unknown")
    return {"id": rid, "kind": kind, "title": title, "description": description,
            "tags": tags, "counters": counters, "approximate_counters": approximations,
            "published_at": published, "channel_id": string(channel, 200) or None,
            "content_type": content_type,
            "duration": first(details, "duration", default=row.get("duration")),
            "parent_id": first(snippet, "parentId", default=first(row, "parent_id", "replyToCid")),
            "video_id": first(snippet, "videoId", default=first(row, "video_id", "videoId")),
            "url": f"https://www.youtube.com/watch?v={rid}" if kind == "videos" else None}


def terms(text: str, max_words: int, stopwords: frozenset[str]) -> list[tuple[str, int, int]]:
    words = list(WORD.finditer(text))
    output = []
    excluded = [(m.start(), m.end()) for m in URL.finditer(text)]
    excluded += [(a, b) for _, a, b in hashtag_spans(text)]
    for i, left in enumerate(words):
        if left.group().casefold() in stopwords or len(left.group()) < 2 or any(a <= left.start() < b for a, b in excluded):
            continue
        for j in range(i, min(i + max_words, len(words))):
            right = words[j]
            if (right.group().casefold() in stopwords or any(a <= right.start() < b for a, b in excluded)
                    or (j > i and text[words[j-1].end():right.start()] not in {" ", "  ", "\t"})):
                break
            output.append((" ".join(w.group().casefold() for w in words[i:j+1]), left.start(), right.end()))
    return output


def finish(operation: str, items: list[KeywordCandidate], options: Mapping[str, Any],
           metadata: Mapping[str, Any], notes: tuple[str, ...] = ()) -> PluginResult:
    limit = integer(options, "limit", 50, 1, 10000)
    sort = choice(options, "sort_by", "provider", (
        "provider", "alphabetical", "occurrences", "reported_video_count", "reported_channel_count",
        "estimated_search_volume", "sample_views_median", "sample_views_sum", "attributed_views"))
    if sort == "alphabetical":
        items = sorted(items, key=lambda c: (c.phrase.casefold(), c.relationship))
    elif sort != "provider":
        def key(c: KeywordCandidate) -> tuple:
            values = [e.value for e in c.evidence if e.metric == sort and isinstance(e.value, (int, float))]
            # Do not pick a most flattering value out of conflicting sources.
            v = values[0] if len(set(values)) == 1 else None
            return v is None, -(v or 0), c.phrase.casefold()
        items = sorted(items, key=key)
    return PluginResult("youtube", operation, tuple(items[:limit]),
                        notes + ("Suggestions, sampled engagement and estimated searches are different evidence; missing data is not zero.",),
                        {**metadata, "schema": SCHEMA, "platform": "YouTube", "rows_received": len(items),
                         "output_truncated": len(items) > limit})


def analyse(raw: list[dict[str, Any]], options: Mapping[str, Any], *, source: str,
            observed_at: str, scope: str, kind: str = "videos", api_data: bool = False) -> tuple[list[KeywordCandidate], dict]:
    observed = timestamp(observed_at)
    include_words = boolean(options, "include_keywords", True)
    include_tags = boolean(options, "include_tags", True)
    derived = boolean(options, "derive_metrics", not api_data)
    if api_data and derived and not boolean(options, "api_derived_metrics_accepted"):
        raise ConfigurationError("Derived API metrics need api_derived_metrics_accepted=true after accepting YouTube's applicable amendment.")
    max_words = integer(options, "max_words", 3, 1, 5)
    max_spans = integer(options, "max_occurrences", 20, 0, 200)
    max_candidates = integer(options, "max_candidates", 50000, 1, 100000)
    extras = options.get("stopwords", "")
    if not isinstance(extras, str):
        raise ConfigurationError("stopwords must be comma-separated text.")
    stopwords = STOPWORDS | frozenset(t.strip().casefold() for t in extras.split(",") if t.strip())
    docs, conflicts = {}, set()
    for row in records(raw, integer(options, "max_records", 10000, 1, 50000)):
        doc = normalise(row, kind)
        if doc["id"] in docs:
            if doc != docs[doc["id"]]:
                conflicts.add(doc["id"])
            continue
        docs[doc["id"]] = doc
    groups: dict[tuple[str, str], dict] = {}
    tag_docs: dict[str, set[str]] = defaultdict(set)
    for rid, doc in docs.items():
        found = []
        for field in ("title", "description"):
            val = doc[field]
            found.extend((word, "hashtag", field, start, end) for word, start, end in hashtag_spans(val))
            if include_words:
                found.extend((word, "keyword", field, start, end) for word, start, end in terms(val, max_words, stopwords))
        if include_tags and kind == "videos":
            found.extend((" ".join(t.casefold().split()), "video-tag", "tags", i, i+1)
                         for i, t in enumerate(doc["tags"]) if t.strip())
        doc_tags = {w for w, k, *_ in found if k == "hashtag"}
        doc["literal_hashtags"] = sorted(doc_tags)
        doc["hashtag_count_exceeds_60"] = len([1 for _, k, *_ in found if k == "hashtag"]) > 60
        for tag in doc_tags:
            tag_docs[tag].add(rid)
        for word, category, field, start, end in found:
            key = (word, category)
            if key not in groups:
                if len(groups) >= max_candidates:
                    raise InputError("Candidate budget exceeded; reduce max_words/input or raise max_candidates.")
                groups[key] = {"ids": set(), "locations": set(), "spans": [], "surfaces": set()}
            g = groups[key]
            g["ids"].add(rid)
            location = (rid, field, start, end)
            if location in g["locations"]:
                continue
            g["locations"].add(location)
            surface = doc[field][start:end] if field != "tags" else doc["tags"][start]
            g["surfaces"].add(surface)
            if len(g["spans"]) < max_spans:
                g["spans"].append({"record_id": rid, "field": field, "start": start, "end": end,
                                   "offset_unit": "tag_index" if field == "tags" else "unicode_character"})
    items = []
    for (word, category), g in groups.items():
        selected = [docs[rid] for rid in sorted(g["ids"])]
        metrics: dict[str, tuple[Any, str]] = {"occurrences": (len(g["locations"]), "text_occurrences"),
                                            "sample_record_count": (len(selected), "records")}
        if derived:
            for counter in COUNTERS:
                vals = [d["counters"][counter] for d in selected if d["counters"][counter] is not None and counter not in d["approximate_counters"]]
                metrics[f"sample_{counter}_available"] = (len(vals), "records")
                for name, fn in (("sum", sum), ("mean", mean), ("median", median)):
                    metrics[f"sample_{counter}_{name}"] = (fn(vals) if vals else None, "count")
            usable = [d for d in selected if all(d["counters"][k] is not None and k not in d["approximate_counters"] for k in ("views", "likes", "comments")) and d["counters"]["views"] > 0]
            metrics["sample_likes_comments_per_view"] = (
                sum(d["counters"]["likes"] + d["counters"]["comments"] for d in usable) / sum(d["counters"]["views"] for d in usable) if usable else None, "ratio")
            metrics["sample_ratio_denominator_records"] = (len(usable), "records")
            dated = [(d, timestamp(d["published_at"])) for d in selected if d["published_at"]]
            for days in (7, 30):
                metrics[f"sample_published_last_{days}d"] = (sum(0 <= (observed - dt).total_seconds() <= days*86400 for _, dt in dated) if dated else None, "records")
            metrics["sample_known_channels"] = (len({d["channel_id"] for d in selected if d["channel_id"]}), "channels")
        note = "Text evidence and record counters in this bounded sample; never global hashtag popularity or causal attribution."
        ev = tuple(KeywordEvidence(source, m, value, unit, observed_at, options.get("country"), note) for m, (value, unit) in metrics.items())
        supporting = [{"id": d["id"], "url": d["url"], "channel_id": d["channel_id"],
                       "counters": d["counters"], "approximate_counters": d["approximate_counters"],
                       "published_at": d["published_at"], "content_type": d["content_type"],
                       "video_id": d["video_id"], "parent_id": d["parent_id"]} for d in selected[:max_spans]]
        co = []
        if category == "hashtag":
            for other, ids in tag_docs.items():
                shared = g["ids"] & ids
                if other != word and shared:
                    entry = {"hashtag": other, "shared_sample_records": len(shared)}
                    if derived:
                        entry["jaccard"] = len(shared) / len(g["ids"] | ids)
                    co.append(entry)
            co.sort(key=lambda x: (-x["shared_sample_records"], x["hashtag"]))
        items.append(KeywordCandidate(word, f"youtube-{category}", None, ev,
                     {"kind": category, "source": source, "scope": scope, "api_data": api_data,
                      "surface_forms": sorted(g["surfaces"]), "occurrences": g["spans"],
                      "occurrences_truncated": len(g["locations"]) > len(g["spans"]),
                      "supporting_records": supporting, "supporting_records_truncated": len(selected) > len(supporting),
                      "cooccurring_hashtags": co[:50], "derived_statistics_enabled": derived,
                      "verification": "literal-text-observed; not platform hashtag certification" if category == "hashtag" else "literal-source-text"}))
    items.sort(key=lambda c: (c.metadata["kind"] != "hashtag", -next(e.value for e in c.evidence if e.metric == "sample_record_count"), c.phrase))
    metadata = {"source": source, "scope": scope, "observed_at": observed_at, "country": options.get("country"),
                "record_kind": kind, "records_received": len(raw), "unique_records": len(docs),
                "duplicate_records": len(raw) - len(docs), "conflicting_duplicate_ids": sorted(conflicts),
                "api_data": api_data, "derived_statistics_enabled": derived,
                "hashtag_over_60_record_ids": [d["id"] for d in docs.values() if d["hashtag_count_exceeds_60"]],
                "record_ids": list(docs), "completeness": "bounded sample, not a population census",
                "api_refresh_or_delete_by": (observed + timedelta(days=30)).isoformat() if api_data else None}
    return items, metadata


def observations(rows: list[dict[str, Any]], *, source: str, scope: str, observed_at: str,
                 country: str | None = None, api_data: bool = False) -> list[KeywordCandidate]:
    timestamp(observed_at)
    items = []
    for row in records(rows):
        if row.get("platform") and str(row["platform"]).casefold() != "youtube":
            raise InputError("Observation platform must be YouTube.")
        word = string(row.get("phrase"), 400).strip()
        category = row.get("kind", "hashtag" if word.startswith("#") else "keyword")
        if category not in {"hashtag", "keyword", "video-tag", "traffic-source"} or not word:
            raise InputError("Observation requires a phrase and valid kind.")
        if category == "hashtag":
            word = hashtag(word)
        metric, unit = string(row.get("metric"), 100).strip(), string(row.get("unit"), 100).strip()
        if not metric or not unit:
            raise InputError("Observations require the actual metric and unit; no guessed popularity score.")
        approximate = row.get("approximate", False)
        if not isinstance(approximate, bool):
            raise InputError("approximate must be a boolean.")
        value = row.get("value")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float, str))):
            raise InputError("Invalid observation value.")
        if isinstance(value, float) and not math.isfinite(value):
            raise InputError("Non-finite observation.")
        if unit in {"videos", "channels", "views", "count", "searches_per_month"}:
            value, compact = display_count(value)
            approximate = approximate or compact
        elif value is not None and unit in {"percent", "ratio", "minutes", "seconds", "ordinal", "index"}:
            from .common import number
            raw_value = value.removesuffix("%") if unit == "percent" and isinstance(value, str) else value
            value = number(raw_value)
        date = string(row.get("observed_at", observed_at), 100)
        timestamp(date)
        src = string(row.get("source", source), 500).strip()
        sc = string(row.get("scope", scope), 1000).strip()
        if not src or not sc:
            raise InputError("Observations must preserve source and scope.")
        geo = row.get("country", country)
        if geo is not None:
            geo = string(geo, 100)
        if "api_data" in row and not isinstance(row["api_data"], bool):
            raise InputError("api_data must be a boolean.")
        availability = row.get("availability", "observed" if value is not None else "unavailable")
        if availability not in {"observed", "unavailable"} or (availability == "unavailable" and value is not None):
            raise InputError("An unavailable observation must have a null value.")
        evidence = KeywordEvidence(src, metric, value, unit, date, geo,
                                   string(row.get("notes")) or "Reported observation; not necessarily a global total.")
        items.append(KeywordCandidate(word, f"youtube-{category}-observation", None, (evidence,),
                     {"kind": category, "source": src, "scope": sc, "country": geo, "approximate": approximate,
                      "api_data": api_data or row.get("api_data") is True,
                      "window": string(row.get("window"), 300) or None, "availability": availability,
                      "raw_display": row.get("value"), "definition": string(row.get("definition"), 1000)}))
    return items


def compare(before: dict, after: dict, options: Mapping[str, Any]) -> list[KeywordCandidate]:
    def index(data: dict) -> dict:
        if not isinstance(data.get("metadata"), dict) or data["metadata"].get("schema") != SCHEMA:
            raise InputError("Compare expects two saved YouTube result JSON files.")
        out = {}
        candidates = data.get("keywords")
        for c in records(list(candidates) if isinstance(candidates, tuple) else candidates):
            meta = c.get("metadata", {})
            if not isinstance(meta, dict) or not isinstance(c.get("phrase"), str):
                raise InputError("Invalid saved candidate metadata or phrase.")
            if meta.get("approximate") or meta.get("availability") == "unavailable":
                continue
            if meta.get("api_data") and not boolean(options, "api_derived_metrics_accepted"):
                raise ConfigurationError("API-derived comparisons require api_derived_metrics_accepted=true.")
            evidence = c.get("evidence")
            for e in records(list(evidence) if isinstance(evidence, tuple) else evidence, 1000):
                if not all(isinstance(e.get(k), str) for k in ("metric", "unit", "source", "observed_at")):
                    raise InputError("Invalid saved evidence fields.")
                # Text-sample counters from changing samples are not comparable popularity growth.
                if e["metric"].startswith("sample_") or e["metric"] == "occurrences":
                    continue
                v = e.get("value")
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    continue
                key = (c["phrase"].casefold(), meta.get("kind"), e["source"], meta.get("scope"),
                       e.get("geography"), meta.get("window"), e["metric"], e["unit"], meta.get("definition"))
                if any(x is not None and not isinstance(x, str) for x in key):
                    raise InputError("Invalid saved observation scope.")
                if not key[3] or key in out:
                    raise InputError("Duplicate or unscoped observations cannot be compared.")
                out[key] = (v, timestamp(e["observed_at"]), c["phrase"])
        return out
    old, new = index(before), index(after)
    result = []
    for key in sorted(old.keys() & new.keys(), key=str):
        a, start, word = old[key]
        b, end, _ = new[key]
        days = (end-start).total_seconds()/86400
        if days <= 0:
            raise InputError("The second snapshot must be later than the first.")
        delta = b-a
        values = {"net_change": (delta, key[7]), "net_change_per_day": (delta/days, f"{key[7]}_per_day"),
                  "percent_change": (delta/a*100 if a else None, "percent")}
        ev = tuple(KeywordEvidence(key[2], key[6]+"_"+metric, v, unit, end.isoformat(), key[4],
                                   "Net change between compatible observations; not causality or search demand.") for metric,(v,unit) in values.items())
        result.append(KeywordCandidate(word, "youtube-observation-change", None, ev,
                      {"scope": key[3], "window": key[5], "before": a, "after": b,
                       "before_date": start.isoformat(), "after_date": end.isoformat(), "days": days}))
    return result
