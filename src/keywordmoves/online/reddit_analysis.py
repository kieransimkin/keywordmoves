"""Bounded, provenance-aware Reddit text/sample analysis; no network or NLP imports."""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginResult
from .common import boolean, choice, integer

VERSION = "keywordmoves-reddit/v1"
WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
HASH = re.compile(r"(?<![\w/#])#([^\W_][\w]*)(?!\w)", re.UNICODE)
COMMUNITY = re.compile(r"(?<!\w)/?r/([A-Za-z0-9_]{2,21})(?!\w)")
STOP = frozenset("a an and are as at be been but by can could did do does for from had has have he her here him his how i if in into is it its just me my no not of on or our out she so some than that the their them there these they this those to too up us was we were what when where which who why will with would you your".split())
ANALYSIS_OPTIONS = {
    "limit", "min_words", "max_words", "min_occurrences", "max_candidates", "max_records",
    "max_text_chars", "include_keywords", "include_hashtags", "include_flair", "include_records",
    "include_nsfw", "exclude_stickied", "stopwords", "sort_by", "cooccurrence_limit",
}
SORTS = ("document_frequency", "sample_post_score_median", "sample_post_comments_median",
         "sample_comment_score_median", "sample_posts_7d", "phrase")
NOTES = (
    "Counts describe a bounded sample, not Reddit-wide keyword search volume or ranking difficulty.",
    "Reddit scores are reported net scores (possibly hidden or adjusted), not exact upvotes, views or reach.",
    "Whole-post engagement is not attributed causally to its keywords; post and comment scores stay separate.",
    "Literal #words are text observations, not confirmation of a native Reddit hashtag index.",
    "Delete expired or removed source content and regenerate derived outputs; no external model is called.",
)


def timestamp(value: Any, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise InputError("An observed_at timestamp with timezone (or YYYY-MM-DD) is required.")
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                raise ValueError
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            raw = str(value)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                raw += "T00:00:00+00:00"
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        raise InputError("Invalid UTC/date timestamp; ambiguous local times are not accepted.") from None


def metric(value: Any, *, signed: bool = False, ratio: bool = False) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputError("Expected a finite numeric metric, not a boolean/object.")
    if isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"-?\d+", value)):
        number = int(value)
        if (not signed and number < 0) or (ratio and number > 1):
            raise InputError("Metric is outside its permitted range.")
        return number
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise InputError("Metric must be numeric; do not expand rounded displays like 2.5k.") from None
    if not math.isfinite(number) or (not signed and number < 0) or (ratio and number > 1):
        raise InputError("Metric is non-finite or outside its permitted range.")
    return int(number) if number.is_integer() else number


def clean(value: Any, field: str = "text") -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InputError(f"{field} must be text.")
    return value


def fullname(value: Any, kind: str | None = None) -> str:
    value = str(value or "")
    if kind and not value.startswith(("t1_", "t3_", "t5_")):
        value = kind + "_" + value
    if not re.fullmatch(r"t[135]_[a-z0-9]{1,20}", value):
        raise ConfigurationError("Use a Reddit fullname such as t3_abc123 or t1_def456.")
    if kind and not value.startswith(kind + "_"):
        raise ConfigurationError("The Reddit fullname has the wrong content kind.")
    return value


def subreddit(value: Any) -> str:
    result = str(value or "").strip().removeprefix("/r/").removeprefix("r/").rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", result) or result.lower().startswith("u_"):
        raise ConfigurationError("Supply one subreddit name, not a URL, user profile or multi.")
    return result.lower()


def safe_url(value: Any) -> str | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise InputError("URL must be text.")
    if value.startswith("/r/"):
        value = "https://www.reddit.com" + value
    try:
        u = urlsplit(value)
        if u.scheme not in {"https", "http"} or not u.hostname or u.username or u.password:
            return None
        # No credentials/tracking parameters are retained in analysis output.
        return u._replace(query="", fragment="").geturl()
    except ValueError:
        return None


def normalise(row: Mapping[str, Any], kind: str | None = None) -> dict[str, Any] | None:
    """Accept native Things or canonical records. Never retain author identifiers."""
    if not isinstance(row, dict):
        raise InputError("Reddit records must be JSON objects.")
    if "data" in row and "kind" in row:
        kind, row = row["kind"], row["data"]
        if not isinstance(row, dict):
            raise InputError("A Reddit Thing must have object data.")
    kind = kind or row.get("kind")
    if kind not in {"t1", "t3"}:
        raise InputError("Post/comment records need kind t3/t1; communities are imported separately.")
    identity = fullname(row.get("name") or row.get("id"), kind)
    title = clean(row.get("title"), "title") if kind == "t3" else ""
    body = clean(row.get("body") if kind == "t1" else row.get("selftext", row.get("body")), "body")
    if (body.strip() in {"[deleted]", "[removed]"} or row.get("removed_by_category")
            or row.get("deleted") is True or row.get("removed") is True):
        return None
    for flag in ("score_hidden", "hide_score", "over_18", "stickied", "locked", "archived"):
        if flag in row and row[flag] is not None and not isinstance(row[flag], bool):
            raise InputError(f"{flag} must be a boolean when present.")
    parent = row.get("parent_id")
    link = row.get("link_id")
    crosspost = row.get("crosspost_parent")
    return {
        "id": identity, "kind": kind, "title": title, "body": body,
        "subreddit": subreddit(row["subreddit"]) if row.get("subreddit") else None,
        "created_at": timestamp(row.get("created_utc", row.get("created_at"))),
        "score": None if row.get("score_hidden") or row.get("hide_score")
        else metric(row.get("score"), signed=True),
        "score_hidden": bool(row.get("score_hidden") or row.get("hide_score")),
        "provider_reported_votes": metric(row.get("provider_reported_votes"), signed=True),
        "num_comments": metric(row.get("num_comments")) if kind == "t3" else None,
        "upvote_ratio": metric(row.get("upvote_ratio"), ratio=True) if kind == "t3" else None,
        "flair": clean(row.get("link_flair_text", row.get("flair")), "flair") if kind == "t3" else "",
        "permalink": safe_url(row.get("permalink")),
        "url": safe_url(row.get("url")) if kind == "t3" else None,
        "parent_id": fullname(parent) if parent else None,
        "link_id": fullname(link, "t3") if link else None,
        "crosspost_parent": fullname(crosspost, "t3") if crosspost else None,
        "over_18": bool(row.get("over_18")), "stickied": bool(row.get("stickied")),
        "locked": bool(row.get("locked")), "archived": bool(row.get("archived")),
    }


def prepare(rows: list[Any], options: Mapping[str, Any], kind: str | None = None):
    maximum = integer(options, "max_records", 1000, 1, 10000)
    if len(rows) > maximum:
        raise InputError("Input exceeds max_records; split the input rather than silently truncating it.")
    total_limit = integer(options, "max_text_chars", 2_000_000, 1, 10_000_000)
    seen, removed, dropped, conflicts = {}, set(), 0, set()
    total = 0
    for value in rows:
        record = normalise(value, kind)
        if record is None:
            data = value.get("data", value)
            k = value.get("kind", kind) or data.get("kind")
            identity = fullname(data.get("name") or data.get("id"), k)
            removed.add(identity)
            seen.pop(identity, None)
            dropped += 1
            continue
        if record["id"] in removed:
            continue
        if ((record["over_18"] and not boolean(options, "include_nsfw"))
                or (record["stickied"] and boolean(options, "exclude_stickied"))):
            dropped += 1
            continue
        total += len(record["title"]) + len(record["body"])
        if total > total_limit:
            raise InputError("Input exceeds max_text_chars; no partial analysis was produced.")
        if record["id"] in seen and seen[record["id"]] != record:
            conflicts.add(record["id"])
        seen.setdefault(record["id"], record)
    return list(seen.values()), {"input_records": len(rows), "excluded_records": dropped,
                               "removed_ids": sorted(removed), "conflicting_ids": sorted(conflicts)}


def visible(value: str) -> str:
    """Suppress code, quoted lines, link destinations and user mentions, not stopword gaps."""
    value = html.unescape(value)
    value = re.sub(r"```.*?```|`[^`]*`", "\n", value, flags=re.S)
    value = re.sub(r"(?m)^\s*>.*$", "\n", value)  # quoted material is not author's own language
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"https?://\S+|(?<!\w)/?u/[A-Za-z0-9_-]+", "\n", value)
    return value


def validate(options: Mapping[str, Any]) -> None:
    for key, default, low, high in (
        ("limit", 50, 1, 1000), ("min_words", 1, 1, 5), ("max_words", 3, 1, 5),
        ("min_occurrences", 1, 1, 10000), ("max_candidates", 10000, 1, 50000),
        ("max_records", 1000, 1, 10000), ("max_text_chars", 2_000_000, 1, 10_000_000),
        ("cooccurrence_limit", 30, 0, 100),
    ):
        integer(options, key, default, low, high)
    if int(options.get("min_words", 1)) > int(options.get("max_words", 3)):
        raise ConfigurationError("min_words cannot exceed max_words.")
    for key in ("include_keywords", "include_hashtags", "include_flair", "include_records",
                "include_nsfw", "exclude_stickied"):
        boolean(options, key, key in {"include_keywords", "include_hashtags", "include_flair"})
    if not isinstance(options.get("stopwords", ""), str):
        raise ConfigurationError("stopwords must be comma-separated text.")


def extract(value: str, options: Mapping[str, Any]) -> Counter[tuple[str, str]]:
    result: Counter[tuple[str, str]] = Counter()
    value = visible(value)
    if boolean(options, "include_hashtags", True):
        result.update(("hashtag", "#" + m[1].casefold()) for m in HASH.finditer(value))
    result.update(("community-mention", "r/" + m[1].lower()) for m in COMMUNITY.finditer(value))
    # Hashes/community mentions remain distinct categories, not plain duplicated n-grams.
    value = HASH.sub("\n", COMMUNITY.sub("\n", value))
    if not boolean(options, "include_keywords", True):
        return result
    low = integer(options, "min_words", 1, 1, 5)
    high = integer(options, "max_words", 3, 1, 5)
    if low > high:
        raise ConfigurationError("min_words cannot exceed max_words.")
    extra = options.get("stopwords", "")
    if not isinstance(extra, str):
        raise ConfigurationError("stopwords must be comma-separated text.")
    stops = STOP | {w.strip().casefold() for w in extra.split(",") if w.strip()}
    for line in re.split(r"[\r\n.!?;,:()\[\]{}|/]+", value):
        tokens = list(WORD.finditer(line))
        for start in range(len(tokens)):
            for width in range(low, high + 1):
                group = tokens[start:start + width]
                if len(group) != width:
                    break
                words = [m[0].casefold() for m in group]
                if (any(len(w) < 2 or w in stops or w.isnumeric() for w in words)
                        or any(not line[a.end():b.start()].isspace()
                               for a, b in zip(group, group[1:]))):
                    continue
                result[("keyword", " ".join(words))] += 1
    return result


def context(source: str, scope: str, observed_at: Any, **extra: Any) -> dict[str, Any]:
    if not source or not scope:
        raise InputError("source and scope are required for reproducible Reddit measurements.")
    return {"platform": "Reddit", "schema": VERSION, "source": source, "scope": scope,
            "observed_at": timestamp(observed_at, required=True), **extra}


def evidence(ctx: Mapping[str, Any], name: str, value: Any, unit: str, note: str = ""):
    return KeywordEvidence(source=ctx["source"], metric=name, value=value, unit=unit,
                           observed_at=ctx["observed_at"], geography=ctx.get("country"), notes=note)


def analyse(rows: list[Any], options: Mapping[str, Any], ctx: Mapping[str, Any],
            operation: str = "import-posts", kind: str | None = None) -> PluginResult:
    records, audit = prepare(rows, options, kind)
    max_candidates = integer(options, "max_candidates", 10000, 1, 50000)
    minimum = integer(options, "min_occurrences", 1, 1, 10000)
    table: dict[tuple[str, str], dict[str, Any]] = {}
    lookup = {r["id"]: r for r in records}
    for record in records:
        for field in ("title", "body", "flair"):
            if field == "flair":
                terms = Counter({("flair", record[field].casefold()): 1}) if (
                    record[field] and boolean(options, "include_flair", True)) else Counter()
            else:
                terms = extract(record[field], options)
            for key, count in terms.items():
                if key not in table and len(table) >= max_candidates:
                    raise InputError("Candidate vocabulary exceeds max_candidates; narrow input/features.")
                entry = table.setdefault(key, {"occurrences": 0, "ids": set(), "fields": Counter()})
                entry["occurrences"] += count
                entry["ids"].add(record["id"])
                entry["fields"][field] += count
    now = datetime.fromisoformat(ctx["observed_at"].replace("Z", "+00:00"))
    items = []
    for (category, phrase), entry in table.items():
        if entry["occurrences"] < minimum:
            continue
        matched = [lookup[i] for i in sorted(entry["ids"])]
        metrics = {"document_frequency": (len(matched), "records"),
                   "occurrences": (entry["occurrences"], "literal_occurrences")}
        for k, label in (("t3", "post"), ("t1", "comment")):
            subset = [r for r in matched if r["kind"] == k]
            metrics[f"sample_{label}_count"] = (len(subset), "records")
            fields = ("score", "num_comments", "upvote_ratio", "provider_reported_votes") if k == "t3" else ("score", "provider_reported_votes")
            for field in fields:
                values = [r[field] for r in subset if r[field] is not None]
                prefix = f"sample_{label}_{'comments' if field == 'num_comments' else field}"
                unit = "ratio_0_1" if field == "upvote_ratio" else (
                    "reported_net_score" if field == "score" else ("provider_reported_votes" if field == "provider_reported_votes" else "comments"))
                metrics[prefix + "_observed"] = (len(values), "records")
                metrics[prefix + "_median"] = (median(values) if values else None, unit)
                metrics[prefix + "_mean"] = (mean(values) if values else None, unit)
            dated = [datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                     for r in subset if r["created_at"]]
            metrics[f"sample_{label}s_timestamped"] = (len(dated), "records")
            for days in (7, 30):
                metrics[f"sample_{label}s_{days}d"] = (
                    sum(0 <= (now - date).total_seconds() <= days * 86400 for date in dated), "records")
        meta = {"kind": category, "source": ctx["source"], "scope": ctx["scope"],
                "record_ids": sorted(entry["ids"]), "fields": dict(entry["fields"]),
                "subreddits": dict(sorted(Counter(r["subreddit"] for r in matched
                                                  if r["subreddit"]).items())),
                "known_status": ctx.get("known_status", "observed-in-source-text"), "metrics": {k: v[0] for k, v in metrics.items()}}
        items.append(KeywordCandidate(phrase, "reddit-" + category, None,
                                      tuple(evidence(ctx, k, v, u) for k, (v, u) in metrics.items()), meta))
    order = choice(options, "sort_by", "document_frequency", SORTS)
    if order == "phrase":
        items.sort(key=lambda k: (k.phrase, k.relationship))
    else:
        items.sort(key=lambda k: (k.metadata["metrics"].get(order) is None,
                                 -(k.metadata["metrics"].get(order) or 0), k.phrase, k.relationship))
    limit = integer(options, "limit", 50, 1, 1000)
    returned = items[:limit]
    # Only top selected candidates take part in the bounded association matrix.
    co_limit = integer(options, "cooccurrence_limit", 30, 0, 100)
    chosen = returned[:co_limit]
    for item in chosen:
        ids = set(item.metadata["record_ids"])
        peers = []
        for other in chosen:
            if other is item:
                continue
            other_ids = set(other.metadata["record_ids"])
            shared = len(ids & other_ids)
            if shared:
                peers.append({"phrase": other.phrase, "kind": other.metadata["kind"],
                              "shared_records": shared, "jaccard": shared / len(ids | other_ids)})
        item.metadata["cooccurrence"] = sorted(peers, key=lambda v: (-v["shared_records"], v["phrase"]))[:10]
    meta = {**ctx, **audit, "sample_records": len(records), "candidate_count": len(items),
            "output_truncated": len(items) > len(returned), "record_ids": sorted(lookup),
            "retention": "Remove deleted content and regenerate derived data; routinely expire within 48h.",
            "tokenizer": "literal Unicode ngrams; no stemming or model inference",
            "analysis_signature": signature(options),
            "analysis_options": {k: options[k] for k in ANALYSIS_OPTIONS if k in options}}
    if boolean(options, "include_records"):
        meta["records"] = records
    return PluginResult("reddit", operation, tuple(returned), NOTES, meta)


def signature(options: Mapping[str, Any]) -> str:
    defaults = {"include_keywords": True, "include_hashtags": True, "include_flair": True,
                "include_nsfw": False, "exclude_stickied": False, "min_words": 1, "max_words": 3,
                "min_occurrences": 1, "max_candidates": 10000, "stopwords": ""}
    data = {key: options.get(key, value) for key, value in defaults.items()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def competition(records: list[Any], query: str, options: Mapping[str, Any], ctx: Mapping[str, Any]):
    rows, audit = prepare(records, options)
    posts = [r for r in rows if r["kind"] == "t3"]
    terms = re.compile(r"(?<!\w)" + re.escape(query.casefold()) + r"(?!\w)")
    matches = [r for r in posts if terms.search(visible(r["title"] + "\n" + r["body"]).casefold())]
    communities = Counter(r["subreddit"] for r in posts if r["subreddit"])
    sites = Counter(urlsplit(r["url"]).hostname for r in posts if r["url"])
    scores = [r["score"] for r in matches if r["score"] is not None]
    comments = [r["num_comments"] for r in matches if r["num_comments"] is not None]
    numbers = {"sample_posts": (len(posts), "posts"), "literal_matching_posts": (len(matches), "posts"),
               "sample_subreddit_count": (len(communities), "subreddits"),
               "matching_score_median": (median(scores) if scores else None, "reported_net_score"),
               "matching_comments_median": (median(comments) if comments else None, "comments"),
               "matching_score_observed": (len(scores), "posts"),
               "matching_comments_observed": (len(comments), "posts"),
               "sample_locked_posts": (sum(r["locked"] for r in posts), "posts"),
               "sample_stickied_posts": (sum(r["stickied"] for r in posts), "posts")}
    item = KeywordCandidate(query, "reddit-attention-context", None,
                            tuple(evidence(ctx, k, v, u) for k, (v, u) in numbers.items()),
                            {"subreddit_distribution": dict(communities), "linked_hosts": dict(sites),
                             "matching_record_ids": [r["id"] for r in matches]})
    return PluginResult("reddit", "competition", (item,), NOTES + (
        "This is observed competition for discussion/attention, not SEO difficulty or a ranking forecast.",),
        {**ctx, **audit, "record_ids": [r["id"] for r in rows]})


def communities(rows: list[Any], ctx: Mapping[str, Any], options: Mapping[str, Any], operation: str):
    if len(rows) > integer(options, "max_records", 1000, 1, 10000):
        raise InputError("Community input exceeds max_records.")
    output, seen = [], set()
    for value in rows:
        raw = value.get("data", value) if isinstance(value, dict) else None
        if not isinstance(raw, dict):
            raise InputError("Community rows must be objects.")
        name = subreddit(raw.get("display_name", raw.get("subreddit")))
        if "over18" in raw and raw["over18"] is not None and not isinstance(raw["over18"], bool):
            raise InputError("Community over18 must be a boolean.")
        if name in seen or (raw.get("over18") and not boolean(options, "include_nsfw")):
            continue
        seen.add(name)
        metrics = {"subscribers": (metric(raw.get("subscribers")), "subscribers"),
                   "reported_active_accounts": (metric(raw.get("accounts_active", raw.get("active_user_count"))),
                                                 "accounts_at_capture")}
        output.append(KeywordCandidate("r/" + name, "reddit-community", None,
                      tuple(evidence(ctx, k, v, unit) for k, (v, unit) in metrics.items()),
                      {"title": clean(raw.get("title")), "public_description": clean(raw.get("public_description")),
                       "subreddit_type": raw.get("subreddit_type"), "known_status": "source-resolved-community"}))
    limit = integer(options, "limit", 50, 1, 1000)
    return PluginResult("reddit", operation, tuple(output[:limit]), (
        "Community size is not keyword search volume; active accounts are a time-specific display.",),
        {**ctx, "rows_received": len(rows), "output_truncated": len(output) > limit})
