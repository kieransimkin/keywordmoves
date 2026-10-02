"""TikTok keyword sources: explicit access routes with separately labelled evidence.

No third-party imports, credential reads, downloads, scraping or paid jobs at import time.
See docs/tiktok.md for provider contracts, permissions, scopes and validation limits.
"""
from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)
from .common import (
    HTTP,
    OnlineSourceError,
    boolean,
    choice,
    code,
    integer,
    iso_date,
    obj,
    secret,
    seeds,
    text,
)
from .tiktok_analysis import (
    analyse,
    compare,
    count,
    finish,
    first,
    hashtag,
    identifier,
    observations,
    records,
    timestamp,
    video_url,
)
from .tiktok_imports import (
    challenge_page,
    decode_json,
    html_records,
    load_records,
    read,
    reference_records,
)

VIDEO_FIELDS = "id,create_time,video_description,title,share_url,duration,like_count,comment_count,share_count,view_count"
RESEARCH_FIELDS = "id,video_description,create_time,region_code,share_count,view_count,like_count,comment_count,music_id,hashtag_names,username,favorites_count,video_duration"
COMMENT_FIELDS = "id,video_id,text,like_count,reply_count,parent_comment_id,create_time"
PROFILE_FIELDS = "display_name,bio_description,username,is_verified,follower_count,following_count,likes_count,video_count"
ACTORS = {
    "hashtag-videos": "clockworks~tiktok-scraper", "keyword-videos": "clockworks~tiktok-scraper",
    "profile-videos": "clockworks~tiktok-scraper", "video-details": "clockworks~tiktok-scraper",
    "related-videos": "clockworks~tiktok-scraper", "hashtag-analytics": "clockworks~tiktok-ads-scraper",
}
OUTPUT_OPTIONS = {"limit", "sort_by"}
ANALYSIS_OPTIONS = {"include_keywords", "include_transcript", "max_words", "stopwords", "max_occurrences", "max_records"}
HTTP_OPTIONS = {"max_requests", "max_response_bytes", "timeout", "min_interval"}
IMPORT_OPTIONS = {"source", "scope", "observed_at", "records_path", "column_map", "max_input_bytes", "max_records"}
PAGE_OPTIONS = {"pages", "page_size", "max_records", "cursor", "search_id"}
AUTH_OPTIONS = {"access_token"}
OP_OPTIONS = {
    "extract": ANALYSIS_OPTIONS | {"text", "max_input_bytes"},
    "import-videos": IMPORT_OPTIONS | ANALYSIS_OPTIONS,
    "import-comments": IMPORT_OPTIONS | ANALYSIS_OPTIONS,
    "import-hashtags": IMPORT_OPTIONS,
    "import-observations": IMPORT_OPTIONS,
    "import-html": IMPORT_OPTIONS | ANALYSIS_OPTIONS | {"dataset_kind", "record_selector", "field_selectors", "empty_selector"},
    "compare": {"max_input_bytes"},
    "oembed": HTTP_OPTIONS | ANALYSIS_OPTIONS,
    "public-hashtag": HTTP_OPTIONS | {"allow_unofficial"},
    "suggestions": HTTP_OPTIONS | {"api_key", "country", "language", "currency", "metrics", "sandbox"},
    "metrics": HTTP_OPTIONS | {"api_key", "country", "language", "currency", "sandbox"},
    "display-videos": HTTP_OPTIONS | AUTH_OPTIONS | (PAGE_OPTIONS - {"search_id"}) | ANALYSIS_OPTIONS,
    "display-query": HTTP_OPTIONS | AUTH_OPTIONS | ANALYSIS_OPTIONS,
    "display-user": HTTP_OPTIONS | AUTH_OPTIONS | ANALYSIS_OPTIONS | {"profile_fields"},
    "research-videos": HTTP_OPTIONS | AUTH_OPTIONS | PAGE_OPTIONS | ANALYSIS_OPTIONS | {
        "research_approved", "start_date", "end_date", "query_field", "region", "is_random"},
    "research-comments": HTTP_OPTIONS | AUTH_OPTIONS | (PAGE_OPTIONS - {"search_id"}) | ANALYSIS_OPTIONS | {"research_approved", "video_id", "comment_id"},
    "research-user": HTTP_OPTIONS | AUTH_OPTIONS | ANALYSIS_OPTIONS | {"research_approved", "username"},
    "commercial-ads": HTTP_OPTIONS | AUTH_OPTIONS | PAGE_OPTIONS | {"commercial_approved", "start_date", "end_date", "country", "search_type"},
    "apify-start": HTTP_OPTIONS | {"apify_token", "actor", "allow_paid", "max_charge_usd", "actor_timeout", "actor_build",
                                  "results_per_seed", "country", "period", "related_searches", "comments_per_post", "replies_per_comment",
                                  "video_sort", "video_date_filter"},
    "apify-fetch": HTTP_OPTIONS | ANALYSIS_OPTIONS | {"apify_token", "run_id", "dataset_id", "dataset_kind", "scope", "observed_at",
                                                   "pages", "page_size", "offset", "country", "period"},
}
LOCAL = {"extract", "import-videos", "import-comments", "import-hashtags", "import-observations", "import-html", "compare"}


def _id(options: Mapping[str, Any], key: str) -> str:
    value = text(options, key)
    if not re.fullmatch(r"[0-9]{1,19}", value) or int(value) > 2**63 - 1:
        raise ConfigurationError(f"{key} must be a positive signed 64-bit decimal ID.")
    if int(value) <= 0:
        raise ConfigurationError(f"{key} must be positive.")
    return value


def _opaque(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 32 for c in value):
        raise OnlineSourceError("Invalid pagination search_id in TikTok response.")
    return value


def _data(payload: Any) -> dict[str, Any]:
    raw = obj(payload)
    error = obj(raw.get("error"))
    if error.get("code") != "ok":
        raise OnlineSourceError("TikTok reported an API error. Check token, approval, scopes, quota and parameters; raw errors are not printed.")
    return obj(raw.get("data"))


def _more(data: dict[str, Any]) -> bool:
    value = data.get("has_more")
    # Some official ad-library examples use strings although the schema says boolean.
    if value in ("true", "false"):
        return value == "true"
    if not isinstance(value, bool):
        raise OnlineSourceError("Missing or invalid TikTok pagination state.")
    return value


def _collect(http: HTTP, path: str, headers: dict[str, str], body: dict[str, Any], fields: str,
             key: str, options: Mapping[str, Any], page_cap: int, *, cursor: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    size = integer(options, "page_size", min(20, page_cap), 1, page_cap)
    pages = integer(options, "pages", 1, 1, 10)
    maximum = integer(options, "max_records", 2000, 1, 10000)
    current = dict(body)
    if "cursor" in options:
        if not cursor:
            raise ConfigurationError("This endpoint uses search_id, not cursor.")
        current["cursor"] = integer(options, "cursor", 0, 0, 2**63 - 1)
    if "search_id" in options:
        current["search_id"] = _opaque(options["search_id"])
    seen, rows, more, completed = set(), [], False, 0
    for _ in range(pages):
        if http.requests >= http.maximum:
            break
        requested = min(size, maximum - len(rows))
        if requested <= 0:
            break
        current["max_count"] = requested
        state = _data(http.json("POST", "https://open.tiktokapis.com/v2/" + path,
                                headers=headers, params={"fields": fields}, json=current))
        page = records(state.get(key), requested)
        rows.extend(page)
        completed += 1
        more = _more(state)
        if not more:
            break
        if cursor:
            next_cursor = count(state.get("cursor"))
            if next_cursor is None or next_cursor > 2**63 - 1:
                raise OnlineSourceError("Missing or invalid next cursor.")
            current["cursor"] = next_cursor
        if "search_id" in state:
            current["search_id"] = _opaque(state["search_id"])
        if not cursor and "search_id" not in current:
            raise OnlineSourceError("Missing next search_id.")
        marker = (current.get("cursor"), current.get("search_id"))
        if marker in seen:
            raise OnlineSourceError("Repeated pagination cursor; stopped instead of requesting duplicates indefinitely.")
        seen.add(marker)
    return rows, {"more_available": more, "pages_fetched": completed,
                  "next_cursor": current.get("cursor") if more else None,
                  "next_search_id": current.get("search_id") if more else None,
                  "collection_complete": not more,
                  "completeness_note": "Complete pagination means only the provider's accessible result set, not all TikTok content."}


class TikTokPlugin:
    descriptor = PluginDescriptor(
        "tiktok", "Discover TikTok hashtags and keywords; analyse separately scoped API, sample, trend and search evidence.",
        ("discover", "analyse", "hashtags", "official-api", "third-party-api", "import", "compare"), tuple(OP_OPTIONS))

    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        op, o = request.operation, dict(request.options)
        if op not in OP_OPTIONS:
            raise ConfigurationError("Unknown TikTok operation; see docs/tiktok.md.")
        unknown = set(o) - (OP_OPTIONS[op] | OUTPUT_OPTIONS)
        if unknown:
            raise ConfigurationError("Unsupported TikTok options for this operation; see docs/tiktok.md (LLM flags are not used).")
        now = datetime.now(timezone.utc).isoformat()
        finish(op, [], {}, o, now)  # validate output limits and ordering before paid/network activity
        if request.inputs and op not in LOCAL:
            raise ConfigurationError("Network operations take --keyword/options, not input files.")
        if op in LOCAL:
            items, meta, observed = self._local(request, o, now)
            return finish(op, items, {"access": "local", "live_query_performed": False, **meta}, o, observed)
        if op in {"suggestions", "metrics"}:
            return self._keywordtool(request, o, now)
        host = "api.apify.com" if op.startswith("apify-") else "www.tiktok.com" if op in {"oembed", "public-hashtag"} else "open.tiktokapis.com"
        with HTTP(o, (host,), self._transport) as http:
            if op.startswith("apify-"):
                items, meta, observed = self._apify(request, o, now, http)
            elif op in {"oembed", "public-hashtag"}:
                items, meta, observed = self._public(request, o, now, http)
            else:
                items, meta = self._official(request, o, now, http)
                observed = now
            meta.update(request_count=http.requests, live_query_performed=True, retrieved_at=now)
        return finish(op, items, meta, o, observed)

    def _local(self, request, o, now):
        op = request.operation
        if op == "extract":
            items, meta = analyse(reference_records(request.inputs, o), o, "Local TikTok reference text", now, kind="text", known=request.keywords)
            return items, meta, now
        if op == "compare":
            if request.keywords or len(request.inputs) != 2:
                raise ConfigurationError("compare needs exactly two saved JSON results, older first, and no keywords.")
            maximum = integer(o, "max_input_bytes", 5_000_000, 1024, 20_000_000)
            before, after = [decode_json(read(p, maximum)) for p in request.inputs]
            if not isinstance(before, dict) or not isinstance(after, dict):
                raise InputError("Snapshots must be result objects.")
            return compare(before, after), {"comparison": "compatible-exact-aggregate-observations-only"}, now
        observed = timestamp(text(o, "observed_at")).isoformat()
        source, scope = text(o, "source"), text(o, "scope")
        kind = {"import-videos": "videos", "import-comments": "comments", "import-hashtags": "hashtags",
                "import-observations": "observations"}.get(op)
        if op == "import-html":
            if len(request.inputs) != 1:
                raise ConfigurationError("import-html takes exactly one saved rendered HTML file.")
            kind = choice(o, "dataset_kind", "hashtags", ("hashtags", "observations", "videos", "comments"))
            rows = html_records(read(request.inputs[0], integer(o, "max_input_bytes", 5_000_000, 1024, 20_000_000)), o)
        else:
            rows = load_records(request.inputs, o, kind)
        if kind in {"videos", "comments"}:
            items, meta = analyse(rows, o, source, observed, kind=kind, known=request.keywords)
        else:
            if request.keywords:
                raise ConfigurationError("Observation imports use phrases in the records, not keyword seeds.")
            items = observations(rows, source, observed, scope, hashtags_only=kind == "hashtags")
            meta = {"rows_received": len(rows)}
        return items, {**meta, "source": source, "scope": scope}, observed

    def _keywordtool(self, request, o, observed):
        from .commercial import KeywordToolPlugin
        # Unlike Instagram, Keyword Tool does not expose type=hashtags for TikTok.
        forwarded = {k: v for k, v in o.items() if k not in OUTPUT_OPTIONS}
        forwarded.update(platform="tiktok", suggestion_type="suggestions")
        with HTTP(o, ("api.keywordtool.io",), self._transport) as http:
            items, meta, notes = KeywordToolPlugin().fetch(
                PluginRequest(request.operation, request.keywords, options=forwarded), http, observed)
            meta["request_count"] = http.requests
        scope = f"keywordtool:{request.operation}:{o.get('country')}:{o.get('language', 'en')}:{boolean(o, 'sandbox')}"
        converted = []
        for item in items:
            converted.append(replace(item, relationship="tiktok-search-suggestion" if request.operation == "suggestions" else "tiktok-search-estimate",
                evidence=tuple(replace(e, metric={"cpc": "google_ads_cpc_proxy", "paid_competition": "google_ads_competition_proxy"}.get(e.metric, e.metric),
                    notes="TikTok search estimate/order from Keyword Tool, not hashtag post count. CPC and competition are Google Ads proxies.") for e in item.evidence),
                metadata={**item.metadata, "platform": "TikTok", "kind": "keyword", "verification": "not-a-confirmed-hashtag",
                          "scope": scope, "window": "provider-monthly-estimate", "approximate_metrics": ["estimated_search_volume"]}))
        return finish(request.operation, converted, {**meta, "access": "third-party-api", "live_query_performed": True,
                      "retrieved_at": observed, "source": "Keyword Tool API", "scope": scope, "provider_notes": notes}, o, observed)

    def _official(self, request, o, observed, http):
        op = request.operation
        research = op.startswith("research-")
        if research and not boolean(o, "research_approved"):
            raise ConfigurationError("Research API needs research_approved=true and an approved eligible research project; it is not a creator/advertiser marketing API.")
        if op == "commercial-ads" and not boolean(o, "commercial_approved"):
            raise ConfigurationError("commercial-ads needs commercial_approved=true and approved Commercial Content API access.")
        env = "TIKTOK_RESEARCH_ACCESS_TOKEN" if research else "TIKTOK_COMMERCIAL_ACCESS_TOKEN" if op == "commercial-ads" else "TIKTOK_ACCESS_TOKEN"
        headers = {"Authorization": "Bearer " + secret(o, "access_token", env)}
        source = "TikTok Research API" if research else "TikTok Display API"
        scope = {"operation": op}
        meta = {"access": "official-api", "source": source, "query_scope": scope}
        if research:
            meta["research_caveat"] = "Use only for approved research. Query-video data is archived: new videos may lag 48 hours, statistics up to 10 days."
        if op in {"display-user", "research-user"}:
            if request.keywords:
                raise ConfigurationError("Profile operations use the authorised user or username option, not --keyword.")
            if research:
                name = code(o, "username", pattern=r"[A-Za-z0-9_.]{1,40}")
                fields = PROFILE_FIELDS.replace(",username", "")
                data = _data(http.json("POST", "https://open.tiktokapis.com/v2/research/user/info/", headers=headers,
                                      params={"fields": fields}, json={"username": name}))
                scope["username"] = name
            else:
                selected = text(o, "profile_fields", "display_name").split(",")
                if not set(selected) <= set(PROFILE_FIELDS.split(",")):
                    raise ConfigurationError("Unsupported profile_fields; see the documented Display API scopes.")
                data = obj(_data(http.json("GET", "https://open.tiktokapis.com/v2/user/info/", headers=headers,
                                          params={"fields": ",".join(selected)})).get("user"))
            profile = {k: data[k] for k in ("display_name", "username", "is_verified") if k in data}
            for k in ("follower_count", "following_count", "likes_count", "video_count"):
                profile[k] = count(data.get(k))
            caption = "\n".join(text({"v": data[k]}, "v") for k in ("display_name", "bio_description") if data.get(k))
            items, stats = analyse([{"caption": caption}], o, source + " profile text", observed, kind="profile")
            return items, {**meta, **stats, "profile": profile, "profile_metric_note": "Account totals are not hashtag popularity."}
        if op == "commercial-ads":
            return self._ads(request, o, observed, http, headers)
        if op == "display-query":
            ids = [_id({"video_id": v}, "video_id") for v in seeds(request, 20)]
            data = _data(http.json("POST", "https://open.tiktokapis.com/v2/video/query/", headers=headers,
                                  params={"fields": VIDEO_FIELDS}, json={"filters": {"video_ids": ids}}))
            rows, page = records(data.get("videos"), 20), {"requested_ids": ids, "unreturned_ids": sorted(set(ids) - {str(r.get("id")) for r in records(data.get("videos"), 20)})}
            scope["video_ids"] = ids
        elif op == "display-videos":
            if request.keywords:
                raise ConfigurationError("display-videos lists the authorised account, not a hashtag search. Use import-videos to select known tags later.")
            rows, page = _collect(http, "video/list/", headers, {}, VIDEO_FIELDS, "videos", o, 20)
        elif op == "research-comments":
            if request.keywords or ("video_id" in o) == ("comment_id" in o):
                raise ConfigurationError("Supply exactly one video_id or comment_id (replies), and no keyword seeds.")
            key = "video_id" if "video_id" in o else "comment_id"
            target = _id(o, key)
            scope[key] = target
            rows, page = _collect(http, "research/video/comment/list/", headers, {key: int(target)}, COMMENT_FIELDS, "comments", o, 100)
            # Comment 'video_id' identifies the parent video, not the comment itself.
            for row in rows:
                if row.get("id") is None:
                    raise OnlineSourceError("Comment ID missing; refusing to deduplicate different comments as a video.")
            items, stats = analyse(rows, o, source + " comments", observed, kind="comments")
            return items, {**meta, **page, **stats, "comments_note": "Comment language/likes, not video hashtag usage. Replies are queried explicitly, not recursively."}
        elif op == "research-videos":
            begin, end = iso_date(o, "start_date"), iso_date(o, "end_date")
            days = (timestamp(end) - timestamp(begin)).days
            if not 0 <= days <= 30:
                raise ConfigurationError("Research video date range must be ordered and at most 30 days apart.")
            field = choice(o, "query_field", "hashtag_name", ("hashtag_name", "keyword", "username", "music_id", "effect_id", "video_id"))
            terms = seeds(request, 20)
            if field == "hashtag_name":
                terms = [hashtag(v)[1:] for v in terms]
            elif field in {"video_id", "music_id", "effect_id"}:
                terms = [_id({field: v}, field) for v in terms]
            elif field == "username":
                terms = [code({"username": v}, "username", pattern=r"[A-Za-z0-9_.]{1,40}") for v in terms]
            conditions = [{"field_name": field, "operation": "EQ", "field_values": [v]} for v in terms]
            query: dict[str, Any] = {"or": conditions}
            if "region" in o:
                region = code(o, "region").upper()
                query["and"] = [{"field_name": "region_code", "operation": "EQ", "field_values": [region]}]
            body = {"query": query, "start_date": begin.replace("-", ""), "end_date": end.replace("-", ""), "is_random": boolean(o, "is_random")}
            fields = RESEARCH_FIELDS + (",voice_to_text" if boolean(o, "include_transcript") else "")
            rows, page = _collect(http, "research/video/query/", headers, body, fields, "videos", o, 100)
            scope.update(field=field, terms=terms, start_date=begin, end_date=end, region=o.get("region"), is_random=body["is_random"])
        else:
            raise ConfigurationError("Unknown official TikTok operation.")
        known = tuple(request.keywords) if op == "research-videos" and o.get("query_field", "hashtag_name") == "hashtag_name" else ()
        items, stats = analyse(rows, o, source, observed, known=known)
        return items, {**meta, **page, **stats, "description_note": "Available descriptions may be shortened; absent tags are not proven absent from the original post."}

    def _ads(self, request, o, observed, http, headers):
        term = seeds(request)[0]
        if len(term) > 50:
            raise ConfigurationError("Commercial Content search terms are at most 50 characters.")
        begin, end = iso_date(o, "start_date"), iso_date(o, "end_date")
        if timestamp(begin) > timestamp(end):
            raise ConfigurationError("Ad date range must be ordered.")
        country = code(o, "country").upper()
        body = {"filters": {"ad_published_date_range": {"min": begin.replace("-", ""), "max": end.replace("-", "")},
                            "country_code_list": [country]}, "search_term": term,
                "search_type": choice(o, "search_type", "exact_phrase", ("exact_phrase", "fuzzy_phrase"))}
        rows, page = _collect(http, "research/adlib/ad/query/", headers, body,
                              "ad.id,ad.first_shown_date,ad.last_shown_date,ad.status,ad.reach,advertiser.business_name",
                              "ads", o, 10, cursor=False)
        ads, seen = [], set()
        for row in rows:
            ad = obj(row.get("ad"))
            ident = identifier(ad.get("id"))
            if not ident:
                raise OnlineSourceError("Ad ID missing from library response.")
            if ident in seen:
                continue
            seen.add(ident)
            reach = obj(ad.get("reach") or {})
            ads.append({"ad_id": ident, "first_shown_date": ad.get("first_shown_date"), "last_shown_date": ad.get("last_shown_date"),
                        "status": ad.get("status"), "reach_display": first(reach, "unique_users_seen", "unique_user_seen"),
                        "reach_note": "Original rounded/ranged ad reach; not summed across overlapping audiences."})
        item = KeywordCandidate(term, "tiktok-ad-library-match", evidence=(KeywordEvidence(
            "TikTok Commercial Content API", "sample_matching_ad_count", len(ads), "count", observed, country,
            "Bounded accessible ad-library matches, not TikTok searches or organic hashtag popularity."),),
            metadata={"platform": "TikTok", "kind": "keyword", "ads": ads, "verification": "ad-search-term-only"})
        return [item], {**page, "access": "official-commercial-content-api", "query_scope": body["filters"],
                        "search_type": body["search_type"], "coverage_note": "Only supported library countries/dates; not the whole advertising market."}

    def _public(self, request, o, observed, http):
        terms = seeds(request, 10 if request.operation == "oembed" else 1)
        if request.operation == "oembed":
            urls = [video_url(v) for v in terms]
            if len(urls) > http.maximum:
                raise ConfigurationError("Increase max_requests to cover the supplied video URLs.")
            rows = []
            for url in urls:
                payload = obj(http.json("GET", "https://www.tiktok.com/oembed", params={"url": url}))
                if payload.get("type") != "video" or not isinstance(payload.get("title"), str):
                    raise OnlineSourceError("Unexpected TikTok oEmbed response.")
                rows.append({"id": url.rsplit("/", 1)[1], "url": url, "caption": payload["title"]})
            items, meta = analyse(rows, o, "TikTok oEmbed captions", observed)
            return items, {**meta, "access": "official-oembed", "engagement_metrics_available": False}, observed
        if not boolean(o, "allow_unofficial"):
            raise ConfigurationError("public-hashtag needs allow_unofficial=true; this page-state parser is experimental.")
        from .websites import check_robots
        tag = hashtag(terms[0])
        url = "https://www.tiktok.com/tag/" + quote(tag[1:], safe="")
        check_robots(http, url)
        _, body = http.request("GET", url)
        rows = challenge_page(body, tag)
        items = observations(rows, "TikTok public hashtag page", observed, "public-tag-page:unpersonalised", hashtags_only=True)
        return items, {"access": "experimental-public-html", "source_url": url, "scope": "public-tag-page:unpersonalised",
                       "live_compatibility": "not guaranteed; blocked/changed pages fail explicitly"}, observed

    def _apify(self, request, o, observed, http):
        headers = {"Authorization": "Bearer " + secret(o, "apify_token", "APIFY_TOKEN")}
        if request.operation == "apify-start":
            if not boolean(o, "allow_paid"):
                raise ConfigurationError("Apify jobs may incur charges; supply allow_paid=true explicitly.")
            charge = o.get("max_charge_usd")
            try:
                if isinstance(charge, bool) or not math.isfinite(float(charge)) or not 0 < float(charge) <= 100:
                    raise ValueError
                charge = float(charge)
            except (ValueError, TypeError, OverflowError):
                raise ConfigurationError("Supply explicit max_charge_usd greater than zero and at most 100.") from None
            actor = choice(o, "actor", "hashtag-videos", tuple(ACTORS))
            terms = seeds(request, 10)
            n = integer(o, "results_per_seed", 50, 1, 1000)
            analytics_only = {"country", "period"}
            video_only = {"results_per_seed", "comments_per_post", "replies_per_comment"}
            keyword_only = {"related_searches", "video_sort", "video_date_filter"}
            if ((actor == "hashtag-analytics" and set(o) & (video_only | keyword_only))
                    or (actor != "hashtag-analytics" and set(o) & analytics_only)
                    or (actor != "keyword-videos" and set(o) & keyword_only)):
                raise ConfigurationError("This Apify preset does not use one of the supplied options; no job was started.")
            if actor == "hashtag-analytics":
                body = {"hashtags": [hashtag(v)[1:] for v in terms], "adsCountryCode": code(o, "country").lower(),
                        "adsTimeRange": choice(o, "period", "7", ("7", "30", "120", "365", "1095"))}
            else:
                body = {"resultsPerPage": n, "shouldDownloadVideos": False, "shouldDownloadCovers": False,
                        "shouldDownloadSlideshowImages": False, "shouldDownloadAvatars": False, "shouldDownloadMusicCovers": False,
                        "downloadSubtitlesOptions": "NEVER_DOWNLOAD_SUBTITLES", "aiVideoDescription": False, "aiVideoSummary": False,
                        "commentsPerPost": integer(o, "comments_per_post", 0, 0, 100),
                        "maxRepliesPerComment": integer(o, "replies_per_comment", 0, 0, 20)}
                if body["maxRepliesPerComment"] and not body["commentsPerPost"]:
                    raise ConfigurationError("replies_per_comment requires comments_per_post greater than zero.")
                if actor == "hashtag-videos":
                    body["hashtags"] = [hashtag(v)[1:] for v in terms]
                elif actor == "keyword-videos":
                    body.update(searchQueries=terms, searchSection="/video", scrapeRelatedSearchWords=boolean(o, "related_searches"))
                    if "video_sort" in o:
                        body["videoSearchSorting"] = choice(o, "video_sort", "MOST_RELEVANT", ("MOST_RELEVANT", "MOST_LIKED", "LATEST"))
                    if "video_date_filter" in o:
                        body["videoSearchDateFilter"] = choice(o, "video_date_filter", "ALL_TIME", ("ALL_TIME", "PAST_24_HOURS", "PAST_WEEK", "PAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS"))
                elif actor == "profile-videos":
                    body.update(profiles=[code({"username": v.removeprefix("@")}, "username", pattern=r"[A-Za-z0-9_.]{1,40}") for v in terms],
                                profileScrapeSections=["videos"])
                else:
                    body.update(postURLs=[video_url(v) for v in terms], scrapeRelatedVideos=actor == "related-videos")
            params = {"waitForFinish": 0, "timeout": integer(o, "actor_timeout", 300, 1, 3600), "maxTotalChargeUsd": charge, "restartOnError": "false"}
            if "actor_build" in o:
                params["build"] = code(o, "actor_build", pattern=r"[A-Za-z0-9_.-]{1,80}")
            run = obj(obj(http.json("POST", f"https://api.apify.com/v2/actors/{ACTORS[actor]}/runs", headers=headers, params=params, json=body)).get("data"))
            run_id = code({"run_id": run.get("id")}, "run_id", pattern=r"[A-Za-z0-9]{1,80}")
            return [], {"access": "third-party-job", "status": "submitted", "run_id": run_id, "actor": ACTORS[actor],
                        "requested_charge_limit_usd": charge, "provider_status": run.get("status"),
                        "submitted_input": body, "actor_build": params.get("build"),
                        "query_scope": {"preset": actor, "terms": terms, "country": o.get("country"), "period": o.get("period", "7") if actor == "hashtag-analytics" else None},
                        "related_search_words_note": "When enabled, the actor retains related-search words in its raw video dataset; this adapter does not assume a stable output field or turn them into confirmed hashtags.",
                        "next_step": "Fetch the same run later with apify-fetch. No automatic polling/restart and no completed collection implied."}, observed
        if request.keywords or ("run_id" in o) == ("dataset_id" in o):
            raise ConfigurationError("apify-fetch requires exactly one run_id or dataset_id, and no keyword seeds.")
        scope = text(o, "scope")
        kind = choice(o, "dataset_kind", "videos", ("videos", "comments", "hashtags", "observations"))
        meta = {"access": "third-party-dataset", "scope": scope}
        if "run_id" in o:
            run_id = code(o, "run_id", pattern=r"[A-Za-z0-9]{1,80}")
            run = obj(obj(http.json("GET", "https://api.apify.com/v2/actor-runs/" + run_id, headers=headers)).get("data"))
            status = run.get("status")
            if status in {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}:
                return [], {**meta, "status": status, "collection_complete": False, "run_id": run_id}, observed
            if status != "SUCCEEDED":
                raise OnlineSourceError("Apify job did not succeed; unavailable results are not zero popularity.")
            dataset = run.get("defaultDatasetId")
            observed = timestamp(run.get("finishedAt")).isoformat()
            meta.update(run_id=run_id, actor_id=identifier(run.get("actId")), provider_status=status)
        else:
            dataset = o["dataset_id"]
            observed = timestamp(text(o, "observed_at")).isoformat()
        dataset = code({"dataset_id": dataset}, "dataset_id", pattern=r"[A-Za-z0-9]{1,80}")
        size, pages = integer(o, "page_size", 100, 1, 1000), integer(o, "pages", 1, 1, 10)
        maximum = integer(o, "max_records", 2000, 1, 10000)
        offset = integer(o, "offset", 0, 0, 10**8)
        rows, more = [], True
        for _ in range(pages):
            if http.requests >= http.maximum or len(rows) >= maximum:
                break
            n = min(size, maximum - len(rows))
            page = records(http.json("GET", f"https://api.apify.com/v2/datasets/{dataset}/items", headers=headers,
                                     params={"format": "json", "clean": "true", "offset": offset + len(rows), "limit": n}), n)
            rows.extend(page)
            more = len(page) == n
            if not more:
                break
        source = "Apify TikTok dataset:" + (meta.get("actor_id") or "reviewed-import")
        if kind in {"videos", "comments"}:
            items, stats = analyse(rows, o, source, observed, kind=kind)
        else:
            # User scope MUST include the collection settings. Keep country/period from records.
            prepared = [{**({"country": o["country"]} if "country" in o else {}),
                         **({"period": o["period"]} if "period" in o else {}), **r} for r in rows]
            items, stats = observations(prepared, source, observed, scope, hashtags_only=kind == "hashtags"), {"rows_received": len(rows)}
        return items, {**meta, **stats, "dataset_id": dataset, "more_available": more, "collection_complete": not more,
                       "next_offset": offset + len(rows) if more else None,
                       "comments_note": "Actor comments/replies may be in a separate dataset. Fetch that dataset explicitly with dataset_kind=comments and its capture date."}, observed
