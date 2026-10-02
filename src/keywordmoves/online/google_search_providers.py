"""Explicit Google Search provider adapters, never silently switching providers."""
from __future__ import annotations

import base64
import os
import string
from typing import Any, Mapping
from urllib.parse import urlencode

from ..errors import ConfigurationError
from ..models import PluginRequest
from .common import (
    HTTP,
    OnlineSourceError,
    array,
    boolean,
    candidate,
    choice,
    code,
    integer,
    obj,
    provider_error,
    secret,
    seeds,
    text,
)
from .google_search_analysis import SERP_SCHEMA, normal_url, numeric, validate_serp
from .websites import check_robots

PROVIDER_HOSTS = (
    "serpapi.com", "api.dataforseo.com", "suggestqueries.google.com",
    "www.googleapis.com", "customsearch.googleapis.com", "googleads.googleapis.com",
    "api.keywordtool.io", "api.semrush.com", "api.keywordseverywhere.com", "api.ahrefs.com",
    "alsoaskedapi.com", "sandbox.alsoaskedapi.com",
)


def dfs_post(http: HTTP, options: Mapping[str, Any], path: str, body: dict) -> tuple[list, dict]:
    credentials = secret(options, "login", "DATAFORSEO_LOGIN") + ":" + secret(options, "password", "DATAFORSEO_PASSWORD")
    data = obj(http.json("POST", "https://api.dataforseo.com/v3/" + path,
                        headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}, json=[body]))
    if data.get("status_code") != 20000:
        raise OnlineSourceError("DataForSEO rejected the request; check access, quota and options.")
    tasks = array(data.get("tasks"))
    if len(tasks) != 1 or obj(tasks[0]).get("status_code") != 20000:
        raise OnlineSourceError("The DataForSEO task did not succeed; no partial result was accepted.")
    return array(tasks[0].get("result") or []), {"reported_cost_usd": numeric(data.get("cost")), "task_id": tasks[0].get("id")}


def serp_base(query: str, source: str, options: Mapping[str, Any], observed: str, engine: str = "google") -> dict:
    return {"schema": SERP_SCHEMA, "query": query, "source": source, "engine": engine,
            "observed_at": observed, "country": options.get("country"),
            "language": options.get("language", "en"), "device": options.get("device", "desktop"),
            "location": options.get("location", options.get("location_code")),
            "scope": text(options, "scope", "organic-google-search"),
            "organic": [], "features_returned": [], "suggestions": [], "ai_overview_sources": [],
            "approximate_total_results": None, "exhaustive": False}


def parse_serpapi(data: Any, report: dict, offset: int = 0) -> None:
    data = provider_error(data)
    status = obj(data.get("search_metadata", {})).get("status")
    if status not in (None, "Success"):
        raise OnlineSourceError("SerpApi search has not completed successfully.")
    if "organic_results" not in data and "search_information" not in data:
        raise OnlineSourceError("SerpApi returned no recognised search-results envelope.")
    for raw in array(data.get("organic_results", [])):
        raw = obj(raw)
        position = numeric(raw.get("position"), low=1)
        if position is None:
            raise OnlineSourceError("Organic results must include their provider rank.")
        # SerpApi page positions are page-local; retain the requested offset.
        report["organic"].append({"url": raw.get("link"), "title": raw.get("title"),
                                  "snippet": raw.get("snippet"), "position": offset + position})
    information = obj(data.get("search_information", {}))
    if information.get("total_results") is not None:
        report["approximate_total_results"] = numeric(information["total_results"])
    for key in ("ads", "shopping_results", "local_results", "knowledge_graph", "answer_box",
                "inline_videos", "video_results", "news_results", "top_stories", "images_results",
                "related_questions", "related_searches", "ai_overview"):
        if data.get(key):
            report["features_returned"].append(key)
    for key, kind, word in (("related_searches", "related-search", "query"),
                            ("related_questions", "people-also-ask", "question")):
        for row in array(data.get(key, [])):
            report["suggestions"].append({"phrase": obj(row).get(word), "kind": kind,
                                          "next_page_token": obj(row).get("next_page_token")})
    ai = data.get("ai_overview")
    if isinstance(ai, dict):
        for raw in array(ai.get("references", [])):
            raw = obj(raw)
            link = raw.get("link") or raw.get("url")
            if link:
                report["ai_overview_sources"].append({"url": normal_url(link), "title": raw.get("title")})
        report["ai_overview_requires_followup"] = bool(ai.get("page_token"))
    pagination = data.get("serpapi_pagination") or {}
    report["provider_next_page_available"] = bool(obj(pagination).get("next"))


def parse_dfs_serp(data: dict, report: dict) -> None:
    report["approximate_total_results"] = numeric(data.get("se_results_count"))
    report["provider_checked_at"] = data.get("datetime")
    for raw in array(data.get("items") or []):
        raw = obj(raw)
        kind = raw.get("type")
        if kind == "organic":
            report["organic"].append({"position": raw.get("rank_group"),
                                      "absolute_position": raw.get("rank_absolute"),
                                      "url": raw.get("url"), "title": raw.get("title"),
                                      "snippet": raw.get("description")})
        elif isinstance(kind, str):
            report["features_returned"].append(kind)
            if kind in ("related_searches", "people_also_search"):
                for item in array(raw.get("items") or []):
                    word = item if isinstance(item, str) else obj(item).get("title")
                    if word:
                        report["suggestions"].append({"phrase": word, "kind": "related-search"})
            elif kind == "people_also_ask":
                for item in array(raw.get("items") or []):
                    if obj(item).get("title"):
                        report["suggestions"].append({"phrase": item["title"], "kind": "people-also-ask"})
            elif kind == "ai_overview":
                # Retain returned references, never call a second paid endpoint implicitly.
                for item in array(raw.get("references") or []):
                    if obj(item).get("url"):
                        report["ai_overview_sources"].append({"url": normal_url(item["url"]), "title": item.get("title")})


def serp(request: PluginRequest, http: HTTP, observed: str) -> dict:
    o = request.options
    query = seeds(request)[0]
    provider = choice(o, "provider", "serpapi", ("serpapi", "dataforseo"))
    pages = integer(o, "pages", 1, 1, 5)
    device = choice(o, "device", "desktop", ("desktop", "mobile", "tablet"))
    report = serp_base(query, provider, o, observed)
    if provider == "serpapi":
        params = {"engine": "google", "q": query, "api_key": secret(o, "api_key", "SERPAPI_API_KEY"),
                  "gl": code(o, "country").lower(), "hl": code(o, "language", "en", r"[A-Za-z-]{2,12}"),
                  "device": device}
        if "location" in o:
            params["location"] = text(o, "location")
        if pages > http.maximum-http.requests:
            raise ConfigurationError("pages exceeds the remaining request budget.")
        for index in range(pages):
            data = http.json("GET", "https://serpapi.com/search.json", params={**params, "start": index*10})
            parse_serpapi(data, report, index*10)
            if not report["provider_next_page_available"]:
                break
    else:
        if device == "tablet":
            raise ConfigurationError("DataForSEO organic SERP supports desktop/mobile in this adapter.")
        location = integer(o, "location_code", 0, 1, 9999999)
        body = {"keyword": query, "location_code": location, "language_code": code(o, "language", "en"),
                "device": device, "max_crawl_pages": pages}
        values, extra = dfs_post(http, o, "serp/google/organic/live/advanced", body)
        if len(values) != 1:
            raise OnlineSourceError("Expected one DataForSEO SERP result.")
        parse_dfs_serp(obj(values[0]), report)
        report.update(extra)
    return validate_serp(report)


def autocomplete(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    seed = seeds(request)[0]
    provider = choice(o, "provider", "serpapi", ("serpapi", "web"))
    expansion = choice(o, "expand", "none", ("none", "alphabet", "questions", "prepositions"))
    all_queries = [seed]
    if expansion == "alphabet":
        all_queries.extend(seed + " " + c for c in string.ascii_lowercase)
    elif expansion == "questions":
        all_queries.extend(w + " " + seed for w in ("how", "what", "why", "where", "when", "which", "can"))
    elif expansion == "prepositions":
        all_queries.extend(seed + " " + w for w in ("for", "with", "without", "near", "versus", "like", "to"))
    start = integer(o, "expansion_offset", 0, 0, len(all_queries)-1)
    amount = integer(o, "max_queries", 5, 1, 10)
    queries = all_queries[start:start+amount]
    per_query = 2 if provider == "web" else 1
    if len(queries)*per_query > http.maximum-http.requests:
        raise ConfigurationError("Expansion would exceed max_requests; reduce max_queries or explicitly raise the request budget.")
    if provider == "web" and not boolean(o, "allow_unofficial"):
        raise ConfigurationError("Browser autocomplete requires allow_unofficial=true after reviewing provider rules.")
    language = code(o, "language", "en", r"[A-Za-z-]{2,12}")
    country = code(o, "country").lower() if provider == "serpapi" else None
    key = secret(o, "api_key", "SERPAPI_API_KEY") if provider == "serpapi" else None
    items = []
    for query in queries:
        if provider == "serpapi":
            data = provider_error(http.json("GET", "https://serpapi.com/search.json", params={
                "engine": "google_autocomplete", "q": query, "hl": language, "gl": country, "api_key": key}))
            suggestions = [obj(v).get("value") for v in array(data.get("suggestions", []))]
        else:
            url = "https://suggestqueries.google.com/complete/search"
            params = {"client": "firefox", "q": query, "hl": language}
            check_robots(http, url + "?" + urlencode(params))
            payload = array(http.json("GET", url, params=params))
            if len(payload) < 2 or payload[0] != query:
                raise OnlineSourceError("Autocomplete response did not match the requested query.")
            suggestions = array(payload[1])
        for index, word in enumerate(suggestions, 1):
            items.append(candidate("Google autocomplete via " + provider, word,
                                   {"suggestion_order": (index, "ordinal")}, observed_at=observed,
                                   geography=country, relationship="autocomplete",
                                   metadata={"seed": seed, "probe": query, "language": language},
                                   note="Autocomplete is a suggestion signal, not exact search volume or ranking difficulty."))
    next_offset = start+len(queries)
    return items, {"probes": queries, "next_expansion_offset": next_offset if next_offset < len(all_queries) else None,
                   "unqueried_probes": len(all_queries)-next_offset, "provider": provider}, [
        "Expansion probes are generated locally; only returned suggestions are evidence.",
        "Web mode has no verified country targeting and stops on robots denial or blocking."]


def questions(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    if "next_page_token" not in o:
        report = serp(request, http, observed)
        from .google_search_analysis import serp_suggestions
        return [i for i in serp_suggestions(report) if i.relationship == "people-also-ask"], {"serp_report": report}, []
    if o.get("provider", "serpapi") != "serpapi":
        raise ConfigurationError("Explicit related-question continuation uses SerpApi only.")
    query = seeds(request)[0]
    data = provider_error(http.json("GET", "https://serpapi.com/search.json", params={
        "engine": "google_related_questions", "next_page_token": text(o, "next_page_token"),
        "api_key": secret(o, "api_key", "SERPAPI_API_KEY")}))
    items = []
    for index, raw in enumerate(array(data.get("related_questions", [])), 1):
        raw = obj(raw)
        items.append(candidate("SerpApi Google People Also Ask", raw.get("question"),
                               {"returned_order": (index, "ordinal")}, observed_at=observed,
                               relationship="people-also-ask", metadata={"seed": query, "next_page_token": raw.get("next_page_token")},
                               note="No recursive expansion or demand inference is performed."))
    return items, {}, []


def custom_search(request: PluginRequest, http: HTTP, observed: str) -> dict:
    o = request.options
    if not boolean(o, "existing_customer"):
        raise ConfigurationError("Custom Search JSON API is closed to new customers; existing_customer=true confirms existing access, not a grant of access.")
    query = seeds(request)[0]
    pages = integer(o, "pages", 1, 1, 10)
    if pages > http.maximum-http.requests:
        raise ConfigurationError("pages exceeds max_requests.")
    engine = text(o, "cx")
    params = {"q": query, "cx": engine, "key": secret(o, "api_key", "GOOGLE_CUSTOM_SEARCH_API_KEY"),
              "gl": code(o, "country").lower(), "hl": code(o, "language", "en"), "num": 10}
    report = serp_base(query, "Google Custom Search JSON API", o, observed, "custom-search:" + engine)
    report["scope"] = "custom-search:" + engine
    for page in range(pages):
        data = provider_error(http.json("GET", "https://customsearch.googleapis.com/customsearch/v1", params={**params, "start": page*10+1}))
        for index, raw in enumerate(array(data.get("items", [])), 1):
            raw = obj(raw)
            report["organic"].append({"position": page*10+index, "url": raw.get("link"),
                                      "title": raw.get("title"), "snippet": raw.get("snippet")})
        report["approximate_total_results"] = numeric(obj(data.get("searchInformation", {})).get("totalResults"))
        if not obj(data.get("queries", {})).get("nextPage"):
            break
    report["warning"] = "Configured engine results, not a google.com rank check. Existing-customer service scheduled to end 2027-01-01."
    return validate_serp(report)


def backlinks(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    target = text(o, "target")
    if "://" in target:
        normal_url(target)
    else:
        code({"target": target}, "target", pattern=r"[A-Za-z0-9.-]+")
    rows, meta = dfs_post(http, o, "backlinks/summary/live", {
        "target": target, "include_subdomains": boolean(o, "include_subdomains", True),
        "backlinks_status_type": choice(o, "backlink_status", "live", ("live", "lost", "all")),
        "internal_list_limit": 10})
    if len(rows) != 1:
        raise OnlineSourceError("Expected one backlink summary.")
    row = obj(rows[0])
    metrics = {k: (numeric(row.get(k)), "count") for k in
               ("backlinks", "referring_domains", "referring_main_domains", "referring_pages", "referring_ips", "broken_backlinks")}
    metrics["provider_rank"] = (numeric(row.get("rank")), "provider_native_rank_not_google_pagerank")
    return [candidate("DataForSEO Backlinks", target, metrics, observed_at=observed,
                      relationship="backlink-profile", metadata={"target": target, "provider_updated_at": row.get("last_seen")},
                      note="Provider backlink index, not Google's index. Referring domains include subdomains; referring_main_domains does not.")], meta, [
        "Page-target and domain-target summaries are not interchangeable. No automatic top-result enrichment is performed."]


def competitor_keywords(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    target = text(o, "target")
    location = integer(o, "location_code", 0, 1, 9999999)
    language = code(o, "language", "en")
    rows, meta = dfs_post(http, o, "dataforseo_labs/google/ranked_keywords/live", {
        "target": target, "location_code": location, "language_code": language, "item_types": ["organic"],
        "limit": integer(o, "limit", 100, 1, 1000), "offset": integer(o, "offset", 0, 0, 1000000)})
    items = []
    for result in rows:
        for row in array(obj(result).get("items") or []):
            row = obj(row)
            keyword = obj(row.get("keyword_data"))
            info = obj(keyword.get("keyword_info") or {})
            properties = obj(keyword.get("keyword_properties") or {})
            serp_item = obj(obj(row.get("ranked_serp_element", {})).get("serp_item", {}))
            if serp_item.get("type") != "organic":
                continue
            items.append(candidate("DataForSEO Labs Google", keyword.get("keyword"), {
                "provider_observed_organic_rank": (numeric(serp_item.get("rank_group")), "position_1_based"),
                "estimated_search_volume": (numeric(info.get("search_volume")), "searches_per_month"),
                "organic_keyword_difficulty": (numeric(properties.get("keyword_difficulty"), high=100), "provider_index_0_100"),
            }, observed_at=observed, geography=str(location), relationship="competitor-ranking-keyword",
                metadata={"target": target, "ranking_url": serp_item.get("url"), "language": language,
                          "provider_updated_at": info.get("last_updated_time")},
                note="Provider database observation; it may lag current rankings and is not an exhaustive keyword inventory."))
    return items, {**meta, "target": target, "location_code": location, "language": language}, []


def trends(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    query = seeds(request)[0]
    data_type = choice(o, "data_type", "TIMESERIES", ("TIMESERIES", "RELATED_QUERIES", "RELATED_TOPICS", "GEO_MAP_0"))
    country = code(o, "country", pattern=r"[A-Za-z]{2}(-[A-Za-z0-9]+)?").upper()
    timeframe = text(o, "timeframe", "today 12-m")
    data = provider_error(http.json("GET", "https://serpapi.com/search.json", params={
        "engine": "google_trends", "q": query, "data_type": data_type,
        "geo": country, "date": timeframe, "hl": code(o, "language", "en"),
        "cat": integer(o, "category", 0, 0, 10000), "api_key": secret(o, "api_key", "SERPAPI_API_KEY")}))
    items, native = [], {}
    if data_type == "TIMESERIES":
        native = obj(data.get("interest_over_time"))
        points = array(native.get("timeline_data"))
        for point in points:
            for value in array(obj(point).get("values")):
                value = obj(value)
                if value.get("extracted_value") is not None:
                    numeric(value["extracted_value"], high=100)
        items = [candidate("Google Trends via SerpApi", query, {"returned_time_points": (len(points), "count")},
                           observed_at=observed, geography=country, relationship="relative-search-interest",
                           note="Index values are request-normalised relative interest, not searches. Censored values and partial flags remain in the timeline.")]
    elif data_type in ("RELATED_QUERIES", "RELATED_TOPICS"):
        native = obj(data.get("related_queries" if data_type == "RELATED_QUERIES" else "related_topics"))
        for category in ("top", "rising"):
            for index, row in enumerate(array(native.get(category, [])), 1):
                row = obj(row)
                label = row.get("query") if data_type == "RELATED_QUERIES" else obj(row.get("topic", {})).get("title")
                items.append(candidate("Google Trends via SerpApi", label,
                                       {"returned_order": (index, "ordinal"), "native_display": (str(row.get("value", "")), "provider_display")},
                                       observed_at=observed, geography=country, relationship="trends-" + category,
                                       metadata={"seed": query}, note="Top interest and rising growth/Breakout are different signals, not search volumes."))
    else:
        native = {"interest_by_region": array(data.get("interest_by_region"))}
        items = [candidate("Google Trends via SerpApi", query, {}, observed_at=observed, geography=country,
                           relationship="relative-interest-by-region", note="Geographic indices are not population-adjusted absolute demand.")]
    return items, {"query": query, "country": country, "timeframe": timeframe,
                   "data_type": data_type, "native_trends": native, "search_property": "web"}, []


def ads_url_ideas(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    kind = choice(o, "seed_type", "url", ("url", "site", "keyword-url"))
    url = normal_url(text(o, "url"))
    customer = text(o, "customer_id").replace("-", "")
    if not customer.isdigit():
        raise ConfigurationError("customer_id must be numeric.")
    version = code(o, "api_version", os.environ.get("GOOGLE_ADS_API_VERSION"), r"v[0-9]+")
    locations = text(o, "location_codes").split(",")
    if not 1 <= len(locations) <= 10 or not all(v.strip().isdigit() for v in locations):
        raise ConfigurationError("location_codes must contain 1-10 numeric geo-target IDs.")
    language = code(o, "language_id", "1000", r"[0-9]+")
    seed = {"urlSeed": {"url": url}} if kind == "url" else {"siteSeed": {"site": url}}
    if kind == "keyword-url":
        seed = {"keywordAndUrlSeed": {"url": url, "keywords": seeds(request, 20)}}
    elif request.keywords:
        raise ConfigurationError("Only seed_type=keyword-url accepts --keyword alongside the URL.")
    headers = {"Authorization": "Bearer " + secret(o, "access_token", "GOOGLE_ADS_ACCESS_TOKEN"),
               "developer-token": secret(o, "developer_token", "GOOGLE_ADS_DEVELOPER_TOKEN")}
    if "login_customer_id" in o:
        headers["login-customer-id"] = code({"id": text(o, "login_customer_id").replace("-", "")}, "id", pattern=r"[0-9]+")
    data = provider_error(http.json("POST", f"https://googleads.googleapis.com/{version}/customers/{customer}:generateKeywordIdeas",
                                   headers=headers, json={**seed, "language": "languageConstants/"+language,
                                                         "geoTargetConstants": ["geoTargetConstants/"+v.strip() for v in locations],
                                                         "keywordPlanNetwork": "GOOGLE_SEARCH",
                                                         "pageSize": integer(o, "limit", 100, 1, 1000),
                                                         **({"pageToken": text(o, "page_token")} if "page_token" in o else {})}))
    items = []
    for row in array(data.get("results", [])):
        info = obj(obj(row).get("keywordIdeaMetrics") or {})
        items.append(candidate("Google Ads Keyword Planning", row.get("text"), {
            "avg_monthly_searches": (numeric(info.get("avgMonthlySearches")), "searches_per_month"),
            "paid_competition": (info.get("competition"), "enum"),
            "paid_competition_index": (numeric(info.get("competitionIndex"), high=100), "index_0_100"),
            "low_top_of_page_bid": (numeric(info.get("lowTopOfPageBidMicros")), "account_currency_micros"),
            "high_top_of_page_bid": (numeric(info.get("highTopOfPageBidMicros")), "account_currency_micros"),
        }, observed_at=observed, geography=",".join(locations), relationship="url-seeded-keyword-idea",
            metadata={"seed_type": kind, "seed_url": url, "monthly_search_volumes": info.get("monthlySearchVolumes", [])},
            note="Planning estimates and paid-auction competition, not organic ranking difficulty."))
    return items, {"next_page_token": data.get("nextPageToken"), "api_version": version}, []


def pagespeed(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    url = normal_url(text(o, "url"))
    strategy = choice(o, "strategy", "mobile", ("mobile", "desktop"))
    params = {"url": url, "strategy": strategy, "category": "PERFORMANCE"}
    if "api_key" in o or os.environ.get("PAGESPEED_API_KEY"):
        params["key"] = secret(o, "api_key", "PAGESPEED_API_KEY")
    data = provider_error(http.json("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params))
    lighthouse = obj(data.get("lighthouseResult"))
    if lighthouse.get("runtimeError"):
        raise OnlineSourceError("PageSpeed could not complete the Lighthouse run.")
    audits = obj(lighthouse.get("audits"))
    metrics = {"lighthouse_performance": (numeric(obj(obj(lighthouse.get("categories")).get("performance")).get("score"), high=1), "fraction_0_1_not_ranking_score")}
    for key in ("largest-contentful-paint", "first-contentful-paint", "total-blocking-time", "cumulative-layout-shift"):
        audit = obj(audits.get(key, {}))
        metrics["lab_" + key.replace("-", "_")] = (numeric(audit.get("numericValue")), str(audit.get("numericUnit") or "provider_unit"))
    return [candidate("Google PageSpeed Insights", url, metrics, observed_at=observed,
                      relationship="page-performance-context", metadata={"strategy": strategy, "fetch_time": lighthouse.get("fetchTime"),
                          "lighthouse_version": lighthouse.get("lighthouseVersion"), "final_url": lighthouse.get("finalUrl")},
                      note="Lab performance is context for technical review, not a Google ranking or keyword-difficulty score.")], {}, []
