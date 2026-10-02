"""Explicit third-party and experimental public YouTube acquisition routes.

Provider estimates remain distinct from first-party API measurements. No automatic
paid retries, arbitrary actor code, embedded-JavaScript execution or cookies.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import replace
from urllib.parse import quote, urlsplit
from urllib.robotparser import RobotFileParser

from ..errors import ConfigurationError
from ..models import KeywordCandidate, KeywordEvidence
from . import youtube_analysis as a
from . import youtube_imports as imp
from .commercial import KeywordToolPlugin
from .common import (
    USER_AGENT,
    OnlineSourceError,
    boolean,
    choice,
    code,
    integer,
    obj,
    provider_error,
    secret,
    seeds,
    text,
)
from .youtube import identifier, scope

HOSTS = {
    "suggestions": ("api.keywordtool.io",), "metrics": ("api.keywordtool.io",),
    "serp-search": ("serpapi.com",), "dataforseo-search": ("api.dataforseo.com",),
    "autocomplete": ("suggestqueries.google.com",), "public-hashtag": ("www.youtube.com",),
    "apify-start": ("api.apify.com",), "apify-fetch": ("api.apify.com",),
}
ACTORS = {"videos": "streamers~youtube-scraper", "comments": "streamers~youtube-comments-scraper"}
PRESETS = ("search-videos", "hashtag-videos", "channel-videos", "playlist-videos", "video-details", "shorts", "streams", "comments")


def hosts_for(operation: str) -> tuple[str, ...]:
    return HOSTS[operation]


def robots(http, url: str) -> None:
    p = urlsplit(url)
    origin = f"https://{p.hostname}"
    status, value = http.request("GET", origin + "/robots.txt", allowed_statuses=(404,))
    if status == 404:
        return
    if "<html" in value.casefold() or not re.search(r"(?im)^\s*user-agent\s*:", value):
        raise OnlineSourceError("robots.txt is unreadable; no page request was made.")
    parser = RobotFileParser()
    parser.parse(value.splitlines())
    if not parser.can_fetch(USER_AGENT, url):
        raise OnlineSourceError("The site's robots policy disallows this request.")
    delay = parser.crawl_delay(USER_AGENT)
    if delay:
        http.interval = max(http.interval, delay)


def _display(value):
    """Preserve visibly rounded counts instead of pretending expanded ints are exact."""
    if isinstance(value, str):
        m = re.fullmatch(r"([\d,.]+\s*[KMB]?)\s+views?", value.strip(), re.I)
        if m:
            return m[1]
    return value


def _suggestions(request, http, observed):
    o = request.options
    kind = choice(o, "suggestion_type", "hashtags", ("hashtags", "suggestions", "questions", "prepositions"))
    options = {**o, "platform": "youtube", "suggestion_type": kind}
    if "keywordtool_api_key" in o:
        options["api_key"] = o["keywordtool_api_key"]
    items, meta, notes = KeywordToolPlugin().fetch(replace(request, options=options), http, observed)
    out = []
    for item in items:
        category = "hashtag" if request.operation == "suggestions" and kind == "hashtags" else "hashtag" if item.phrase.startswith("#") else "keyword"
        word = a.hashtag(item.phrase) if category == "hashtag" else item.phrase
        ev = tuple(replace(e, metric={"cpc": "google_ads_cpc_proxy", "paid_competition": "google_ads_competition_proxy"}.get(e.metric, e.metric),
                           notes="Provider-estimated YouTube demand; CPC/competition are Google Ads proxies, not YouTube organic difficulty.") for e in item.evidence)
        safe = {"country": o.get("country"), "language": o.get("language", "en"), "type": kind, "query": list(request.keywords)}
        out.append(replace(item, phrase=word, relationship="youtube-" + category + "-suggestion", evidence=ev,
                           metadata={**item.metadata, "kind": category, "scope": scope("keywordtool", safe), "api_data": False,
                                     "verification": "provider-suggested; not platform certification"}))
    return out, {**meta, "source": "Keyword Tool API", "suggestion_type": kind}, notes


def _serp(request, http, observed):
    o = request.options
    p = {"engine": "youtube", "search_query": seeds(request)[0], "hl": code(o, "language", "en", r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,3})?"),
         "gl": code(o, "country", "US").lower()}
    if "sp" in o:
        p["sp"] = text(o, "sp")
    sc = scope("serp-search", p)
    key = secret(o, "serpapi_key", "SERPAPI_API_KEY")
    rows, related, context, token, tokens, ads = [], [], [], None, set(), 0
    maximum = integer(o, "pages", 1, 1, 5)
    for page in range(maximum):
        data = provider_error(http.json("GET", "https://serpapi.com/search.json", params={**p, "api_key": key}))
        if obj(data.get("search_metadata", {})).get("status") not in (None, "Success"):
            raise OnlineSourceError("The SerpApi search is not complete; no polling or retries were made.")
        if not any(k in data for k in ("video_results", "shorts_results", "channel_results", "playlist_results", "related_searches", "search_information")):
            raise OnlineSourceError("SerpApi omitted its known result structures.")
        raw = a.records(data.get("video_results", []))
        for section in a.records(data.get("shorts_results", [])):
            raw.extend({**r, "content_type": "shorts"} for r in a.records(section.get("shorts", [])))
        for r in raw:
            rows.append({"id": a.video_id(r.get("video_id", r.get("link"))), "title": r.get("title"),
                         "description": r.get("description"), "views": _display(r.get("views_original", r.get("views"))),
                         "content_type": r.get("content_type", "unknown")})
            context.append({"video_id": rows[-1]["id"], "page": page + 1, "position_on_page": r.get("position_on_page"),
                            "published_display": r.get("published_date"), "duration_display": r.get("length")})
        for r in a.records(data.get("related_searches", [])):
            word = text(r, "query")
            related.append(KeywordCandidate(word, "youtube-related-search", None,
                (KeywordEvidence("SerpApi YouTube Search", "returned_order", len(related) + 1, "ordinal", observed, o.get("country"), "Order is not search demand."),),
                {"kind": "keyword", "scope": sc, "verification": "provider-related-query"}))
        ads += len(a.records(data.get("ads_results", [])))
        token = obj(data.get("serpapi_pagination", data.get("pagination", {}))).get("next_page_token")
        if not token:
            break
        if not isinstance(token, str) or len(token) > 4096 or token in tokens:
            raise OnlineSourceError("Invalid/repeated SerpApi continuation token.")
        tokens.add(token)
        p["sp"] = token
    items, meta = a.analyse(rows, {**o, "country": None}, source="SerpApi YouTube Search", scope=sc, observed_at=observed)
    meta.update(pages_fetched=page + 1, more_available=bool(token), search_positions=context, excluded_ad_records=ads,
                description_scope="Search snippets can omit hashtags present in full descriptions.")
    return items + related, meta, ["SERP ranks are query/location/time specific, not a universal hashtag ranking; relative publication displays are not parsed into exact dates."]


def _dataforseo(request, http, observed):
    o = request.options
    body = {"keyword": seeds(request)[0], "location_code": integer(o, "location_code", 0, 1, 9999999),
            "language_code": code(o, "language", "en"), "device": choice(o, "device", "desktop", ("desktop", "mobile")),
            "block_depth": integer(o, "block_depth", 20, 1, 200)}
    auth = (secret(o, "login", "DATAFORSEO_LOGIN"), secret(o, "password", "DATAFORSEO_PASSWORD"))
    data = provider_error(http.json("POST", "https://api.dataforseo.com/v3/serp/youtube/organic/live/advanced", auth=auth, json=[body]))
    tasks = a.records(data.get("tasks"), 1)
    if data.get("status_code") != 20000 or len(tasks) != 1 or tasks[0].get("status_code") != 20000:
        raise OnlineSourceError("DataForSEO task did not succeed; no paid retry was made.")
    rows, positions, skipped = [], [], Counter()
    for result in a.records(tasks[0].get("result"), 5):
        for r in a.records(result.get("items", []) or []):
            if r.get("type") != "youtube_video":
                skipped[str(r.get("type", "unknown"))] += 1
                continue
            rid = a.video_id(r.get("video_id", r.get("url")))
            rows.append({"id": rid, "title": r.get("title"), "description": r.get("description"),
                         "views": r.get("views_count"), "published_at": r.get("timestamp"), "channel_id": r.get("channel_id"),
                         "content_type": "shorts" if r.get("is_shorts") is True else "live" if r.get("is_live") is True else "unknown"})
            positions.append({"id": rid, "rank_group": r.get("rank_group"), "rank_absolute": r.get("rank_absolute"), "block_name": r.get("block_name")})
    items, meta = a.analyse(rows, {**o, "country": None}, source="DataForSEO YouTube SERP", scope=scope("dataforseo-search", body), observed_at=observed)
    meta.update(collection_parameters=body, search_positions=positions, excluded_record_types=dict(skipped), provider_cost=data.get("cost"))
    return items, meta, ["Paid SERP collection, billed by provider depth; local limit is not a spending cap. Search result counts are not hashtag totals."]


def _web(request, http, observed):
    o, op = request.options, request.operation
    if not boolean(o, "allow_unofficial"):
        raise ConfigurationError("Experimental website access requires allow_unofficial=true and appropriate permission.")
    query = seeds(request)[0]
    if op == "public-hashtag":
        query = a.hashtag(query)
        url = "https://www.youtube.com/hashtag/" + quote(query[1:], safe="")
        robots(http, url)
        _, value = http.request("GET", url, params={"hl": "en"})
        rows = imp.hashtag_page(value, query)
        source = "YouTube public hashtag page (experimental)"
        items = a.observations(rows, source=source, scope=scope(op, {"hashtag": query, "language": "en"}), observed_at=observed)
    else:
        url = "https://suggestqueries.google.com/complete/search"
        robots(http, url)
        data = http.json("GET", url, params={"client": "firefox", "ds": "yt", "q": query,
                                             "hl": code(o, "language", "en"), "gl": code(o, "country", "US")})
        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
            raise OnlineSourceError("Unexpected experimental autocomplete shape.")
        source = "YouTube browser autocomplete (experimental)"
        items = []
        for i, value in enumerate(data[1]):
            word = value[0] if isinstance(value, list) and value else value
            if not isinstance(word, str) or not word.strip():
                raise OnlineSourceError("Invalid autocomplete text.")
            items.append(KeywordCandidate(word, "youtube-autocomplete", None,
                (KeywordEvidence(source, "suggestion_position", i + 1, "ordinal", observed, o.get("country"), "Autocomplete position is not query volume."),),
                {"kind": "hashtag" if word.startswith("#") else "keyword", "scope": scope(op, {"query": query, "country": o.get("country"), "language": o.get("language")})}))
    return items, {"source": source, "access": "experimental-public-endpoint", "query": query}, [
        "No stable developer contract; robots allowance alone is not permission. No cookies, private endpoints, challenge bypass or automatic fallback."]


def _actor_input(request):
    o = request.options
    preset = choice(o, "actor", "search-videos", PRESETS)
    count = integer(o, "results_per_seed", 20, 1, 500)
    terms = seeds(request, 20)
    if preset == "comments":
        body = {"startUrls": [{"url": "https://www.youtube.com/watch?v=" + a.video_id(v)} for v in terms],
                "maxComments": count, "sortCommentsBy": choice(o, "comment_order", "NEWEST_FIRST", ("NEWEST_FIRST", "TOP_COMMENTS"))}
        return preset, ACTORS["comments"], body
    body = {"maxResults": 0 if preset in {"shorts", "streams"} else count,
            "maxResultsShorts": count if preset == "shorts" else 0,
            "maxResultStreams": count if preset == "streams" else 0,
            "transcriptionAndSubtitle": "NONE", "saveSubsToKVS": False,
            "aiVideoDescription": False, "aiVideoSummary": False}
    if preset in {"search-videos", "shorts", "streams"}:
        body["searchQueries"] = terms
        body["sortingOrder"] = choice(o, "order", "relevance", ("relevance", "rating", "date", "views"))
    else:
        urls = []
        for v in terms:
            if preset == "hashtag-videos":
                url = "https://www.youtube.com/hashtag/" + quote(a.hashtag(v)[1:], safe="")
            elif preset == "video-details":
                url = "https://www.youtube.com/watch?v=" + a.video_id(v)
            elif preset == "playlist-videos":
                url = "https://www.youtube.com/playlist?list=" + identifier(v, "playlist_id")
            else:
                # Strict channel IDs or handles; never arbitrary start URLs or spreadsheets.
                if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", v):
                    url = "https://www.youtube.com/channel/" + v
                elif re.fullmatch(r"@[\w.\-]{3,30}", v):
                    url = "https://www.youtube.com/" + quote(v, safe="@")
                else:
                    raise ConfigurationError("channel-videos seeds must be a UC channel ID or @handle.")
            urls.append({"url": url})
        body["startUrls"] = urls
    return preset, ACTORS["videos"], body


def _apify(request, http, observed):
    o = request.options
    if request.operation == "apify-start":
        if not boolean(o, "allow_paid"):
            raise ConfigurationError("Apify submission requires allow_paid=true and an explicit max_charge_usd.")
        value = o.get("max_charge_usd")
        try:
            charge = float(value)
            if isinstance(value, bool) or not math.isfinite(charge) or not 0 < charge <= 1000:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigurationError("max_charge_usd must be an explicit positive finite amount, at most 1000.") from None
        preset, actor, body = _actor_input(request)
        token = secret(o, "apify_token", "APIFY_TOKEN")
        p = {"maxTotalChargeUsd": charge, "timeout": integer(o, "actor_timeout", 120, 30, 3600)}
        data = provider_error(http.json("POST", f"https://api.apify.com/v2/acts/{actor}/runs", params=p,
                                       headers={"Authorization": "Bearer " + token}, json=body))
        run = obj(data.get("data"))
        rid = identifier(run.get("id"), "run_id")
        return [], {"source": "Apify", "run_id": rid, "status": run.get("status"), "actor": actor, "preset": preset,
                    "submitted": True, "completed": False, "input": body, "requested_max_charge_usd": charge,
                    "scope": scope("apify:" + preset, body)}, ["A remote job was submitted, not completed. Fetch this run separately. The provider enforces its charge ceiling; no automatic retry/restart or polling occurs."]
    if request.keywords:
        raise ConfigurationError("apify-fetch takes run_id, dataset_kind and scope, not seed keywords.")
    rid = identifier(o.get("run_id"), "run_id")
    kind = choice(o, "dataset_kind", "videos", ("videos", "comments", "observations"))
    sc = text(o, "scope")
    headers = {"Authorization": "Bearer " + secret(o, "apify_token", "APIFY_TOKEN")}
    run = obj(provider_error(http.json("GET", f"https://api.apify.com/v2/actor-runs/{rid}", headers=headers)).get("data"))
    if run.get("status") != "SUCCEEDED":
        raise OnlineSourceError("Apify run has not succeeded. No restart/polling or zero-popularity inference was made.")
    dataset = identifier(run.get("defaultDatasetId"), "dataset_id")
    captured = text(run, "finishedAt")
    a.timestamp(captured)
    total = None
    dataset_info = obj(provider_error(http.json("GET", f"https://api.apify.com/v2/datasets/{dataset}", headers=headers)).get("data"))
    total = a.count(dataset_info.get("itemCount"))
    rows, offset = [], 0
    size = integer(o, "page_size", 100, 1, 1000)
    for _ in range(integer(o, "pages", 1, 1, 10)):
        chunk = a.records(http.json("GET", f"https://api.apify.com/v2/datasets/{dataset}/items", headers=headers,
                           params={"format": "json", "clean": "true", "offset": offset, "limit": size}), size)
        rows.extend(chunk)
        offset += len(chunk)
        if len(chunk) < size or (total is not None and offset >= total):
            break
    source = "Apify YouTube dataset"
    if kind == "observations":
        items = a.observations(rows, source=source, scope=sc, observed_at=captured, country=o.get("country"))
        meta = {"source": source, "scope": sc}
    else:
        items, meta = a.analyse(rows, {**o, "country": None}, source=source, scope=sc, observed_at=captured, kind=kind)
    meta.update(run_id=rid, actor_id=run.get("actId"), actor_build_id=run.get("buildId"), dataset_id=dataset,
                source_captured_at=captured, observed_at=captured, fetched_at=observed, dataset_items_reported=total,
                dataset_rows_fetched=len(rows), more_available=offset < total if total is not None else len(chunk) == size,
                provider_usage_usd=run.get("usageTotalUsd"), submitted=False)
    return items, meta, ["Explicit third-party dataset import; the caller must select YouTube data and retain consistent collection scope. Results do not certify global hashtag counts."]


def fetch(request, http, observed):
    operation = request.operation
    if operation in {"suggestions", "metrics"}:
        return _suggestions(request, http, observed)
    if operation == "serp-search":
        return _serp(request, http, observed)
    if operation == "dataforseo-search":
        return _dataforseo(request, http, observed)
    if operation in {"autocomplete", "public-hashtag"}:
        return _web(request, http, observed)
    return _apify(request, http, observed)
