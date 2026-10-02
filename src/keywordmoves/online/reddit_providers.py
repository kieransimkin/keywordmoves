"""Explicit third-party Reddit routes. Never fall back after native access is denied."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, PluginResult
from . import reddit_analysis as analysis
from .common import (
    HTTP,
    OnlineSourceError,
    array,
    boolean,
    code,
    integer,
    obj,
    provider_error,
    secret,
    seeds,
    text,
)

ACTOR = "trudax~reddit-scraper-lite"
ACTOR_URL = "https://api.apify.com/v2/acts/" + ACTOR


def _finish(request, items, ctx, notes):
    limit = integer(request.options, "limit", 50, 1, 1000)
    sort = request.options.get("sort_by")
    if sort:
        allowed = {entry.metric for item in items for entry in item.evidence}
        if sort not in allowed and items:
            raise ConfigurationError("sort_by must be a returned provider metric name.")
        def key(item):
            value = next((e.value for e in item.evidence if e.metric == sort), None)
            return (value is None, (value or 0) * (1 if sort.endswith("position") else -1), item.phrase)
        items.sort(key=key)
    return PluginResult("reddit", request.operation, tuple(items[:limit]), tuple(notes),
                        {**ctx, "rows_received": len(items), "output_truncated": len(items) > limit})


def run_provider(request, transport=None):
    o, operation = request.options, request.operation
    now = datetime.now(timezone.utc).isoformat()
    if operation.startswith("apify-"):
        with HTTP(o, ("api.apify.com",), transport) as http:
            result = apify(request, http)
            return replace(result, metadata={**result.metadata, "request_count": http.requests})
    if operation == "web-search":
        query = seeds(request)[0]
        with HTTP(o, ("serpapi.com",), transport) as http:
            params = {"engine": "google", "q": "site:reddit.com " + query,
                      "api_key": secret(o, "api_key", "SERPAPI_API_KEY"),
                      "gl": code(o, "country", "gb"), "hl": code(o, "language", "en")}
            data = provider_error(http.json("GET", "https://serpapi.com/search.json", params=params))
            ctx = analysis.context("SerpApi Google site:reddit.com results", "web-search:" + json.dumps({k: v for k, v in params.items() if k != "api_key"}, sort_keys=True),
                                   now, country=params["gl"], live_query_performed=True,
                                   request_count=http.requests, search_engine="Google-not-Reddit")
            items = []
            for row in array(data.get("organic_results", [])):
                row = obj(row)
                url = analysis.safe_url(row.get("link"))
                if not url:
                    continue
                host = urlsplit(url).hostname
                if host != "reddit.com" and not host.endswith(".reddit.com"):
                    continue
                items.append(KeywordCandidate(text(row, "title"), "reddit-web-search-result", None,
                    (analysis.evidence(ctx, "google_result_position", analysis.metric(row.get("position")), "ordinal"),),
                    {"url": url, "snippet": row.get("snippet"), "known_status": "search-engine-index-observation"}))
            return _finish(request, items, ctx, (
                "These are Google-indexed Reddit pages, not native Reddit search rankings or search volumes.",
                "Titles/snippets can be stale or truncated. Provider calls may consume paid credits.",))
    with HTTP(o, ("api.keywordtool.io",), transport, interval=4.0) as http:
        terms = seeds(request, 100 if operation == "metrics" else 1)
        if any(len(v) > 80 or len(v.split()) > 10 for v in terms):
            raise ConfigurationError("Keyword Tool queries are limited to 80 characters / 10 words.")
        country = code(o, "country", pattern=r"(?:[A-Z]{2}|GLB)")
        language = code(o, "language", "en", r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*")
        include = operation == "metrics" or boolean(o, "metrics")
        body = {"apikey": secret(o, "api_key", "KEYWORDTOOL_API_KEY"), "output": "json",
                "keyword": terms if operation == "metrics" else terms[0], "country": country,
                "language": language}
        kind = text(o, "suggestion_type", "suggestions")
        if kind not in {"suggestions", "communities"}:
            raise ConfigurationError("Use suggestion_type=suggestions or communities; user-profile discovery is excluded.")
        if operation == "suggestions":
            body.update(type=kind, metrics=include)
        currency = code(o, "currency", "USD", r"[A-Z]{3}")
        if include:
            body["metrics_currency"] = currency
        version = "v2-sandbox" if boolean(o, "sandbox") else "v2"
        endpoint = "volume" if operation == "metrics" else "suggestions"
        data = provider_error(http.json("POST", f"https://api.keywordtool.io/{version}/search/{endpoint}/reddit", json=body))
        scope = json.dumps({k: v for k, v in body.items() if k != "apikey"}, sort_keys=True)
        ctx = analysis.context("Keyword Tool Reddit API", version + ":" + operation + ":" + scope,
                               now, country=country, language=language, live_query_performed=True,
                               request_count=http.requests, sandbox=boolean(o, "sandbox"),
                               partial_notice=bool(data.get("notice")))
        items = []
        for index, (word, value) in enumerate(obj(data.get("results")).items(), 1):
            row = obj(value)
            values = {"suggestion_position": (index, "ordinal")}
            if include:
                values.update(estimated_search_volume=(analysis.metric(row.get("volume")), "searches_per_month"),
                              google_ads_cpc_proxy=(analysis.metric(row.get("cpc")), currency),
                              google_ads_paid_competition_proxy=(analysis.metric(row.get("cmp"), ratio=True), "index_0_1"))
            items.append(KeywordCandidate(text(row, "string", word), "reddit-provider-suggestion", None,
                          tuple(analysis.evidence(ctx, key, val, unit) for key, (val, unit) in values.items()),
                          {"known_status": "provider-suggested-not-native-verified", "suggestion_type": kind,
                           "monthly_metrics": {k: analysis.metric(v) for k, v in row.items()
                                               if k.startswith("m") and k[1:2].isdigit()}}))
        return _finish(request, items, ctx, (
            "Search volumes are provider estimates, not Reddit-published measurements.",
            "CPC and paid competition are Google Ads-derived proxies, not Reddit organic ranking difficulty.",
            "The result limit is not a spending cap; this API may bill for a larger response.",))


def apify_rows(rows, dataset_kind):
    output, skipped = [], 0
    for value in rows:
        raw = obj(value)
        data_type = raw.get("dataType")
        expected = {"posts": "post", "comments": "comment", "communities": "community"}[dataset_kind]
        if data_type != expected or raw.get("isAd") is True:
            skipped += 1
            continue
        if data_type == "community":
            url = analysis.safe_url(raw.get("url")) or ""
            name = urlsplit(url).path.strip("/").split("/")
            if len(name) != 2 or name[0] != "r":
                raise InputError("Apify community record has no unambiguous subreddit URL.")
            output.append({"kind": "t5", "data": {"display_name": name[1],
                "title": raw.get("title"), "public_description": raw.get("description"),
                "subscribers": raw.get("numberOfMembers"), "over18": raw.get("over18", False)}})
        else:
            output.append({"kind": "t1" if data_type == "comment" else "t3", "id": raw.get("id"),
                "title": raw.get("title", ""), "body": raw.get("body", ""),
                "subreddit": raw.get("parsedCommunityName") or raw.get("communityName"),
                "num_comments": raw.get("numberOfComments"), "upvote_ratio": raw.get("upVoteRatio"),
                "provider_reported_votes": raw.get("upVotes"),  # deliberately not mapped to exact upvotes
                "created_at": raw.get("createdAt"), "permalink": raw.get("url"),
                "parent_id": raw.get("parentId"), "flair": raw.get("flair", ""),
                "over_18": raw.get("over18", False)})
    return output, skipped


def apify(request, http):
    o = request.options
    headers = {"Authorization": "Bearer " + secret(o, "api_key", "APIFY_TOKEN")}
    if request.operation == "apify-start":
        if not boolean(o, "allow_paid") or not boolean(o, "collection_authorized"):
            raise ConfigurationError("Starting a job needs allow_paid=true and collection_authorized=true; neither grants Reddit permission.")
        budget = analysis.metric(o.get("max_charge_usd"))
        if budget is None or not 0 < budget <= 100:
            raise ConfigurationError("Set a positive max_charge_usd no greater than 100.")
        mode = text(o, "actor", "search-posts")
        if mode not in {"search-posts", "search-comments", "search-communities", "subreddit-posts"}:
            raise ConfigurationError("Unknown Apify Reddit collection preset.")
        limit = integer(o, "results_per_seed", 30, 1, 500)
        comments = boolean(o, "include_comments") or mode == "search-comments"
        body = {"startUrls": [], "searches": [], "searchPosts": mode == "search-posts",
                "searchComments": mode == "search-comments", "searchCommunities": mode == "search-communities",
                "searchUsers": False, "searchMedia": False, "skipUserPosts": True,
                "skipComments": not comments, "skipCommunity": mode != "search-communities",
                "includeMediaLinks": True, "includeNSFW": boolean(o, "include_nsfw"),
                "maxItems": limit, "maxPostCount": limit,
                "maxComments": integer(o, "comment_limit", 20, 0, 100) if comments else 0,
                "maxCommunitiesCount": limit if mode == "search-communities" else 0,
                "maxUserCount": 0, "debugMode": False,
                "sort": text(o, "sort", "new"), "time": text(o, "time", "month")}
        if body["sort"] not in {"relevance", "new", "hot", "top", "comments"} or body["time"] not in {
                "hour", "day", "week", "month", "year", "all"}:
            raise ConfigurationError("Unsupported Apify sort/time value.")
        if mode == "subreddit-posts":
            body["startUrls"] = [{"url": "https://www.reddit.com/r/" + analysis.subreddit(v) + "/"}
                                 for v in seeds(request, 5)]
            if "sort" in o or "time" in o:
                raise ConfigurationError("This actor ignores search sort/time for start URLs; use search-posts instead.")
        else:
            body["searches"] = seeds(request, 5)
            if "subreddit" in o:
                body["searchCommunityName"] = analysis.subreddit(o["subreddit"])
        scope = "apify:" + mode + ":" + json.dumps(body, sort_keys=True)
        data = obj(provider_error(http.json("POST", ACTOR_URL + "/runs", headers=headers,
                    params={"maxTotalChargeUsd": budget, "timeout": 300}, json=body)).get("data"))
        return PluginResult("reddit", "apify-start", notes=(
            "A paid-capable job was submitted; it is not a completed collection. Fetch the same run separately.",
            "Third-party collection does not grant Reddit data rights. The provider controls billing and internal requests.",),
            metadata={"platform": "Reddit", "run_id": text(data, "id"), "status": data.get("status"),
                      "scope": scope, "actor": ACTOR, "max_charge_usd_requested": budget,
                      "live_query_performed": True})
    run_id = code(o, "run_id", pattern=r"[A-Za-z0-9]{1,80}")
    scope = text(o, "scope")
    kind = text(o, "dataset_kind", "posts")
    if kind not in {"posts", "comments", "communities"}:
        raise ConfigurationError("dataset_kind must be posts, comments or communities.")
    actor = obj(provider_error(http.json("GET", ACTOR_URL, headers=headers)).get("data"))
    run = obj(provider_error(http.json("GET", f"https://api.apify.com/v2/actor-runs/{run_id}", headers=headers)).get("data"))
    if run.get("actId") != actor.get("id") or not actor.get("id"):
        raise OnlineSourceError("The run does not belong to the supported Reddit actor.")
    if run.get("status") != "SUCCEEDED":
        raise OnlineSourceError("The Apify run is not successfully completed. No restart/poll was attempted.")
    dataset = code(run, "defaultDatasetId", pattern=r"[A-Za-z0-9]{1,80}")
    start = integer(o, "dataset_offset", 0, 0, 1000000)
    limit = integer(o, "max_records", 1000, 1, 10000)
    raw = array(http.json("GET", f"https://api.apify.com/v2/datasets/{dataset}/items", headers=headers,
                         params={"format": "json", "clean": "true", "offset": start, "limit": limit}))
    rows, skipped = apify_rows(raw, kind)
    ctx = analysis.context("Apify " + ACTOR, scope, run.get("finishedAt"),
                           collection_started_at=run.get("startedAt"), live_query_performed=True,
                           run_id=run_id, skipped_other_rows=skipped,
                           next_dataset_offset=start + len(raw) if len(raw) == limit else None,
                           collection_complete=False)
    if kind == "communities":
        return analysis.communities(rows, ctx, o, "apify-fetch")
    return analysis.analyse(rows, o, ctx, "apify-fetch")
