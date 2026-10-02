"""Instagram-specific, local analysis. No third-party imports or network access.

Hashtag supply, estimated searches, sampled engagement and owned-media insights
are different measurements. None is converted into a universal popularity score.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice
from statistics import mean, median
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from ..errors import InputError
from ..models import KeywordCandidate, KeywordEvidence
from .common import OnlineSourceError, number

COUNT_FIELDS = {
    "likes": ("likes", "like_count", "likesCount"),
    "comments": ("comments_count", "commentsCount", "comments"),
    "views": ("views",),
    "video_views": ("videoViewCount", "video_views"),
    "plays": ("videoPlayCount", "plays"),
    "shares": ("shares", "sharesCount"),
    "replies": ("replies",),
    "saved": ("saved", "savesCount"),
    "reach": ("reach",),
    "total_interactions": ("total_interactions",),
    "followers": ("followers_count", "followersCount", "followers"),
}
SAMPLE_NOTE = (
    "Describes only the returned sample, not all Instagram posts, hashtag search demand, "
    "hashtag-attributed reach, or a causal effect of using this tag. Missing counts are not zero."
)
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
STOP = frozenset("a an and are as at be been but by for from had has have he her here him his i "
                 "in is it its me my not of on or our she so that the their them then there these "
                 "they this to us was we were what when where which who will with you your".split())


def _tag_char(char: str) -> bool:
    return char == "_" or unicodedata.category(char)[0] in "LMN"


def hashtag(value: Any) -> str:
    """Normalise an explicitly supplied tag; never silently join separate words."""
    if not isinstance(value, str):
        raise InputError("A hashtag must be text.")
    value = unicodedata.normalize("NFC", value.strip()).removeprefix("#")
    if not value or len(value) > 100 or not all(_tag_char(c) for c in value):
        raise InputError("Hashtags must contain 1–100 letters, marks, numbers or underscores, without spaces.")
    return "#" + unicodedata.normalize("NFC", value.lower())


def hashtags(text: str) -> list[tuple[str, int, int]]:
    result = []
    for match in re.finditer("#", text):
        start = match.start()
        # Exclude URL fragments, embedded identifiers and Markdown headings.
        if start and (text[start - 1] in "/#&=" or _tag_char(text[start - 1])):
            continue
        end = start + 1
        while end < len(text) and _tag_char(text[end]):
            end += 1
        if end > start + 1 and end - start <= 101:
            result.append((hashtag(text[start:end]), start, end))
    return result


def timestamp(value: Any) -> datetime:
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)):
            result = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                result = result.replace(tzinfo=timezone.utc)
        else:
            raise ValueError
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        raise InputError("Timestamps must be ISO dates, timezone-qualified ISO datetimes, or Unix seconds.") from None


def count(value: Any, *, sentinel: bool = False) -> int | float | None:
    result = number(value)
    if sentinel and result == -1:
        return None  # Some public-data providers use -1 for unavailable likes.
    if result is not None and result < 0:
        raise OnlineSourceError("Negative Instagram counts are invalid; use null for unavailable values.")
    return result


def displayed_count(value: Any) -> tuple[int | float | None, bool]:
    """Parse English compact display counts without pretending rounded values are exact."""
    if value is None or value == "":
        return None, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return count(value), False
    if not isinstance(value, str) or len(value) > 200:
        raise InputError("Post-count displays must be numbers or at most 200 characters of text.")
    match = re.fullmatch(r"\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([kmbg]?)\s*(\+?)\s*(?:posts?)?\s*", value, re.I)
    if not match:
        return None, True
    scale = {"": 1, "k": 1000, "m": 1_000_000, "b": 1_000_000_000, "g": 1_000_000_000}
    result = float(match[1].replace(",", "")) * scale[match[2].lower()]
    return count(result), bool(match[2] or match[3])


def permalink(value: Any) -> str | None:
    """Only preserve credential-free public Instagram media URLs; never fetch them."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname in {"instagram.com", "www.instagram.com"}
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and re.fullmatch(r"/(?:p|reel|tv)/[A-Za-z0-9_-]+/?", parsed.path)):
            return "https://www.instagram.com" + parsed.path.rstrip("/") + "/"
    except ValueError:
        pass
    return None


@dataclass
class Media:
    key: str
    caption: str
    tags: set[str]
    metrics: dict[str, int | float | None]
    published: datetime | None = None
    media_type: str = "unknown"
    url: str | None = None
    contexts: set[str] = field(default_factory=set)
    spans: list[dict[str, Any]] = field(default_factory=list)


def media_row(row: Mapping[str, Any], index: int, *, provider: str = "import") -> Media:
    if row.get("error") or row.get("errors"):
        raise OnlineSourceError("An Instagram record reports an error; it is not an empty or zero-demand result.")
    caption = row.get("caption", row.get("title", row.get("text", "")))
    if caption is None:
        caption = ""
    if not isinstance(caption, str) or len(caption) > 1_000_000:
        raise InputError("Instagram captions must be bounded text.")
    spans = hashtags(caption)
    tags = {tag for tag, _, _ in spans}
    supplied = row.get("hashtags", [])
    if supplied is not None:
        if not isinstance(supplied, list) or len(supplied) > 500:
            raise InputError("The hashtags field must be an array of at most 500 strings.")
        tags.update(hashtag(t) for t in supplied)
    supplied_context = row.get("_matched_hashtag")
    if supplied_context:
        tags.add(hashtag(supplied_context))
    if len(tags) > 100 or len(spans) > 5000:
        raise InputError("A media/text record exceeds the 100 distinct hashtags or 5000 span safety limit.")
    metrics = {}
    for target, aliases in COUNT_FIELDS.items():
        # An exported comments array is not a numeric comment count.
        value = next((row[k] for k in aliases if k in row and not
                      (k == "comments" and isinstance(row[k], list))), None)
        metrics[target] = count(value, sentinel=provider.startswith("apify"))
    raw_time = row.get("timestamp", row.get("creation_timestamp"))
    if raw_time is None and isinstance(row.get("media"), list) and row["media"]:
        first = row["media"][0]
        if not isinstance(first, dict):
            raise InputError("Export media entries must be objects.")
        raw_time = first.get("creation_timestamp")
    url = permalink(row.get("permalink", row.get("url")))
    identity = row.get("id", row.get("shortCode", row.get("shortcode")))
    if identity is not None and (isinstance(identity, bool) or not isinstance(identity, (str, int))):
        raise InputError("Instagram media IDs must be text or integers.")
    # Prefer the stable media ID when one endpoint omits a permalink.
    key = ("id:" + str(identity)) if identity is not None else (url or f"row:{index}")
    media_type = str(row.get("media_type", row.get("type", "unknown")))[:80]
    if row.get("media_product_type"):
        media_type += ":" + str(row["media_product_type"])[:80]
    return Media(key, caption, tags, metrics, timestamp(raw_time) if raw_time not in (None, "") else None,
                 media_type, url, {str(row.get("_context", provider))},
                 [{"hashtag": tag, "start": start, "end": end} for tag, start, end in spans])


def deduplicate(rows: Iterable[Media]) -> tuple[list[Media], int, int]:
    grouped: dict[str, Media] = {}
    duplicates = conflicts = 0
    for media in rows:
        if media.key not in grouped:
            grouped[media.key] = media
            continue
        duplicates += 1
        old = grouped[media.key]
        old.tags.update(media.tags)
        old.contexts.update(media.contexts)
        for key, value in media.metrics.items():
            if old.metrics[key] is None:
                old.metrics[key] = value
            elif value is not None and value != old.metrics[key]:
                conflicts += 1  # Retain first observation, never add repeated counts.
        if not old.caption and media.caption:
            old.caption, old.spans = media.caption, media.spans
        old.published = old.published or media.published
        old.url = old.url or media.url
    return list(grouped.values()), duplicates, conflicts


def evidence(source: str, metric: str, value: Any, unit: str, observed: str,
             note: str = SAMPLE_NOTE) -> KeywordEvidence:
    return KeywordEvidence(source, metric, value, unit, observed_at=observed, notes=note)


def analyse(rows: list[Media], *, source: str, observed: str, known: Mapping[str, str] | None = None,
            include_keywords: bool = False, related_limit: int = 20,
            sample_kind: str = "media") -> tuple[list[KeywordCandidate], dict[str, Any]]:
    if sum(len(row.tags) for row in rows) > 50_000:
        raise InputError("Sample exceeds 50000 hashtag assignments; split the collection.")
    rows, duplicates, conflicts = deduplicate(rows)
    now = timestamp(observed)
    by_tag: dict[str, list[Media]] = defaultdict(list)
    for tag in known or {}:
        by_tag[tag] = []
    for media in rows:
        for tag in media.tags:
            by_tag[tag].append(media)
    items = []
    for tag, matches in sorted(by_tag.items(), key=lambda p: (-len(p[1]), p[0])):
        n = len(matches)
        ev = [evidence(source, "sampled_post_count" if sample_kind == "media" else "sampled_text_count", n,
                       "posts" if sample_kind == "media" else "documents", observed)]
        if sample_kind == "media":
            for metric in COUNT_FIELDS:
                values = [m.metrics[metric] for m in matches if m.metrics[metric] is not None]
                ev.append(evidence(source, f"{metric}_observed_posts", len(values), "posts", observed))
                for stat, value in (("mean", mean(values) if values else None),
                                    ("median", median(values) if values else None)):
                    ev.append(evidence(source, f"sample_{metric}_{stat}", value, "count_per_post", observed))
            engagements = [m.metrics["likes"] + m.metrics["comments"] for m in matches
                           if m.metrics["likes"] is not None and m.metrics["comments"] is not None]
            ev.extend([evidence(source, "engagement_observed_posts", len(engagements), "posts", observed),
                       evidence(source, "sample_likes_plus_comments_median",
                                median(engagements) if engagements else None, "interactions_per_post", observed)])
            rates = [(m.metrics["likes"] + m.metrics["comments"]) / m.metrics["followers"] * 100
                     for m in matches if m.metrics["likes"] is not None
                     and m.metrics["comments"] is not None and m.metrics["followers"]]
            ev.extend([evidence(source, "follower_rate_observed_posts", len(rates), "posts", observed),
                       evidence(source, "sample_follower_engagement_percent_mean", mean(rates) if rates else None,
                                "percent", observed, "Mean of (likes+comments)/available follower count*100. "
                                "Follower counts may be current, not at publication. " + SAMPLE_NOTE)])
            ages = [(now - m.published).total_seconds() / 3600 for m in matches if m.published is not None]
            valid_ages = [age for age in ages if age >= 0]
            ev.extend([evidence(source, "timestamp_observed_posts", len(valid_ages), "posts", observed),
                       evidence(source, "sample_posts_last_24h", sum(a <= 24 for a in valid_ages)
                                if valid_ages else None, "posts", observed),
                       evidence(source, "sample_posts_last_7d", sum(a <= 168 for a in valid_ages)
                                if valid_ages else None, "posts", observed)])
        co = Counter(other for media in matches for other in media.tags if other != tag)
        related = [{"hashtag": other, ("shared_posts" if sample_kind == "media" else "shared_documents"): joint,
                    "conditional_sample_fraction": joint / n,
                    "sample_jaccard": joint / (n + len(by_tag[other]) - joint)}
                   for other, joint in sorted(co.items(), key=lambda p: (-p[1], p[0]))[:related_limit]]
        contexts = Counter(context for m in matches for context in m.contexts)
        meta = {"platform": "Instagram", "verification": (known or {}).get(tag, "observed-in-sample"
                 if sample_kind != "reference" else "reference-only"), "cooccurring_hashtags": related,
                "related_truncated": len(co) > related_limit, "sample_contexts": dict(contexts),
                "media_types": dict(Counter(m.media_type for m in matches)),
                "occurrence_examples": list(islice(({"media_key": m.key, **span} for m in matches
                                        for span in m.spans if span["hashtag"] == tag), 10))}
        items.append(KeywordCandidate(tag, "instagram-hashtag", evidence=tuple(ev), metadata=meta))
    if include_keywords:
        items.extend(caption_keywords(rows, source, observed))
    return items, {"sample_size": len(rows), "duplicates_removed": duplicates,
                   "conflicting_counts_retained_first": conflicts,
                   "undated_media": sum(m.published is None for m in rows),
                   "future_dated_media": sum(m.published is not None and m.published > now for m in rows),
                   "sample_kind": sample_kind}


def caption_keywords(rows: list[Media], source: str, observed: str) -> list[KeywordCandidate]:
    """Literal words/bigrams only: do not turn them into claimed existing hashtags."""
    counts: Counter[str] = Counter()
    for row in rows:
        words = list(WORD.finditer(row.caption))
        found = set()
        for i, word in enumerate(words):
            value = word.group().casefold()
            if len(value) < 3 or value in STOP or (word.start() and row.caption[word.start() - 1] in "#@"):
                continue
            found.add(value)
            if i:
                left = words[i - 1]
                if (left.group().casefold() not in STOP and len(left.group()) >= 3
                        and re.fullmatch(r"[ \t]+", row.caption[left.end():word.start()])
                        and (not left.start() or row.caption[left.start() - 1] not in "#@")):
                    found.add(left.group().casefold() + " " + value)
        counts.update(found)
        if len(counts) > 20_000:
            raise InputError("Caption vocabulary exceeds 20000 phrases; split the input.")
    return [KeywordCandidate(phrase, "instagram-caption-keyword",
                             evidence=(evidence(source, "sample_document_frequency", n, "documents", observed),),
                             metadata={"platform": "Instagram", "verification": "text-only",
                                       "method": "literal unigram/bigram; small English stoplist"})
            for phrase, n in sorted(counts.items(), key=lambda p: (-p[1], p[0]))]


def stats_candidates(rows: list[dict[str, Any]], source: str, observed: str) -> list[KeywordCandidate]:
    items = []
    for row in rows:
        if len(items) > 20_000:
            raise InputError("Hashtag statistics exceed 20000 candidates; split the input.")
        if row.get("error") or row.get("errors"):
            raise OnlineSourceError("Hashtag statistics contain a provider error; no zero is inferred.")
        name = hashtag(row.get("name", row.get("hashtag", row.get("hash"))))
        availability = row.get("availability", "observed")
        if availability not in {"observed", "unavailable", "restricted", "unknown"}:
            raise InputError("Unknown hashtag availability state.")
        raw = row.get("post_count", row.get("postsCount", row.get("media_count")))
        display = row.get("post_count_display", row.get("posts", row.get("info")))
        value, approximate = displayed_count(raw if raw is not None else display)
        explicit_approximate = row.get("count_is_approximate", False)
        if not isinstance(explicit_approximate, bool):
            raise InputError("count_is_approximate must be boolean.")
        approximate = approximate or explicit_approximate
        if availability != "observed" and value is not None:
            raise InputError("Unavailable hashtag observations must not carry a numeric post count.")
        ev = [evidence(source, "reported_post_count", value, "posts", observed,
                       "Provider-reported post supply, possibly rounded or stale. Not unique people, "
                       "search volume, reach, or a guaranteed complete count.")]
        if isinstance(display, str):
            ev.append(evidence(source, "reported_post_count_display", display, "display_text", observed))
        if row.get("postsPerDay") not in (None, ""):
            ev.append(evidence(source, "provider_posts_per_day", count(row["postsPerDay"]), "posts_per_day",
                               observed, "Third-party activity estimate; collection method/window may be unknown."))
        related = []
        for group in ("related", "frequent", "average", "rare", "relatedFrequent", "relatedAverage", "relatedRare"):
            values = row.get(group, [])
            if not isinstance(values, list) or len(values) > 1000:
                raise InputError("Related hashtags must be a bounded array.")
            for item in values:
                if isinstance(item, str):
                    item = {"hash": item}
                if not isinstance(item, dict):
                    raise InputError("Related hashtag records must be objects or strings.")
                tag = hashtag(item.get("hash", item.get("name")))
                qty, rounded = displayed_count(item.get("postsCount", item.get("info")))
                related.append({"hashtag": tag, "provider_group": group, "reported_post_count": qty,
                                "count_is_approximate": rounded})
        items.append(KeywordCandidate(name, "instagram-hashtag", evidence=tuple(ev), metadata={
            "platform": "Instagram", "verification": "provider-reported", "availability": availability,
            "count_is_approximate": approximate, "related_hashtags": related,
        }))
        for item in related:
            items.append(KeywordCandidate(item["hashtag"], "instagram-related-hashtag", evidence=(
                evidence(source, "reported_post_count", item["reported_post_count"], "posts", observed,
                         "Related-tag provider observation; not search volume."),),
                metadata={"platform": "Instagram", "verification": "provider-reported", "seed": name,
                          "provider_group": item["provider_group"],
                          "count_is_approximate": item["count_is_approximate"]}))
    return items


def compare(before: dict[str, Any], after: dict[str, Any], metric: str) -> list[KeywordCandidate]:
    for payload in (before, after):
        if payload.get("plugin") != "instagram" or not isinstance(payload.get("metadata"), dict):
            raise InputError("Compare expects two saved Instagram plugin JSON results.")
    left, right = before["metadata"], after["metadata"]
    if not left.get("comparison_scope") or left.get("comparison_scope") != right.get("comparison_scope"):
        raise InputError("Snapshots must have identical comparison_scope (provider, collection and geography).")
    elapsed = (timestamp(right.get("observed_at")) - timestamp(left.get("observed_at"))).total_seconds() / 86400
    if elapsed <= 0:
        raise InputError("Snapshots must be supplied oldest first with different observation times.")

    def measurements(payload: dict[str, Any]) -> dict[tuple[Any, ...], Any]:
        found = {}
        rows = payload.get("keywords")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise InputError("Snapshot keywords must be an array of objects.")
        for row in rows:
            meta = row.get("metadata", {})
            if not isinstance(meta, dict) or not isinstance(row.get("evidence"), list):
                raise InputError("Snapshot metadata/evidence has an invalid shape.")
            if meta.get("count_is_approximate"):
                continue
            for item in row["evidence"]:
                if not isinstance(item, dict) or not isinstance(item.get("source"), str):
                    raise InputError("Snapshot evidence must contain source-labelled objects.")
                if item.get("metric") != metric or item.get("value") is None:
                    continue
                key = (hashtag(row.get("phrase")), item["source"], item["metric"], str(item.get("unit")), str(item.get("geography")))
                value = count(item["value"])
                if key in found and found[key] != value:
                    raise InputError("Snapshot contains conflicting observations for the same measurement.")
                found[key] = value
        return found
    old, new = measurements(before), measurements(after)
    items = []
    for key in sorted(old.keys() & new.keys(), key=str):
        a, b = old[key], new[key]
        delta = b - a
        note = "Net change between comparable observations, not gross new posts or proof of a future trend."
        items.append(KeywordCandidate(key[0], "instagram-snapshot-change", evidence=(
            evidence(key[1], metric + "_delta", delta, key[3], right["observed_at"], note),
            evidence(key[1], metric + "_delta_per_day", delta / elapsed, str(key[3]) + "_per_day",
                     right["observed_at"], note),
            evidence(key[1], metric + "_percent_change", (b - a) / a * 100 if a else None,
                     "percent", right["observed_at"], note),
        ), metadata={"platform": "Instagram", "previous": a, "current": b, "elapsed_days": elapsed}))
    return items
