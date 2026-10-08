"""YouTube discovery through explicit API, provider and local-import routes.

All operations are read-only except the explicitly opted-in Apify job submission.
No optional dependency imports, credential reads, or network access at import time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
)
from . import youtube_analysis as analysis
from . import youtube_imports as imports
from .common import (
    HTTP,
    OnlineSourceError,
    boolean,
    choice,
    code,
    integer,
    iso_date,
    obj,
    provider_error,
    secret,
    seeds,
    text,
)

DATA_URL = "https://www.googleapis.com/youtube/v3/"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
PARTS = "snippet,statistics,contentDetails,topicDetails,liveStreamingDetails"
DATA_OPERATIONS = ("search", "hashtag", "videos", "channel", "playlist", "popular", "comments", "captions-list", "captions")
ANALYTICS_OPERATIONS = ("analytics-search", "analytics-hashtags", "analytics-traffic", "analytics-videos")
LOCAL_OPERATIONS = ("extract", "import-videos", "import-comments", "import-observations", "import-html", "import-transcript", "compare", "trends-import")
PROVIDER_OPERATIONS = ("suggestions", "metrics", "serp-search", "dataforseo-search", "autocomplete", "public-hashtag", "apify-start", "apify-fetch")


def identifier(value: Any, kind: str = "identifier") -> str:
    value = text({kind: value}, kind)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise ConfigurationError(f"Invalid {kind}; supply the identifier, not a URL.")
    return value


def opaque_id(value: Any, kind: str) -> str:
    """Comment/reply and caption IDs are opaque and may contain dots or padding."""
    value = text({kind: value}, kind)
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.=-]{0,511}", value):
        raise ConfigurationError(f"Invalid {kind}; supply the opaque ID returned by YouTube.")
    return value


def channel_id(options: Mapping[str, Any]) -> str:
    value = identifier(options.get("channel_id", os.environ.get("YOUTUBE_CHANNEL_ID")), "channel_id")
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", value):
        raise ConfigurationError("channel_id must be the UC-prefixed channel ID (YOUTUBE_CHANNEL_ID).")
    return value


def scope(operation: str, parameters: Mapping[str, Any]) -> str:
    """Stable scope uses only reviewed, non-secret collection parameters."""
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return f"youtube:{operation}:{digest}"


def _input_count(request: PluginRequest, expected: int = 1) -> None:
    if len(request.inputs) != expected:
        raise ConfigurationError(f"This operation requires exactly {expected} --input file(s).")


def _auth(options: Mapping[str, Any], oauth: bool = False) -> tuple[dict, dict]:
    mode = "oauth" if oauth else choice(options, "auth", "key", ("key", "oauth"))
    if mode == "oauth":
        return {}, {"Authorization": "Bearer " + secret(options, "access_token", "YOUTUBE_ACCESS_TOKEN")}
    return {"key": secret(options, "api_key", "YOUTUBE_API_KEY")}, {}


class _DataAPI:
    """Pagination tokens only: provider-supplied URLs are never followed."""
    def __init__(self, http: HTTP, options: Mapping[str, Any], oauth: bool = False) -> None:
        self.http = http
        self.query_auth, self.headers = _auth(options, oauth)
        self.options = options
        self.calls: Counter[str] = Counter()
        self.page_reports: list[dict] = []

    def get(self, route: str, parameters: Mapping[str, Any]) -> dict:
        self.calls[route] += 1
        return provider_error(self.http.json("GET", DATA_URL + route,
                              params={**parameters, **self.query_auth}, headers=self.headers))

    def pages(self, route: str, parameters: Mapping[str, Any], maximum: int | None = None) -> list[dict]:
        maximum = maximum if maximum is not None else integer(self.options, "pages", 1, 1, 10)
        params = dict(parameters)
        rows, tokens, totals = [], set(), []
        token = None
        for page in range(maximum):
            if token:
                params["pageToken"] = token
            data = self.get(route, params)
            if "items" not in data:
                raise OnlineSourceError("YouTube omitted the expected items array.")
            rows.extend(analysis.records(data["items"], 100))
            if route == "search" and isinstance(data.get("pageInfo"), dict):
                totals.append(analysis.count(data["pageInfo"].get("totalResults")))
            token = data.get("nextPageToken")
            if not token:
                break
            if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 for c in token):
                raise OnlineSourceError("Invalid continuation token.")
            if token in tokens:
                raise OnlineSourceError("YouTube repeated a continuation token; stopped rather than looping.")
            tokens.add(token)
        self.page_reports.append({"endpoint": route, "pages_fetched": page + 1,
                                  "more_available": bool(token), "records_received": len(rows),
                                  "approximate_search_result_totals": totals})
        return rows

    def videos(self, ids: list[str]) -> tuple[list[dict], list[str]]:
        ids = list(dict.fromkeys(analysis.video_id(v) for v in ids))
        found = {}
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            data = self.get("videos", {"part": PARTS, "id": ",".join(batch)})
            for row in analysis.records(data.get("items"), 50):
                rid = analysis.video_id(row.get("id"))
                if rid not in batch:
                    raise OnlineSourceError("Video details contain an unrequested ID.")
                found[rid] = row
        return [found[v] for v in ids if v in found], [v for v in ids if v not in found]


class YouTubePlugin:
    descriptor = PluginDescriptor(
        "youtube", "Discover YouTube hashtags, tags and queries; retain source-specific popularity evidence.",
        ("discover", "analyse", "hashtags", "official-api", "authorised-analytics", "import", "compare"),
        DATA_OPERATIONS + ANALYTICS_OPERATIONS + PROVIDER_OPERATIONS + LOCAL_OPERATIONS,
    )

    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport

    def run(self, request: PluginRequest, context: ExecutionContext):
        op, o = request.operation, request.options
        if op not in self.descriptor.operations:
            raise ConfigurationError("YouTube operations: " + ", ".join(self.descriptor.operations))
        if any(k == "llm" or k.startswith("llm_") for k in o):
            raise ConfigurationError("YouTube discovery does not select a generative LLM.")
        # Validate output settings before any request, especially a paid job.
        analysis.finish(op, [], o, {})
        # Validate analysis options before quota/billable work, even on empty samples.
        analysis.analyse([], o, source="preflight", observed_at="2000-01-01",
                         scope="preflight", api_data=op in DATA_OPERATIONS)
        for key, default, low, high in (("pages", 1, 1, 10), ("reply_pages", 1, 1, 5)):
            integer(o, key, default, low, high)
        boolean(o, "include_replies")
        if "include_uploads" in o:
            if op != "channel":
                raise ConfigurationError("include_uploads is only supported by the channel operation.")
            boolean(o, "include_uploads", True)
        observed = datetime.now(timezone.utc).isoformat()
        if op in LOCAL_OPERATIONS:
            items, meta, notes = self._local(request, context, observed)
            meta.update(live_query_performed=False, request_count=0)
        else:
            if request.inputs:
                raise ConfigurationError("Network operations do not accept --input files.")
            if op in DATA_OPERATIONS:
                hosts = ("www.googleapis.com",)
            elif op in ANALYTICS_OPERATIONS:
                hosts = ("youtubeanalytics.googleapis.com",)
            else:
                from .youtube_providers import hosts_for
                hosts = hosts_for(op)
            with HTTP({"max_requests": 10, **o}, hosts, self._transport) as http:
                if op in DATA_OPERATIONS:
                    items, meta, notes = self._data(request, http, observed)
                elif op in ANALYTICS_OPERATIONS:
                    items, meta, notes = self._analytics(request, http, observed)
                else:
                    from .youtube_providers import fetch
                    items, meta, notes = fetch(request, http, observed)
                meta.update(live_query_performed=True, request_count=http.requests)
        return analysis.finish(op, items, o, {"observed_at": observed, **meta}, tuple(notes))

    def _data(self, request, http, observed):
        op, o = request.operation, request.options
        # Fail before spending quota if derived-statistics permission is missing.
        if boolean(o, "derive_metrics") and not boolean(o, "api_derived_metrics_accepted"):
            raise ConfigurationError("derive_metrics for API data requires api_derived_metrics_accepted=true; see docs/youtube.md.")
        api = _DataAPI(http, o, oauth=op.startswith("captions") or (op == "channel" and boolean(o, "mine")))
        raw, extra, params, kind = [], {}, {}, "videos"
        size = integer(o, "page_size", 50, 1, 50)
        source = "YouTube Data API v3"
        if op in {"search", "hashtag"}:
            query = seeds(request)[0]
            if op == "hashtag":
                query = analysis.hashtag(query)
            resource = "video" if op == "hashtag" else choice(o, "search_type", "video", ("video", "channel", "playlist"))
            params = {"part": "snippet", "q": query, "type": resource, "maxResults": size,
                      "order": choice(o, "order", "relevance", ("relevance", "date", "viewCount", "rating", "title"))}
            self._search_filters(o, params, resource)
            matches = api.pages("search", params)
            matches = [m for m in matches if isinstance(m.get("id"), dict) and m["id"].get(resource + "Id")]
            extra["search_results"] = [{"position": i + 1, "id": m["id"][resource + "Id"],
                                        "title": obj(m.get("snippet")).get("title")} for i, m in enumerate(matches)]
            if resource == "video":
                raw, missing = api.videos([m["id"]["videoId"] for m in matches])
                extra["unavailable_video_ids"] = missing
            else:
                kind = "channels" if resource == "channel" else "playlists"
                raw = [{**m, "id": m["id"][resource + "Id"]} for m in matches]
            extra["query"] = query
            if op == "hashtag":
                matched = []
                for row in raw:
                    d = analysis.normalise(row)
                    tags = {v[0] for field in ("title", "description") for v in analysis.hashtag_spans(d[field])}
                    if query in tags:
                        matched.append(row)
                extra.update(videos_before_exact_filter=len(raw), literal_matching_video_count=len(matched),
                             hashtag_verification="observed-in-returned-text" if matched else "not-observed-in-this-sample",
                             unverified_search_video_ids=[r["id"] for r in raw if r not in matched])
                raw = matched
        elif op == "videos":
            ids = [analysis.video_id(v) for v in seeds(request, 200)]
            params = {"video_ids": ids}
            raw, extra["unavailable_video_ids"] = api.videos(ids)
        elif op in {"channel", "playlist"}:
            if request.keywords:
                raise ConfigurationError("Use channel_id, handle, mine or playlist_id options for this operation.")
            if op == "channel":
                selected = [k for k in ("channel_id", "handle", "mine") if k in o]
                if len(selected) > 1:
                    raise ConfigurationError("Select exactly one of channel_id, handle or mine.")
                params = {"part": "snippet,statistics,contentDetails,brandingSettings,topicDetails"}
                if boolean(o, "mine"):
                    params["mine"] = "true"
                elif "handle" in o:
                    params["forHandle"] = text(o, "handle")
                else:
                    params["id"] = channel_id(o)
                channels = analysis.records(api.get("channels", params).get("items"), 50)
                if len(channels) != 1:
                    raise OnlineSourceError("Expected one accessible channel; no public uploads were inferred.")
                channel = channels[0]
                extra["channel"] = {"id": channel["id"], "title": obj(channel.get("snippet")).get("title"),
                                    "description": obj(channel.get("snippet")).get("description"),
                                    "statistics": obj(channel.get("statistics")), "topicDetails": channel.get("topicDetails"),
                                    "subscriber_count_precision": "YouTube rounds subscriberCount to three significant figures; hidden is not zero."}
                if not boolean(o, "include_uploads", True):
                    params = {"channel_id": channel["id"], "include_uploads": False}
                    return [], {**extra, "api_data": True, "source": source, "scope": scope(op, params),
                                "record_kind": "channel", "uploads_included": False,
                                "collection_parameters": params, "pagination": [],
                                "endpoint_calls": dict(api.calls)}, [
                        "Channel profile metadata only; no uploads, video samples or keyword demand are inferred.",
                        "Subscriber counters may be rounded or hidden; missing values are not zero.",
                        "API snapshots need refresh/deletion under the applicable terms.",
                    ]
                playlist = obj(obj(channel.get("contentDetails")).get("relatedPlaylists")).get("uploads")
                if not playlist:
                    raise OnlineSourceError("The channel response has no uploads playlist.")
                # Resolve mine/handle to concrete channel ID before snapshot scoping.
                params = {"channel_id": channel["id"], "playlist_id": playlist}
            else:
                playlist = identifier(o.get("playlist_id"), "playlist_id")
                params = {"playlist_id": playlist}
            entries = api.pages("playlistItems", {"part": "contentDetails,snippet", "playlistId": playlist, "maxResults": size})
            ids = [obj(e.get("contentDetails")).get("videoId") for e in entries if obj(e.get("contentDetails")).get("videoId")]
            raw, extra["unavailable_video_ids"] = api.videos(ids)
            extra["playlist_items_received"] = len(entries)
        elif op == "popular":
            params = {"part": PARTS, "chart": "mostPopular", "maxResults": size}
            if "country" in o:
                params["regionCode"] = code(o, "country").upper()
            if "category_id" in o:
                params["videoCategoryId"] = code(o, "category_id", pattern=r"\d{1,5}")
            raw = api.pages("videos", params)
            extra["chart_scope"] = "Current Music, Movies and Gaming charts; not a whole-site trending census."
        elif op == "comments":
            vid = analysis.video_id(o.get("video_id") if "video_id" in o else seeds(request)[0])
            params = {"part": "snippet", "videoId": vid, "maxResults": integer(o, "page_size", 50, 1, 100),
                      "textFormat": "plainText", "order": choice(o, "order", "relevance", ("relevance", "time"))}
            if "search_terms" in o:
                params["searchTerms"] = text(o, "search_terms")
            threads = api.pages("commentThreads", params)
            parents_with_more = []
            for thread in threads:
                info = obj(thread.get("snippet"))
                comment = self._comment(obj(info.get("topLevelComment")), vid)
                raw.append(comment)
                if analysis.count(info.get("totalReplyCount")):
                    if boolean(o, "include_replies"):
                        replies = api.pages("comments", {"part": "snippet", "parentId": comment["id"], "maxResults": 100,
                                                          "textFormat": "plainText"}, integer(o, "reply_pages", 1, 1, 5))
                        raw.extend(self._comment(r, vid) for r in replies)
                        if api.page_reports[-1]["more_available"]:
                            parents_with_more.append(comment["id"])
                    else:
                        parents_with_more.append(comment["id"])
            kind = "comments"
            extra.update(replies_included=boolean(o, "include_replies"), parents_with_uncollected_replies=parents_with_more)
        else:  # Captions require an owner/editor token even for public videos.
            vid = analysis.video_id(o.get("video_id"))
            params = {"part": "snippet", "videoId": vid}
            tracks = analysis.records(api.get("captions", params).get("items"), 100)
            extra["caption_tracks"] = [{"id": t.get("id"), **{k: obj(t.get("snippet")).get(k) for k in (
                "language", "name", "trackKind", "status", "isAutoSynced", "isDraft", "lastUpdated")}} for t in tracks]
            if op == "captions-list":
                return [], {**extra, "api_data": True, "source": source, "scope": scope(op, params),
                            "endpoint_calls": dict(api.calls)}, ["Caption listing returns track metadata, not a public transcript."]
            caption = opaque_id(o.get("caption_id"), "caption_id")
            if caption not in {t.get("id") for t in tracks}:
                raise ConfigurationError("caption_id does not belong to the requested accessible video.")
            api.calls["captions.download"] += 1
            _, value = http.request("GET", DATA_URL + "captions/" + quote(caption, safe=""), params={"tfmt": "vtt"}, headers=api.headers)
            raw = imports.transcript(value, vid + ":" + caption, "vtt")
            extra["transcript_cues"] = [{k: v for k, v in cue.items() if k != "description"} for cue in raw]
            params["caption_id"] = caption
            kind = "transcript"
        settings = {**params, "pages": integer(o, "pages", 1, 1, 10), "include_replies": boolean(o, "include_replies")}
        items, meta = analysis.analyse(raw, {**o, "country": None}, source=source, observed_at=observed, scope=scope(op, settings), kind=kind, api_data=True)
        meta.update(extra)
        meta.update(collection_parameters=settings, pagination=api.page_reports, endpoint_calls=dict(api.calls),
                    query_region=o.get("country"), metric_geography="not geographically disaggregated")
        return items, meta, [
            "Search result totals are approximate and capped, not a global hashtag inventory or query volume.",
            "Public video counters are whole-video lifetime measurements. API snapshots need refresh/deletion under the applicable terms.",
            "Shorts views include starts/replays under the current counting definition; short duration does not identify a Short.",
        ]

    @staticmethod
    def _search_filters(o, p, resource):
        if "country" in o:
            p["regionCode"] = code(o, "country").upper()
        if "language" in o:
            p["relevanceLanguage"] = code(o, "language", pattern=r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?")
        if "channel_id" in o:
            p["channelId"] = channel_id(o)
        for option, param in (("published_after", "publishedAfter"), ("published_before", "publishedBefore")):
            if option in o:
                p[param] = analysis.timestamp(text(o, option)).isoformat()
        if p.get("publishedAfter") and p.get("publishedBefore") and p["publishedAfter"] >= p["publishedBefore"]:
            raise ConfigurationError("published_after must precede published_before.")
        if resource == "video":
            for option, param, allowed in (
                ("duration", "videoDuration", ("any", "short", "medium", "long")),
                ("caption", "videoCaption", ("any", "closedCaption", "none")),
                ("event_type", "eventType", ("completed", "live", "upcoming")),
                ("definition", "videoDefinition", ("any", "high", "standard")),
            ):
                if option in o:
                    p[param] = choice(o, option, allowed[0], allowed)
            if "category_id" in o:
                p["videoCategoryId"] = code(o, "category_id", pattern=r"\d{1,5}")
        if "safe_search" in o:
            p["safeSearch"] = choice(o, "safe_search", "moderate", ("none", "moderate", "strict"))

    @staticmethod
    def _comment(row, vid):
        s = obj(row.get("snippet"))
        # No commenter names, avatars or profile IDs are retained.
        return {"id": opaque_id(row.get("id"), "comment_id"), "video_id": vid,
                "text": s.get("textOriginal", s.get("textDisplay", "")),
                "likeCount": s.get("likeCount"), "parent_id": s.get("parentId"), "published_at": s.get("publishedAt")}

    def _analytics(self, request, http, observed):
        o, op = request.options, request.operation
        if request.keywords:
            raise ConfigurationError("Analytics reports take channel_id, dates and optional video_id, not seed queries.")
        cid = channel_id(o)
        start, end = iso_date(o, "start_date"), iso_date(o, "end_date")
        if start > end:
            raise ConfigurationError("start_date must not be after end_date.")
        _, headers = _auth(o, oauth=True)
        detailed = op in {"analytics-search", "analytics-hashtags"}
        dimension = "insightTrafficSourceDetail" if detailed else "video" if op == "analytics-videos" else "insightTrafficSourceType"
        metrics = "views,engagedViews,estimatedMinutesWatched"
        if op == "analytics-videos":
            metrics += ",averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost"
        p = {"ids": f"channel=={cid}", "startDate": start, "endDate": end,
             "dimensions": dimension, "metrics": metrics, "sort": "-views",
             "maxResults": integer(o, "report_limit", 25 if detailed else 50, 1, 25 if detailed else 200)}
        filters = []
        if detailed:
            filters.append("insightTrafficSourceType==" + ("YT_SEARCH" if op == "analytics-search" else "HASHTAGS"))
        if "country" in o:
            filters.append("country==" + code(o, "country").upper())
        if "video_id" in o:
            filters.append("video==" + analysis.video_id(o["video_id"]))
        if filters:
            p["filters"] = ";".join(filters)
        data = provider_error(http.json("GET", ANALYTICS_URL, params=p, headers=headers))
        columns = analysis.records(data.get("columnHeaders"), 50)
        names = [c.get("name") for c in columns]
        if len(set(names)) != len(names) or dimension not in names or not set(metrics.split(",")).issubset(names):
            raise OnlineSourceError("Analytics columns differ from the requested report.")
        rows = data.get("rows", [])
        if not isinstance(rows, list) or len(rows) > p["maxResults"]:
            raise OnlineSourceError("Unexpected Analytics rows.")
        items, video_rows = [], []
        for values in rows:
            if not isinstance(values, list) or len(values) != len(names):
                raise OnlineSourceError("Analytics rows do not match the column headers.")
            row = dict(zip(names, values))
            word = analysis.string(row[dimension], 1000).strip()
            if not word:
                raise OnlineSourceError("An Analytics dimension is missing.")
            ev = []
            for name in metrics.split(","):
                value = row[name]
                if name in {"estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage"}:
                    from .common import number
                    value = number(value)
                    if value is not None and value < 0:
                        raise OnlineSourceError("Negative Analytics measurement.")
                else:
                    value = analysis.count(value)
                unit = "minutes" if name == "estimatedMinutesWatched" else "seconds" if name == "averageViewDuration" else "percent" if name == "averageViewPercentage" else "count"
                metric = "attributed_views" if name == "views" and detailed else "analytics_" + name
                ev.append(KeywordEvidence("YouTube Analytics API", metric, value, unit, observed, o.get("country"),
                                          "Authorised channel report for the stated date interval; not market-wide search volume."))
            category = "hashtag" if op == "analytics-hashtags" else "keyword" if op == "analytics-search" else "traffic-source"
            if category == "hashtag":
                word = analysis.hashtag(word)
            if op == "analytics-videos":
                # Do not treat video IDs as keyword phrases or allocate performance to every title word.
                video_rows.append({"video_id": analysis.video_id(word), "measurements": {e.metric: e.value for e in ev}, "units": {e.metric: e.unit for e in ev}})
            else:
                items.append(KeywordCandidate(word, "youtube-" + category + "-attribution", None, tuple(ev),
                    {"kind": category, "source": "YouTube Analytics API", "scope": scope(op, p), "api_data": True,
                     "window": start + "/" + end, "channel_id": cid,
                     "traffic_source": "YT_SEARCH" if op == "analytics-search" else "HASHTAGS" if detailed else word}))
        return items, {"source": "YouTube Analytics API", "scope": scope(op, p), "api_data": True,
                       "channel_id": cid, "collection_parameters": p, "video_reports": video_rows,
                       "report_rows": len(rows), "report_limit_reached": len(rows) == p["maxResults"],
                       "api_authorisation_recheck_by": (analysis.timestamp(observed) + timedelta(days=30)).isoformat(),
                       "completeness": "top report rows; privacy thresholds/report availability may suppress terms"}, [
                       "Search referrals and hashtag referrals are actual channel traffic attribution, not counts of all searches.",
                       "Analytics HASHTAGS is a traffic-source report, not attribution to every hashtag included on a video."]

    def _local(self, request, context, now):
        o, op = request.options, request.operation
        if request.keywords:
            raise ConfigurationError("Local analysis takes reference files/text, not seed keywords.")
        if op == "compare":
            _input_count(request, 2)
            before, after = (imports.decode_json(imports.read(p, o)) for p in request.inputs)
            if not isinstance(before, dict) or not isinstance(after, dict):
                raise InputError("Expected saved result objects for comparison.")
            return analysis.compare(before, after, o), {"comparison": "compatible exact source/scope/metric observations only"}, []
        if op == "trends-import":
            if text(o, "search_property") != "youtube":
                raise ConfigurationError("Confirm search_property=youtube; a CSV cannot prove its search-property filter.")
            from ..builtin.google_trends import GoogleTrendsPlugin
            operation = choice(o, "trends_operation", "import-interest", ("import-interest", "import-related"))
            iso_date(o, "observed_at")
            result = GoogleTrendsPlugin().run(replace(request, operation=operation), context)
            return [replace(c, score=None, metadata={**c.metadata, "search_property": "youtube"}) for c in result.keywords], {
                **result.metadata, "observed_at": o["observed_at"], "search_property": "youtube",
                "scope": text(o, "scope"), "source": "Google Trends YouTube Search export"}, list(result.notes)
        if op == "extract":
            if not request.inputs and "text" not in o:
                raise InputError("Supply reference --input text or --option text=...")
            raw = []
            if "text" in o:
                value = analysis.string(o["text"])
                raw.append({"id": "inline", "description": value})
            for i, path in enumerate(request.inputs):
                raw.append({"id": str(path.resolve()) + f":{i}", "description": imports.read(path, o)})
            items, meta = analysis.analyse(raw, o, source="Local YouTube reference text (not a platform observation)",
                observed_at=now, scope=scope(op, {"files": [str(p) for p in request.inputs], "inline": "text" in o}), kind="reference")
            return items, meta, ["Literal hashtags in reference text are candidate proposals, not evidence of use or popularity on YouTube."]
        _input_count(request)
        source, sc, observed = text(o, "source"), text(o, "scope"), text(o, "observed_at")
        analysis.timestamp(observed)
        api_data = boolean(o, "api_data")
        p = request.inputs[0]
        if op in {"import-observations", "import-html"}:
            rows = imports.html_observations(imports.read(p, o), o) if op == "import-html" else imports.load(p, o, "observations")
            items = analysis.observations(rows, source=source, scope=sc, observed_at=observed, country=o.get("country"), api_data=api_data)
            return items, {"source": source, "scope": sc, "observed_at": observed, "api_data": api_data}, []
        if op == "import-transcript":
            rows = imports.transcript(imports.read(p, o), str(p.resolve()), text(o, "transcript_format", p.suffix.lower().lstrip(".")))
            kind = "transcript"
        else:
            kind = "comments" if op == "import-comments" else "videos"
            rows = imports.load(p, o, kind)
        api_data = api_data or any(str(r.get("kind", "")).startswith("youtube#") for r in rows)
        items, meta = analysis.analyse(rows, o, source=source, observed_at=observed, scope=sc, kind=kind, api_data=api_data)
        if kind == "transcript":
            meta["transcript_cues"] = [{k: v for k, v in cue.items() if k != "description"} for cue in rows]
        return items, meta, []
