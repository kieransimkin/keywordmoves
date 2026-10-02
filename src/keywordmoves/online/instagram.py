"""Instagram discovery: Meta, Keyword Tool, Apify and explicit local observations.

Network calls are bounded and opt-in by operation; no login automation, private
mobile APIs, cookie import, CAPTCHA bypass, posting or automatic provider fallback.
"""
from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)
from .common import (
    HTTP,
    OnlineSourceError,
    array,
    boolean,
    choice,
    code,
    integer,
    obj,
    secret,
    seeds,
    text,
)
from .instagram_analysis import (
    SAMPLE_NOTE,
    analyse,
    compare,
    count,
    deduplicate,
    hashtag,
    media_row,
    stats_candidates,
    timestamp,
)
from .instagram_imports import (
    json_input,
    public_hashtag_html,
    read_input,
    read_records,
    records,
    saved_html,
)

MEDIA_FIELDS = "id,caption,media_type,permalink,like_count,comments_count"
OWN_FIELDS = MEDIA_FIELDS + ",timestamp,media_product_type"
TRANSPORT_OPTIONS = {"max_requests", "max_response_bytes", "timeout", "min_interval"}
ANALYSIS_OPTIONS = {"limit", "include_keywords", "include_media", "related_limit", "sort_by"}
SORT_METRICS = ("discovery", "reported_post_count", "estimated_search_volume",
                "sampled_post_count", "sample_likes_plus_comments_median")
IMPORT_OPTIONS = {"source", "scope", "observed_at", "max_input_bytes", "max_records"}
GRAPH_OPTIONS = {"access_token", "user_id", "graph_version", "login", "pages", "page_size", "max_media"}
OPERATIONS = {
    "extract": {"text", "max_input_bytes"},
    "hashtag": GRAPH_OPTIONS | {"edge"},
    "quota": GRAPH_OPTIONS,
    "account": GRAPH_OPTIONS | {"username", "media_edge", "include_profile"},
    "media-insights": GRAPH_OPTIONS | {"media_id", "metrics"},
    "comments": GRAPH_OPTIONS | {"media_id"},
    "suggestions": {"api_key", "country", "language", "currency", "metrics", "sandbox"},
    "metrics": {"api_key", "country", "language", "currency", "sandbox"},
    "import-media": IMPORT_OPTIONS,
    "import-hashtags": IMPORT_OPTIONS | {"hashtag_column", "post_count_column"},
    "import-html": IMPORT_OPTIONS | {"selector", "name_selector", "count_selector"},
    "public-page": {"allow_web"},
    "apify-start": {"apify_token", "actor", "allow_paid", "max_charge_usd", "results_per_seed",
                    "results_type", "actor_timeout", "actor_build", "include_posts"},
    "apify-fetch": {"apify_token", "run_id", "dataset_id", "dataset_kind", "scope", "observed_at",
                    "pages", "page_size", "max_records"},
    "compare": {"max_input_bytes", "metric"},
}
ACTORS = {
    "hashtag-stats": "apify~instagram-hashtag-analytics-scraper",
    "hashtag-posts": "apify~instagram-hashtag-scraper",
    "keyword-posts": "apify~instagram-hashtag-scraper",
    "hashtag-search": "apify~instagram-search-scraper",
    "reels-search": "apify~instagram-search-scraper",
}


def _id(options: dict[str, Any], name: str) -> str:
    return code(options, name, pattern=r"[0-9]{1,50}")


def _cursor(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_+/=\-]{1,2000}", value):
        raise OnlineSourceError("Invalid or unsafe Instagram paging cursor.")
    return value


def _merge(items: list[KeywordCandidate]) -> list[KeywordCandidate]:
    grouped: dict[str, KeywordCandidate] = {}
    for item in items:
        old = grouped.get(item.phrase)
        if old is None:
            grouped[item.phrase] = item
        else:
            meta = {**item.metadata, **old.metadata}
            if item.metadata.get("count_is_approximate") or old.metadata.get("count_is_approximate"):
                meta["count_is_approximate"] = True
            meta["additional_contexts"] = [*old.metadata.get("additional_contexts", []),
                                           {k: v for k, v in item.metadata.items() if k in {"seed", "provider_group", "verification"}}]
            grouped[item.phrase] = replace(old, evidence=tuple(dict.fromkeys((*old.evidence, *item.evidence))),
                                          metadata=meta)
    return list(grouped.values())


def _ordered(items: list[KeywordCandidate], options: dict[str, Any]) -> list[KeywordCandidate]:
    metric = choice(options, "sort_by", "discovery", SORT_METRICS)
    if metric == "discovery":
        return items

    def key(item: KeywordCandidate) -> tuple[Any, ...]:
        values = {e.value for e in item.evidence if e.metric == metric
                  and isinstance(e.value, (int, float)) and not isinstance(e.value, bool)}
        # Conflicting observations must not be resolved by choosing a convenient maximum.
        value = values.pop() if len(values) == 1 else None
        return (value is None, -value if value is not None else 0, item.phrase)

    return sorted(items, key=key)


class Graph:
    def __init__(self, http: HTTP, options: dict[str, Any]) -> None:
        self.http, self.options = http, options
        self.login = choice(options, "login", "facebook", ("facebook", "instagram"))
        self.version = code({"graph_version": options.get("graph_version", os.getenv("INSTAGRAM_GRAPH_VERSION"))},
                            "graph_version", pattern=r"v[0-9]{1,3}\.0")
        self.user = _id({"user_id": options.get("user_id", os.getenv("INSTAGRAM_USER_ID"))}, "user_id")
        self.token = secret(options, "access_token", "INSTAGRAM_ACCESS_TOKEN")
        self.base = "https://graph." + ("facebook" if self.login == "facebook" else "instagram") + ".com/" + self.version
        self.pages = integer(options, "pages", 1, 1, 10)
        self.page_size = integer(options, "page_size", 50, 1, 50)
        self.maximum = integer(options, "max_media", 500, 1, 2000)

    def facebook_only(self) -> None:
        if self.login != "facebook":
            raise ConfigurationError("Hashtag search, quota and business discovery require login=facebook; Instagram Login cannot substitute.")

    def get(self, path: str, params: dict[str, Any], *, optional_metric: bool = False) -> dict[str, Any]:
        status, raw = self.http.request("GET", self.base + "/" + path,
                                        params=params, headers={"Authorization": "Bearer " + self.token},
                                        allowed_statuses=(400,))
        data = obj(json_input(raw))
        if data.get("error"):
            err = obj(data["error"])
            if optional_metric and err.get("code") == 100:
                return {"data": [], "_unavailable": "unsupported_metric_or_request"}
            raise OnlineSourceError("Meta rejected the request. Check token, permissions, API version, "
                                    "account and quota. This does not prove a hashtag is banned.")
        if status == 400:
            raise OnlineSourceError("Meta rejected the request without a recognised error response.")
        return data

    def collect(self, path: str, params: dict[str, Any], *, business: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        after = None
        more = False
        profile = {}
        for _ in range(self.pages):
            page_params = {**params, "limit": min(self.page_size, self.maximum - len(rows))}
            if after:
                page_params["after"] = after
            if business:
                paging = f".after({after})" if after else ""
                profile_fields = "username,followers_count,media_count"
                if boolean(self.options, "include_profile"):
                    profile_fields += ",name,biography"
                page_params = {"fields": f"business_discovery.username({business}){{{profile_fields},"
                               f"media.limit({page_params['limit']}){paging}{{{OWN_FIELDS}}}}}"}
            payload = self.get(path, page_params)
            if business:
                payload = obj(payload.get("business_discovery"))
                profile = {"username": payload.get("username"), "followers_count": count(payload.get("followers_count")),
                           "media_count": count(payload.get("media_count"))}
                if boolean(self.options, "include_profile"):
                    profile.update(name=payload.get("name"), biography=payload.get("biography"))
                payload = obj(payload.get("media"))
            page = array(payload.get("data"))
            if any(not isinstance(row, dict) for row in page):
                raise OnlineSourceError("Invalid Instagram media page.")
            remaining = self.maximum - len(rows)
            rows.extend(page[:remaining])
            paging = obj(payload.get("paging", {}))
            more = bool(paging.get("next")) or len(page) > remaining
            if not more or len(rows) >= self.maximum:
                break
            cursors = obj(paging.get("cursors", {}))
            if not cursors.get("after"):
                break  # Never follow a token-bearing or arbitrary 'next' URL.
            after = _cursor(cursors["after"])
            if after in seen:
                raise OnlineSourceError("Instagram repeated a paging cursor; refusing an infinite loop.")
            seen.add(after)
        return rows, {"more_available": more, "profile": profile}


class InstagramPlugin:
    descriptor = PluginDescriptor(
        "instagram", "Instagram hashtags, keyword discovery, sampled engagement and owned-media insights.",
        ("discover", "analyse", "hashtags", "official-api", "third-party", "import"), tuple(OPERATIONS),
    )

    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        op, o = request.operation, dict(request.options)
        if op not in OPERATIONS:
            raise ConfigurationError("Instagram operations: " + ", ".join(OPERATIONS))
        if set(o) - OPERATIONS[op] - TRANSPORT_OPTIONS - ANALYSIS_OPTIONS:
            raise ConfigurationError("Unsupported option for this Instagram operation; see docs/instagram.md. No LLM is selected implicitly.")
        limit = integer(o, "limit", 100, 1, 2000)
        sort_by = choice(o, "sort_by", "discovery", SORT_METRICS)
        for flag in ("include_keywords", "include_media"):
            boolean(o, flag)
        integer(o, "related_limit", 20, 0, 100)
        offline = op.startswith("import-") or op in {"extract", "compare"}
        if request.inputs and not offline:
            raise ConfigurationError("This Instagram network operation does not accept --input files.")
        if request.keywords and op not in {"hashtag", "suggestions", "metrics", "apify-start", "public-page"}:
            raise ConfigurationError("This operation uses options or files, not --keyword seeds.")
        observed = datetime.now(timezone.utc).isoformat()
        if op.startswith("import-") or (op == "apify-fetch" and "dataset_id" in o):
            observed = timestamp(o.get("observed_at")).isoformat()
        notes = [SAMPLE_NOTE, "No automatic provider fallback or hashtag recursion. Failures are not proof of a banned hashtag."]
        metadata: dict[str, Any] = {"platform": "Instagram", "observed_at": observed,
                                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                    "live_query_performed": not offline, "request_count": 0,
                                    "comparison_scope": {"operation": op},
                                    "completeness": "bounded, source-dependent; not exhaustive"}
        if offline:
            items, extra = self._offline(request, o, observed)
        else:
            hosts = ("graph.facebook.com", "graph.instagram.com", "api.apify.com", "www.instagram.com")
            if op in {"suggestions", "metrics"}:
                return self._keywordtool(request, o)
            with HTTP({"max_requests": 20, **o}, hosts, self._transport) as http:
                if op in {"hashtag", "quota", "account", "media-insights", "comments"}:
                    items, extra = self._meta(request, o, observed, Graph(http, o))
                elif op.startswith("apify-"):
                    items, extra = self._apify(request, o, observed, http)
                else:
                    if not boolean(o, "allow_web"):
                        raise ConfigurationError("public-page needs allow_web=true after reviewing access rules.")
                    tag = hashtag(seeds(request)[0])
                    url = "https://www.instagram.com/explore/tags/" + quote(tag[1:], safe="") + "/"
                    from .websites import check_robots
                    check_robots(http, url)
                    _, body = http.request("GET", url)
                    items = stats_candidates(public_hashtag_html(body, tag), "Instagram public HTML", observed)
                    extra = {"access": "experimental-public-html", "comparison_scope": {"source": "instagram-public-html", "tag": tag}}
                metadata["request_count"] = http.requests
        metadata.update(extra)
        items = _ordered(_merge(items), o)
        metadata["sort_by"] = sort_by
        metadata.update(rows_received=len(items), output_truncated=len(items) > limit)
        if not items:
            notes.append("No candidates in this response; this is not a statement of zero Instagram demand.")
        return PluginResult("instagram", op, tuple(items[:limit]), tuple(notes), metadata)

    def _analyse(self, rows: list[dict[str, Any]], o: dict[str, Any], observed: str,
                 source: str, *, known: dict[str, str] | None = None,
                 kind: str = "media") -> tuple[list[KeywordCandidate], dict[str, Any]]:
        media = [media_row(row, i, provider=source) for i, row in enumerate(rows)]
        items, meta = analyse(media, source=source, observed=observed, known=known,
                              include_keywords=boolean(o, "include_keywords"),
                              related_limit=integer(o, "related_limit", 20, 0, 100), sample_kind=kind)
        if boolean(o, "include_media"):
            unique, _, _ = deduplicate(media)
            meta["media"] = [{"key": m.key, "caption": m.caption, "hashtags": sorted(m.tags),
                               "metrics": m.metrics, "timestamp": m.published.isoformat() if m.published else None,
                               "media_type": m.media_type, "url": m.url, "contexts": sorted(m.contexts)} for m in unique]
        return items, meta

    def _offline(self, request: PluginRequest, o: dict[str, Any], observed: str) -> tuple[list[KeywordCandidate], dict[str, Any]]:
        op = request.operation
        maximum = integer(o, "max_input_bytes", 5_000_000, 1024, 20_000_000)
        if op == "compare":
            if len(request.inputs) != 2:
                raise InputError("Compare needs exactly two --input files, oldest first.")
            before, after = [obj(json_input(read_input(p, maximum))) for p in request.inputs]
            metric = choice(o, "metric", "reported_post_count", ("reported_post_count", "estimated_search_volume", "sampled_post_count"))
            return compare(before, after, metric), {"access": "local-comparison", "metric": metric,
                                                    "comparison_scope": after["metadata"]["comparison_scope"],
                                                    "observed_at": after["metadata"]["observed_at"]}
        if op == "extract":
            texts = []
            if "text" in o:
                if not isinstance(o["text"], str) or not o["text"].strip():
                    raise InputError("Reference text must be non-empty text.")
                texts.append(o["text"])
            for path in request.inputs:
                texts.append(read_input(path, maximum))
            if not texts or sum(len(t.encode("utf-8")) for t in texts) > maximum:
                raise InputError("Supply reference text/files within max_input_bytes.")
            items, meta = self._analyse([{"id": str(i), "caption": t} for i, t in enumerate(texts)],
                                        o, observed, "reference-text", kind="reference")
            return items, {**meta, "access": "local-reference", "comparison_scope": {"source": "reference-text"}}
        source, scope = text(o, "source"), text(o, "scope")
        if op == "import-html":
            if len(request.inputs) != 1:
                raise InputError("import-html expects one saved HTML file.")
            rows = saved_html(read_input(request.inputs[0], maximum), o)
        else:
            rows = read_records(request.inputs, o, "media" if op == "import-media" else "hashtags")
        if op == "import-media":
            items, meta = self._analyse(rows, o, observed, source)
        else:
            items, meta = stats_candidates(rows, source, observed), {}
        return items, {**meta, "access": "local-export", "comparison_scope": {"source": source, "scope": scope, "kind": op}}

    def _meta(self, request: PluginRequest, o: dict[str, Any], observed: str, graph: Graph) -> tuple[list[KeywordCandidate], dict[str, Any]]:
        op = request.operation
        scope = {"source": "Meta", "operation": op, "user_id": graph.user, "version": graph.version, "login": graph.login,
                 "pages": graph.pages, "page_size": graph.page_size, "max_media": graph.maximum}
        base = {"access": "meta-api", "comparison_scope": scope, "graph_version": graph.version}
        if op == "quota":
            graph.facebook_only()
            rows, page = graph.collect(graph.user + "/recently_searched_hashtags", {"fields": "id,name"})
            names = [hashtag(row["name"]) for row in rows if row.get("name")]
            return [], {**base, "queried_hashtag_ids": list(dict.fromkeys(str(row["id"]) for row in rows)),
                        "queried_hashtags": names, "rolling_limit": 30, "rolling_window_days": 7,
                        "quota_list_truncated": page["more_available"],
                        "quota_note": "Shared across apps for this account. No guaranteed remaining quota is inferred from a partial list."}
        if op == "hashtag":
            graph.facebook_only()
            tags = list(dict.fromkeys(hashtag(s) for s in seeds(request, 30)))
            edge = choice(o, "edge", "both", ("top", "recent", "both"))
            edges = ("top_media", "recent_media") if edge == "both" else (edge + "_media",)
            if len(tags) * (1 + len(edges)) > graph.http.maximum:
                raise ConfigurationError("Seeds need more requests than max_requests even for first pages; split the batch.")
            rows, known, info = [], {}, []
            for tag in tags:
                data = graph.get("ig_hashtag_search", {"user_id": graph.user, "q": tag[1:]})
                found = array(data.get("data"))
                if not found:
                    known[tag] = "unresolved-by-api"
                    continue
                if len(found) != 1:
                    raise OnlineSourceError("Expected one ID for an exact Instagram hashtag lookup.")
                identifier = _id({"id": obj(found[0]).get("id")}, "id")
                known[tag] = "meta-resolved"
                for route in edges:
                    page, page_meta = graph.collect(identifier + "/" + route,
                                                    {"user_id": graph.user, "fields": MEDIA_FIELDS})
                    rows.extend({**row, "_matched_hashtag": tag, "_context": tag + ":" + route} for row in page)
                    info.append({"hashtag": tag, "edge": route, "rows": len(page), "more_available": page_meta["more_available"]})
            items, meta = self._analyse(rows, o, observed, "Meta hashtag media", known=known)
            scope.update(tags=tags, edge=edge, pages=graph.pages, page_size=graph.page_size, max_media=graph.maximum)
            return items, {**base, **meta, "collections": info,
                           "limitations": "Exact hashtag lookup; 30 unique tags per rolling 7 days. Recent media is a bounded 24h surface. "
                                          "Top is ranked, not random. No total hashtag post count, username, or timestamp is requested on hashtag edges."}
        if op == "account":
            edge = choice(o, "media_edge", "media", ("media", "stories", "tags"))
            include_profile = boolean(o, "include_profile")
            scope["include_profile"] = include_profile
            if "username" in o:
                graph.facebook_only()
                username = code(o, "username", pattern=r"[A-Za-z0-9_.]{1,30}")
                if edge != "media":
                    raise ConfigurationError("Business Discovery only supports media in this plugin.")
                rows, page = graph.collect(graph.user, {}, business=username)
                profile = page["profile"]
            else:
                if edge == "tags":
                    graph.facebook_only()
                fields = "id,username,followers_count,media_count" + (",name,biography" if include_profile else "")
                profile_raw = graph.get(graph.user, {"fields": fields})
                profile = {"username": profile_raw.get("username"), "followers_count": count(profile_raw.get("followers_count")),
                           "media_count": count(profile_raw.get("media_count"))}
                if include_profile:
                    profile.update(name=profile_raw.get("name"), biography=profile_raw.get("biography"))
                username = profile.get("username")
                rows, page = graph.collect(graph.user + "/" + edge, {"fields": OWN_FIELDS})
            # Do not use OUR follower count to normalise engagement on someone else's tagged media.
            if edge != "tags":
                rows = [{**row, "followers_count": profile.get("followers_count")} for row in rows]
            scope.update(username=username, edge=edge, pages=graph.pages, page_size=graph.page_size, max_media=graph.maximum)
            items, meta = self._analyse(rows, o, observed, "Meta account media")
            if include_profile:
                parts = [profile.pop(k, None) for k in ("name", "biography")]
                if any(part is not None and not isinstance(part, str) for part in parts):
                    raise OnlineSourceError("Invalid professional profile text fields.")
                profile_items, profile_meta = self._analyse(
                    [{"caption": "\n".join(part for part in parts if part)}], o, observed,
                    "Meta professional profile text", kind="profile")
                items.extend(profile_items)
                meta["profile_text_analysis"] = profile_meta
            return items, {**base, **meta, "profile": profile, "more_available": page["more_available"]}
        identifier = _id(o, "media_id")
        scope["media_id"] = identifier
        if op == "comments":
            rows, page = graph.collect(identifier + "/comments", {"fields": "id,text,timestamp"})
            items, meta = self._analyse(rows, o, observed, "Meta owned-media comments", kind="comments")
            return items, {**base, **meta, "more_available": page["more_available"]}
        metrics = text(o, "metrics", "views,reach,saved,shares").split(",")
        metrics = list(dict.fromkeys(m.strip() for m in metrics))
        scope["metrics"] = metrics
        allowed = {"views", "reach", "saved", "shares", "likes", "comments", "total_interactions", "replies"}
        if not metrics or not set(metrics) <= allowed:
            raise ConfigurationError("Unsupported media insight metric; see docs/instagram.md.")
        if len(metrics) + 1 > graph.http.maximum:
            raise ConfigurationError("media-insights needs one media request plus one request per metric.")
        row = graph.get(identifier, {"fields": OWN_FIELDS})
        results = {}
        for metric in metrics:
            payload = graph.get(identifier + "/insights", {"metric": metric}, optional_metric=True)
            data = array(payload.get("data"))
            entry = next((obj(x) for x in data if obj(x).get("name") == metric), None)
            raw = None
            if entry:
                if "total_value" in entry:
                    raw = obj(entry["total_value"]).get("value")
                elif isinstance(entry.get("values"), list) and len(entry["values"]) == 1:
                    raw = obj(entry["values"][0]).get("value")
            value = count(raw) if not isinstance(raw, (dict, list)) else None
            results[metric] = {"value": value, "period": entry.get("period") if entry else None,
                               "availability": "observed" if value is not None else "unavailable_for_this_request"}
            # Preserve public counts if the equivalent insight is missing.
            if value is not None:
                row[{"comments": "comments_count", "likes": "like_count"}.get(metric, metric)] = value
        items, meta = self._analyse([row], o, observed, "Meta owned-media insights")
        return items, {**base, **meta, "media_insights": results,
                       "insight_note": "Metrics describe the whole media item, not incremental or attributable performance of individual hashtags."}

    def _keywordtool(self, request: PluginRequest, o: dict[str, Any]) -> PluginResult:
        from .commercial import KeywordToolPlugin
        provider_options = {k: v for k, v in o.items() if k in OPERATIONS[request.operation] | TRANSPORT_OPTIONS | {"limit"}}
        provider_options.update(platform="instagram")
        if request.operation == "suggestions":
            provider_options["suggestion_type"] = "hashtags"
        result = KeywordToolPlugin(transport=self._transport).run(
            PluginRequest(request.operation, keywords=request.keywords, options=provider_options), ExecutionContext(None))
        candidates = [replace(item, phrase=hashtag(item.phrase),
                              relationship="instagram-hashtag-suggestion" if request.operation == "suggestions" else "instagram-hashtag-metric",
                              metadata={**item.metadata, "verification": "third-party-suggestion"
                                        if request.operation == "suggestions" else "unverified-metric-query"},
                              evidence=tuple(replace(e, notes=("Suggestion position, not popularity or demand."
                                    if e.metric == "returned_order" else
                                    "Third-party Instagram search estimate, not hashtag post count. "
                                    "CPC/competition are Google Ads proxy metrics, not Instagram organic difficulty."))
                                             for e in item.evidence)) for item in result.keywords]
        scope = {"provider": "Keyword Tool", "operation": request.operation, "country": o.get("country"),
                 "language": o.get("language", "en"), "keywords": list(request.keywords), "sandbox": boolean(o, "sandbox")}
        return replace(result, plugin="instagram", keywords=tuple(_ordered(_merge(candidates), o)), metadata={**result.metadata,
                       "sort_by": choice(o, "sort_by", "discovery", SORT_METRICS),
                       "comparison_scope": scope, "observed_at": result.metadata["retrieved_at"], "platform": "Instagram",
                       "live_query_performed": True})

    def _apify(self, request: PluginRequest, o: dict[str, Any], observed: str, http: HTTP) -> tuple[list[KeywordCandidate], dict[str, Any]]:
        headers = {"Authorization": "Bearer " + secret(o, "apify_token", "APIFY_TOKEN")}
        if request.operation == "apify-start":
            if not boolean(o, "allow_paid"):
                raise ConfigurationError("Starting an Apify job needs allow_paid=true; the job may incur charges.")
            actor = choice(o, "actor", "hashtag-stats", tuple(ACTORS))
            terms = seeds(request, 10)
            n = integer(o, "results_per_seed", 50, 1, 250)
            if actor == "hashtag-stats":
                body = {"hashtags": [hashtag(t)[1:] for t in terms], "includeLatestPosts": boolean(o, "include_posts"),
                        "includeTopPosts": boolean(o, "include_posts")}
            elif actor in {"hashtag-posts", "keyword-posts"}:
                body = {"hashtags": [hashtag(t)[1:] for t in terms] if actor == "hashtag-posts" else terms,
                        "keywordSearch": actor == "keyword-posts", "resultsLimit": n,
                        "resultsType": choice(o, "results_type", "posts", ("posts", "reels"))}
            else:
                if any("," in t for t in terms):
                    raise ConfigurationError("Apify search terms must not contain commas (the actor uses a comma-separated list).")
                body = {"search": ",".join(terms), "searchType": "hashtag" if actor == "hashtag-search" else "popular",
                        "searchLimit": n}
            charge = count(o.get("max_charge_usd", 1))
            if charge is None or not 0 < charge <= 100:
                raise ConfigurationError("max_charge_usd must be a finite positive amount at most 100 USD.")
            params = {"maxTotalChargeUsd": charge, "timeout": integer(o, "actor_timeout", 300, 1, 3600),
                      "waitForFinish": 0, "restartOnError": "false"}
            if "actor_build" in o:
                params["build"] = code(o, "actor_build", pattern=r"[A-Za-z0-9_.-]{1,80}")
            raw = obj(http.json("POST", f"https://api.apify.com/v2/actors/{ACTORS[actor]}/runs",
                               json=body, params=params, headers=headers))
            run = obj(raw.get("data"))
            identifier = code({"run_id": run.get("id")}, "run_id", pattern=r"[A-Za-z0-9]{1,80}")
            return [], {"access": "third-party-job", "status": "submitted", "provider_status": run.get("status"),
                        "run_id": identifier, "actor": ACTORS[actor], "max_charge_usd": charge,
                        "comparison_scope": {"actor": actor, "terms": terms},
                        "next_step": "Use apify-fetch with run_id and dataset_kind. Submission is not completed collection."}
        scope = text(o, "scope")
        kind = choice(o, "dataset_kind", "media", ("media", "hashtags"))
        if ("run_id" in o) == ("dataset_id" in o):
            raise ConfigurationError("Supply exactly one of run_id or dataset_id.")
        meta: dict[str, Any] = {"access": "third-party-dataset", "comparison_scope": {"provider": "Apify", "scope": scope, "kind": kind}}
        if "run_id" in o:
            run_id = code(o, "run_id", pattern=r"[A-Za-z0-9]{1,80}")
            run = obj(obj(http.json("GET", "https://api.apify.com/v2/actor-runs/" + run_id, headers=headers)).get("data"))
            status = run.get("status")
            meta.update(run_id=run_id, status=status)
            if status in {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}:
                return [], {**meta, "collection_complete": False, "next_step": "Fetch this run again later; no new job was submitted."}
            if status != "SUCCEEDED":
                raise OnlineSourceError("The Apify job did not succeed; no empty-success or zero-demand result is returned.")
            dataset = run.get("defaultDatasetId")
            observed = timestamp(run.get("finishedAt")).isoformat()
            meta["comparison_scope"]["actor_id"] = run.get("actId")
        else:
            dataset = o["dataset_id"]
        dataset = code({"dataset_id": dataset}, "dataset_id", pattern=r"[A-Za-z0-9]{1,80}")
        size = integer(o, "page_size", 100, 1, 1000)
        pages = integer(o, "pages", 1, 1, 10)
        maximum = integer(o, "max_records", 2000, 1, 10000)
        meta["comparison_scope"].update(pages=pages, page_size=size, max_records=maximum)
        rows = []
        more = False
        for _ in range(pages):
            requested = min(size, maximum - len(rows))
            page = array(http.json("GET", f"https://api.apify.com/v2/datasets/{dataset}/items", headers=headers,
                                   params={"format": "json", "skipHidden": "true", "skipEmpty": "false",
                                           "offset": len(rows), "limit": requested}))
            if len(page) > requested:
                raise OnlineSourceError("Apify returned more records than the requested page limit.")
            rows.extend(records(page, maximum, kind))
            more = len(page) == requested
            if not more or len(rows) >= maximum:
                break
        source = "apify:" + (meta["comparison_scope"].get("actor_id") or "imported-dataset")
        if kind == "hashtags":
            items = stats_candidates(rows, source, observed)
            embedded = []
            for row in rows:
                for key in ("topPosts", "latestPosts"):
                    posts = row.get(key, [])
                    if not isinstance(posts, list) or any(not isinstance(p, dict) for p in posts):
                        raise InputError("Nested Apify media must be an array of objects.")
                    if len(embedded) + len(posts) > maximum:
                        raise InputError("Nested Apify media exceeds max_records.")
                    embedded.extend({**post, "_context": hashtag(row.get("name")) + ":" + key}
                                    for post in posts)
            extra_items, extra = self._analyse(embedded, o, observed, source)
            items.extend(extra_items)
        else:
            items, extra = self._analyse(rows, o, observed, source)
        return items, {**meta, **extra, "observed_at": observed, "dataset_id": dataset,
                       "dataset_rows": len(rows), "more_may_be_available": more,
                       "collection_complete": not more and "run_id" in o}
