"""TikTok evidence analysis without network access or optional dependencies.

A sampled video's engagement belongs to the VIDEO, not separately to each hashtag.
Reported totals and search estimates are never inferred from a sampled collection.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import mean, median
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginResult
from .common import boolean, integer, number

SCHEMA = "keywordmoves-tiktok-result/v1"
COUNT_FIELDS = {
    "views": ("view_count", "playCount", "views"),
    "likes": ("like_count", "diggCount", "likes"),
    "comments": ("comment_count", "commentCount", "comments"),
    "shares": ("share_count", "shareCount", "shares"),
    "saves": ("favorites_count", "collectCount", "save_count", "saves"),
    "reposts": ("repost_count", "repostCount", "reposts"),
}
# These are distinct provider-reported measurements, not an invented total score.
REPORTED_FIELDS = {
    "reported_post_count": ("reported_post_count", "publishCntAll", "videoCount"),
    "reported_view_count": ("reported_view_count", "videoViewsAll", "viewCount"),
    "period_post_count": ("period_post_count", "publishCnt"),
    "period_view_count": ("period_view_count", "videoViews"),
}
NATIVE_DIMENSIONS = (
    "trend", "longevity", "countryInfo", "industryInfo", "audienceAges", "audienceInterests",
    "audienceCountries", "isPromoted", "trendingType", "relatedHashtags", "recommendHashtags", "recList",
)
STOPWORDS = frozenset("a an and are as at be been but by can do for from has have he her his i in is it its me my of on or our she so that the their them there these they this to was we were what when where which who will with you your".split())
WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)


def hashtag(value: Any) -> str:
    if not isinstance(value, str):
        raise InputError("A hashtag must be text, not an ID or other value.")
    name = unicodedata.normalize("NFC", value.strip().removeprefix("#"))
    if not 1 <= len(name) <= 100 or not all(
        c == "_" or unicodedata.category(c)[0] in "LMN" for c in name
    ):
        raise InputError("Hashtags must be literal single tags (letters, marks, numbers, underscore).")
    return "#" + name.casefold()


def hashtag_spans(value: str) -> list[tuple[str, int, int]]:
    found = []
    for match in re.finditer("#", value):
        start = match.start()
        if start and (value[start - 1].isalnum() or value[start - 1] in "_#"):
            continue
        end = start + 1
        while end < len(value) and (
            value[end] == "_" or unicodedata.category(value[end])[0] in "LMN"
        ):
            end += 1
        if 1 < end - start <= 101:
            found.append((hashtag(value[start:end]), start, end))
    return found


def count(value: Any) -> int | None:
    """Exact nonnegative counts. Never convert 64-bit integers through float."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputError("A TikTok count must be an exact nonnegative integer or null.")
    if isinstance(value, float) and (not math.isfinite(value) or abs(value) > 2**53):
        raise InputError("Unsafe floating-point count; supply the original integer or string.")
    try:
        raw = str(value).strip()
        if not re.fullmatch(r"\d+(?:\.0+)?", raw):
            raise ValueError
        result = Decimal(raw)
        if result > Decimal("1e30"):
            raise ValueError
        return int(result)
    except (ValueError, InvalidOperation):
        raise InputError("A TikTok count must be an exact nonnegative integer or null.") from None


def displayed_count(value: Any) -> tuple[int | None, bool]:
    """Decode English compact displays, retaining approximation explicitly."""
    if isinstance(value, str):
        raw = value.strip()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMB])", raw, re.I)
        if match:
            return int(Decimal(match[1]) * {"k": 1000, "m": 10**6, "b": 10**9}[match[2].lower()]), True
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
            value = raw.replace(",", "")
    return count(value), False


def timestamp(value: Any) -> datetime:
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(float(value), timezone.utc)
        if not isinstance(value, str):
            raise ValueError
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            # A date-only observation is unambiguously defined here as UTC midnight.
            if len(value) != 10:
                raise ValueError
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        raise InputError("Dates must be ISO dates, timezone-aware ISO timestamps or Unix seconds.") from None


def first(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default


def _string(value: Any, maximum: int = 100000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum:
        raise InputError("Unexpected or oversized text field in a TikTok record.")
    return value


def identifier(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise InputError("TikTok IDs must be strings or integers, never floating-point numbers.")
    result = str(value)
    if len(result) > 200 or any(ord(c) < 32 for c in result):
        raise InputError("Invalid TikTok identifier.")
    return result


def video_url(value: Any) -> str:
    """Only canonical public video URLs, not redirectors or arbitrary fetch URLs."""
    if not isinstance(value, str):
        raise ConfigurationError("Supply a full TikTok video URL.")
    try:
        p = urlsplit(value)
        if (p.scheme != "https" or p.hostname != "www.tiktok.com" or p.username or p.password
                or p.port not in (None, 443) or p.fragment
                or not re.fullmatch(r"/@[A-Za-z0-9_.]{1,40}/video/[0-9]{1,25}/?", p.path)):
            raise ValueError
        return "https://www.tiktok.com" + p.path.rstrip("/")
    except ValueError:
        raise ConfigurationError("Use a full https://www.tiktok.com/@name/video/ID URL, not a short link.") from None


def records(value: Any, maximum: int = 10000) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v, dict) for v in value):
        raise InputError("Expected a bounded array of TikTok record objects; check records_path and max_records.")
    return value


def normalise_video(row: Mapping[str, Any], kind: str = "videos") -> dict[str, Any]:
    if row.get("error") or row.get("errorCode"):
        raise InputError("A collection row reports an error; do not interpret unavailable/private data as zero.")
    if row.get("platform") is not None and str(row["platform"]).casefold() != "tiktok":
        raise InputError("TikTok analysis cannot relabel another platform as TikTok.")
    stats = first(row, "stats", default={})
    author = first(row, "authorMeta", "author", default={})
    music = first(row, "musicMeta", "music", default={})
    video = first(row, "videoMeta", "video", default={})
    if not all(isinstance(v, dict) for v in (stats, author, music, video)):
        raise InputError("Unexpected TikTok stats/author/music/video structure.")
    caption = _string(first(row, "caption", "video_description", "text", "desc", "title", default=""))
    transcript = _string(first(row, "voice_to_text", "transcript", default=""))
    native_tags = first(row, "hashtags", "hashtag_names", default=[])
    if not isinstance(native_tags, list) or len(native_tags) > 500:
        raise InputError("Hashtag annotations must be a bounded array.")
    tags = {tag for tag, _, _ in hashtag_spans(caption)}
    for tag in native_tags:
        # Apify 'title' is a description of the tag, not its literal name.
        name = first(tag, "name", "hashtag_name") if isinstance(tag, dict) else tag
        tags.add(hashtag(name))
    raw_time = first(row, "createTimeISO", "created_at", "create_time", "createTime")
    created = timestamp(raw_time).isoformat() if raw_time not in (None, "") else None
    metrics = {}
    for name, aliases in COUNT_FIELDS.items():
        metrics[name] = count(first(row, *aliases, default=first(stats, *aliases)))
    if kind != "videos":
        # Comments, bios and local text must never inflate video metrics.
        metrics = {name: (metrics[name] if kind == "comments" and name == "likes" else None)
                   for name in COUNT_FIELDS}
    raw_id = first(row, "id", "comment_id") if kind == "comments" else first(row, "id", "video_id", "aweme_id")
    link = first(row, "webVideoUrl", "share_url", "url")
    canonical = None
    if link is not None:
        try:
            canonical = video_url(link)
        except ConfigurationError:
            # A malformed source URL isn't followed or used to deduplicate records.
            canonical = None
    duration = number(first(row, "duration", "video_duration", default=video.get("duration")))
    if duration is not None and duration < 0:
        raise InputError("Video duration cannot be negative.")
    return {
        "id": identifier(raw_id), "url": canonical, "caption": caption, "transcript": transcript,
        "tags": tags, "created_at": created, "metrics": metrics,
        "author": identifier(first(author, "id", "name", default=row.get("username"))),
        "music_id": identifier(first(row, "music_id", default=first(music, "musicId", "id"))),
        "music_name": _string(first(music, "musicName", "title", default=""), 1000),
        "region_code": _string(row.get("region_code"), 20), "duration": duration,
        "format": "slideshow" if boolean(row, "isSlideshow") else "video",
    }


def _keywords(text: str, maximum_words: int, extra_stopwords: set[str]) -> set[str]:
    """Literal adjacent words; punctuation, newlines, tags and mentions break phrases."""
    result = set()
    stops = STOPWORDS | extra_stopwords
    matches = list(WORD.finditer(text))
    for i, token in enumerate(matches):
        if token.start() and text[token.start() - 1] in "#@":
            continue
        words = []
        last = token.start()
        for match in matches[i:i + maximum_words]:
            if words and (not text[last:match.start()].isspace() or
                          any(c in text[last:match.start()] for c in "\r\n")):
                break
            word = unicodedata.normalize("NFC", match.group()).casefold()
            if word in stops or len(word) < 2 or word.isdecimal():
                break
            words.append(word)
            last = match.end()
            if len(" ".join(words)) <= 160:
                result.add(" ".join(words))
    return result


def analyse(rows: list[dict[str, Any]], options: Mapping[str, Any], source: str,
            observed: str, *, kind: str = "videos", known: tuple[str, ...] = ()) -> tuple[list[KeywordCandidate], dict[str, Any]]:
    maximum = integer(options, "max_records", 2000, 1, 10000)
    rows = records(rows, maximum)
    now = timestamp(observed)
    max_words = integer(options, "max_words", 3, 1, 5)
    include_words = boolean(options, "include_keywords", kind in {"comments", "text", "profile"})
    include_transcript = boolean(options, "include_transcript")
    stop = options.get("stopwords", "")
    if not isinstance(stop, str):
        raise ConfigurationError("stopwords must be a comma-separated string.")
    extra = {s.strip().casefold() for s in stop.split(",") if s.strip()}
    samples: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    duplicates, conflicts = 0, 0
    for row in rows:
        item = normalise_video(row, kind)
        key = item["id"] or item["url"]
        if key and key in seen:
            duplicates += 1
            prior = samples[seen[key]]
            for field in ("caption", "transcript", "created_at", "author", "music_id", "music_name", "duration"):
                if prior[field] in (None, "") and item[field] not in (None, ""):
                    prior[field] = item[field]
                elif prior[field] not in (None, "") and item[field] not in (None, "") and prior[field] != item[field]:
                    conflicts += 1
            prior["tags"] |= item["tags"]
            for metric, value in item["metrics"].items():
                if prior["metrics"][metric] is None:
                    prior["metrics"][metric] = value
                elif value is not None and value != prior["metrics"][metric]:
                    conflicts += 1  # first observation retained, no summation of duplicate counts
        else:
            if key:
                seen[key] = len(samples)
            samples.append(item)
    memberships: dict[str, set[int]] = defaultdict(set)
    fields: dict[str, set[str]] = defaultdict(set)
    spans: dict[str, list[dict[str, Any]]] = defaultdict(list)
    max_spans = integer(options, "max_occurrences", 10, 0, 100)
    for index, row in enumerate(samples):
        for tag in sorted(row["tags"]):
            memberships[tag].add(index)
            fields[tag].add("caption_or_hashtag_annotation")
        for tag, start, end in hashtag_spans(row["caption"]):
            if len(spans[tag]) < max_spans:
                spans[tag].append({"record_index": index, "field": "caption", "start": start, "end": end})
        if include_words:
            for field in ("caption", "transcript") if include_transcript else ("caption",):
                for word in sorted(_keywords(row[field], max_words, extra)):
                    memberships[word].add(index)
                    fields[word].add(field)
        if len(memberships) > 10000:
            raise InputError("More than 10,000 distinct candidates; narrow input or disable caption keywords.")
    for tag in known:
        memberships.setdefault(hashtag(tag), set())
    tags = [key for key in memberships if key.startswith("#")]
    unit_name = {"videos": "video", "comments": "comment", "text": "text_record", "profile": "profile"}[kind]
    result = []
    for term, indices in memberships.items():
        selected = [samples[i] for i in sorted(indices)]
        metric_values: dict[str, tuple[Any, str]] = {f"sampled_{unit_name}_count": (len(selected), "count")}
        metric_values["sample_document_frequency"] = (len(selected) / len(samples) if samples else None, "ratio")
        availability = {}
        for metric in COUNT_FIELDS:
            if kind != "videos" and not (kind == "comments" and metric == "likes"):
                continue
            vals = [item["metrics"][metric] for item in selected if item["metrics"][metric] is not None]
            availability[metric] = len(vals)
            for stat, fn in (("sum", sum), ("mean", mean), ("median", median)):
                metric_values[f"sample_{unit_name}_{metric}_{stat}"] = (fn(vals) if vals else None, "count")
        durations = [item["duration"] for item in selected if item["duration"] is not None]
        if kind == "videos":
            availability["duration_seconds"] = len(durations)
            metric_values["sample_duration_seconds_mean"] = (mean(durations) if durations else None, "seconds")
            metric_values["sample_duration_seconds_median"] = (median(durations) if durations else None, "seconds")
        dated = [timestamp(item["created_at"]) for item in selected if item["created_at"]]
        future = sum(date > now for date in dated)
        for days in (7, 30):
            recent = sum(0 <= (now - date).total_seconds() < days * 86400 for date in dated)
            metric_values[f"sample_{unit_name}s_last_{days}_days"] = (recent if dated else None, "count")
        paired = [s for s in selected if all(s["metrics"][k] is not None for k in ("likes", "comments", "shares", "views"))]
        if kind == "videos":
            denominator = sum(s["metrics"]["views"] for s in paired)
            numerator = sum(sum(s["metrics"][k] for k in ("likes", "comments", "shares")) for s in paired)
            metric_values["sample_interactions_per_view"] = (numerator / denominator if denominator else None, "ratio")
        peers = []
        if term.startswith("#") and selected:
            for tag in tags:
                shared = len(indices & memberships[tag])
                if tag == term or not shared:
                    continue
                union = len(indices | memberships[tag])
                peers.append({"hashtag": tag, "shared_records": shared,
                              "conditional_ratio": shared / len(indices), "jaccard": shared / union,
                              "lift": shared * len(samples) / (len(indices) * len(memberships[tag]))})
        sounds = Counter(s["music_id"] for s in selected if s["music_id"])
        authors = {s["author"] for s in selected if s["author"]}
        is_tag = term.startswith("#")
        status = ("observed-in-video-sample" if kind == "videos" else "observed-in-" + kind) if selected else "not-observed-in-this-sample"
        result.append(KeywordCandidate(
            phrase=term, relationship="tiktok-hashtag" if is_tag else "tiktok-text-keyword", score=None,
            evidence=tuple(KeywordEvidence(source, metric, value, unit, observed, None,
                "Sample-only association. Whole-video metrics are not hashtag-attributed lift or market totals.")
                for metric, (value, unit) in metric_values.items()),
            metadata={"platform": "TikTok", "kind": "hashtag" if is_tag else "keyword",
                      "verification": status, "text_fields": sorted(fields[term]),
                      "metric_available_records": availability, "dated_records": len(dated),
                      "future_timestamps": future, "complete_engagement_records": len(paired) if kind == "videos" else 0,
                      "author_available_records": sum(bool(s["author"]) for s in selected),
                      "distinct_sample_authors": len(authors), "music_available_records": sum(sounds.values()),
                      "sounds": [{"music_id": k, "sampled_records": v,
                                  **({"names": sorted({s["music_name"] for s in selected
                                                      if s["music_id"] == k and s["music_name"]})}
                                     if any(s["music_id"] == k and s["music_name"] for s in selected) else {})}
                                 for k, v in sounds.most_common(20)],
                      "formats": dict(Counter(s["format"] for s in selected)) if kind == "videos" else {},
                      "cooccurring_hashtags": sorted(peers, key=lambda p: (-p["shared_records"], p["hashtag"]))[:20],
                      "occurrences": spans[term]},
        ))
    return result, {"records_received": len(rows), "unique_records": len(samples), "duplicate_records": duplicates,
                    "conflicting_duplicate_fields": conflicts, "missing_record_ids": sum(not(s["id"] or s["url"]) for s in samples),
                    "sample_kind": kind, "candidate_count": len(result), "population_total_known": False,
                    "region_code_meaning": "creator registration country, not viewer country"}


def observations(rows: list[dict[str, Any]], source: str, observed: str, scope: str,
                 *, hashtags_only: bool = False) -> list[KeywordCandidate]:
    """Canonical observations and known hashtag-analytics provider records.

    Each result keeps its own geography/window/scope; unlike metrics are not collapsed.
    """
    output = []
    for row in records(rows):
        if row.get("error") or row.get("errorCode"):
            raise InputError("Hashtag collection returned an error, not zero popularity.")
        if row.get("platform") is not None and str(row["platform"]).casefold() != "tiktok":
            raise InputError("TikTok observations cannot relabel another platform as TikTok.")
        is_tag = hashtags_only or row.get("kind") == "hashtag" or any(k in row for k in ("hashtagName", "hashtag"))
        word = first(row, "hashtagName", "hashtag", "phrase", "name")
        word = hashtag(word) if is_tag else _string(word, 400).strip()
        if not word:
            raise InputError("Observation needs a phrase or hashtag name.")
        country = first(row, "countryCode", "country", "geography")
        country = _string(country, 100) or None
        period = identifier(first(row, "period", "window"))
        date = timestamp(row.get("observed_at", observed)).isoformat()
        src = _string(row.get("source", source), 300)
        row_scope = _string(row.get("scope", scope), 1000)
        if not src.strip() or not row_scope.strip():
            raise InputError("Source and scope cannot be empty; retain observation provenance.")
        availability = row.get("availability", "observed")
        if availability not in {"observed", "unavailable"}:
            raise InputError("availability must be observed or unavailable.")
        evidence = []
        declared_approximate = row.get("approximate_metrics", [])
        if not isinstance(declared_approximate, list) or any(not isinstance(m, str) for m in declared_approximate):
            raise InputError("approximate_metrics must be an array of metric names.")
        approximate = []
        if "metric" in row:
            metric, unit = _string(row["metric"], 120), _string(row.get("unit"), 100)
            if not metric or not unit:
                raise InputError("An observation requires the actual metric and its unit.")
            value = row.get("value")
            if value not in (None, ""):
                if unit in {"count", "views", "posts", "searches_per_month", "ordinal"}:
                    value, rounded = displayed_count(value)
                    if rounded:
                        approximate.append(metric)
                elif unit in {"ratio", "percent", "seconds", "index", "index_0_100", "index_0_1"}:
                    value = number(value)
                elif isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise InputError("Observation value must be a scalar or null.")
                elif isinstance(value, float) and not math.isfinite(value):
                    raise InputError("Observation value must be finite.")
            else:
                value = None
            if availability == "unavailable" and value is not None:
                raise InputError("Unavailable observations cannot carry a measured numeric value (including zero).")
            evidence.append(KeywordEvidence(src, metric, value, unit, date, country,
                            _string(row.get("notes"), 2000) or "Provider-reported observation; scope and units must be retained."))
        else:
            for metric, aliases in REPORTED_FIELDS.items():
                raw = first(row, *aliases)
                if any(k in row for k in aliases):
                    value, rounded = displayed_count(raw)
                    if rounded:
                        approximate.append(metric)
                    evidence.append(KeywordEvidence(src, metric, value, "count", date, country,
                                    "Provider-reported hashtag aggregate; not search volume or a unique-person count."))
            if not evidence and availability != "unavailable":
                raise InputError("No recognised hashtag count; supply an explicit metric and unit instead.")
        if availability == "unavailable" and any(e.value is not None for e in evidence):
            raise InputError("Unavailable observations cannot carry measured counts, including zero.")
        if availability == "unavailable":
            evidence = [KeywordEvidence(src, "availability", None, "state", date, country, "Unavailable is not zero demand or a banned-hashtag verdict.")]
        output.append(KeywordCandidate(word, "tiktok-hashtag-observation" if is_tag else "tiktok-keyword-observation",
            evidence=tuple(evidence), metadata={
                "platform": "TikTok", "kind": "hashtag" if is_tag else "keyword", "scope": row_scope,
                "geography": country, "window": period, "availability": availability,
                "verification": "provider-reported" if availability == "observed" else "unavailable",
                "hashtag_id": identifier(first(row, "hashtagId", "hashtag_id")),
                "approximate_metrics": sorted(set(approximate) | set(declared_approximate)),
                "native_dimensions": {k: row[k] for k in NATIVE_DIMENSIONS if k in row},
                "native_dimension_note": "Provider-native categories/scores; do not assume percentages or viewer representativeness.",
            }))
        # The documented analytics output exposes names/IDs in relatedHashtags and
        # recList. Those are discovery evidence, not counts inherited from the seed.
        if is_tag and availability == "observed":
            related = {}
            for field in ("relatedHashtags", "recList"):
                for position, child in enumerate(records(row.get(field, []), 500), 1):
                    name = hashtag(child.get("hashtagName"))
                    if name == word:
                        continue
                    info = related.setdefault(name, {"id": identifier(child.get("hashtagId")), "positions": {}})
                    info["positions"][field] = position
            for name, info in related.items():
                output.append(KeywordCandidate(name, "tiktok-related-hashtag", evidence=tuple(
                    KeywordEvidence(src, "provider_" + field + "_position", position, "ordinal", date, country,
                                    "Position in this seed's related list, not hashtag popularity or volume.")
                    for field, position in info["positions"].items()), metadata={
                        "platform": "TikTok", "kind": "hashtag", "verification": "provider-related-hashtag",
                        "parent_hashtag": word, "hashtag_id": info["id"], "scope": row_scope,
                        "window": period, "geography": country, "availability": "observed",
                    }))
        if len(output) > 10000:
            raise InputError("More than 10,000 observation candidates; narrow the input.")
    return output


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[KeywordCandidate]:
    """Net changes only between exact, compatible aggregate observations."""
    def index(payload):
        if not isinstance(payload, dict):
            raise InputError("Snapshots must be result objects.")
        meta = payload.get("metadata", {})
        if not isinstance(meta, dict) or payload.get("plugin") != "tiktok" or meta.get("schema") != SCHEMA:
            raise InputError("Comparison requires two saved TikTok plugin results with the current result schema.")
        result = {}
        items = payload.get("keywords", [])
        for item in records(list(items) if isinstance(items, tuple) else items):
            md = item.get("metadata", {})
            if not isinstance(md, dict) or md.get("platform") != "TikTok":
                raise InputError("Cross-platform comparison is not supported.")
            approximations = md.get("approximate_metrics", [])
            if not isinstance(approximations, list) or any(not isinstance(m, str) for m in approximations):
                raise InputError("Malformed snapshot approximation flags.")
            evidence = item.get("evidence", [])
            for e in records(list(evidence) if isinstance(evidence, tuple) else evidence):
                if e.get("metric") not in {"reported_post_count", "reported_view_count", "estimated_search_volume"}:
                    continue
                if not all(isinstance(v, str) and v.strip() for v in
                           (item.get("phrase"), e.get("source"), e.get("unit"), e.get("observed_at"))):
                    raise InputError("Snapshot measurements need phrase, source, unit and observation date.")
                if any(v is not None and not isinstance(v, str) for v in
                       (e.get("geography"), md.get("scope"), md.get("window"))):
                    raise InputError("Malformed snapshot scope, geography or window.")
                key = (item["phrase"], e["source"], e["metric"], e["unit"], e.get("geography"),
                       md.get("scope"), md.get("window"))
                if not md.get("scope"):
                    continue
                if key in result:
                    raise InputError("Ambiguous duplicate snapshot measurements; filter source/scope first.")
                result[key] = (e, md)
        return result
    left, right = index(before), index(after)
    output = []
    for key, (end, md) in right.items():
        if key not in left:
            continue
        start, prior_md = left[key]
        metric = key[2]
        if metric in md.get("approximate_metrics", []) or metric in prior_md.get("approximate_metrics", []):
            continue
        if start.get("value") is None or end.get("value") is None:
            continue
        a, b = count(start["value"]), count(end["value"])
        days = (timestamp(end["observed_at"]) - timestamp(start["observed_at"])).total_seconds() / 86400
        if days <= 0:
            raise InputError("The second snapshot must be later than the first.")
        delta = b - a
        output.append(KeywordCandidate(key[0], "tiktok-snapshot-change", evidence=tuple(
            KeywordEvidence(key[1], metric + suffix, value, unit, end["observed_at"], key[4],
                            "Net reported change, not gross new posts, organic uplift, or a forecast.")
            for suffix, value, unit in (("_delta", delta, key[3]), ("_delta_per_day", delta / days, key[3] + "_per_day"),
                                        ("_change_percent", delta / a * 100 if a else None, "percent"))),
            metadata={"platform": "TikTok", "scope": key[5], "window": key[6], "interval_days": days,
                      "baseline": a, "latest": b, "decrease_observed": delta < 0}))
    if not output:
        raise InputError("No compatible exact dated aggregate measurements. Check scope, source, geography, window and rounding.")
    return output


def finish(operation: str, items: list[KeywordCandidate], metadata: dict[str, Any],
           options: Mapping[str, Any], observed: str) -> PluginResult:
    limit = integer(options, "limit", 50, 1, 1000)
    sort_by = options.get("sort_by", "discovery")
    allowed = {"discovery", "reported_post_count", "reported_view_count", "period_post_count", "period_view_count",
               "estimated_search_volume", "sampled_video_count", "sample_video_views_median", "sample_interactions_per_view"}
    if not isinstance(sort_by, str) or sort_by not in allowed:
        raise ConfigurationError("Unknown sort_by metric; see docs/tiktok.md.")
    if sort_by != "discovery":
        def ranking(item):
            values = [e.value for e in item.evidence if e.metric == sort_by and isinstance(e.value, (int, float))]
            return (not bool(values), -values[0] if values else 0, item.phrase)
        items = sorted(items, key=ranking)
    return PluginResult("tiktok", operation, tuple(items[:limit]), (
        "Search phrases, hashtag totals, ad metrics and sample engagement are different measurements.",
        "Missing/blocked/empty data is not zero popularity or proof of a banned hashtag.",
        "Whole-video engagement associated with tags does not establish causal hashtag performance.",
    ), {**metadata, "schema": SCHEMA, "platform": "TikTok", "observed_at": observed,
        "candidates_before_limit": len(items), "output_truncated": len(items) > limit, "sort_by": sort_by})
