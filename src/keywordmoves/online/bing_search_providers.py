"""Bing-specific provider routes; no retired Azure Bing Search v7 endpoints."""
from __future__ import annotations

import base64
import string
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..models import PluginRequest
from .bing_search_analysis import SERP_SCHEMA, numeric, target_host, url, validate_serp
from .common import (
    HTTP,
    ConfigurationError,
    OnlineSourceError,
    array,
    boolean,
    candidate,
    choice,
    code,
    integer,
    number,
    obj,
    phrase,
    provider_error,
    secret,
    seeds,
    text,
)

SERP_FEATURES = (
    "ads", "local_results", "local_map", "knowledge_graph", "answer_box",
    "inline_images", "inline_videos", "inline_products", "shopping_results",
    "news_results", "related_questions", "related_searches", "copilot_answer",
)


def market(o) -> str:
    parts = code(o, "market", pattern=r"[A-Za-z]{2}-[A-Za-z]{2}").split("-")
    return parts[0].lower() + "-" + parts[1].upper()


def _next_first(payload: dict, current: int, query: str) -> int | None:
    pagination = obj(payload.get("serpapi_pagination") or {})
    raw = pagination.get("next_link") or pagination.get("next")
    if not raw:
        raw = obj(payload.get("pagination") or {}).get("next")
    if not raw:
        return None
    parsed = urlsplit(url(raw))
    if parsed.hostname not in {"serpapi.com", "www.bing.com", "bing.com"} or \
            parsed.path not in {"/search", "/search.json"} or parsed.port not in (None, 443):
        raise OnlineSourceError("Unrecognised Bing pagination URL; no request followed it.")
    q = parse_qs(parsed.query)
    if q.get("q") != [query] or ("engine" in q and q["engine"] != ["bing"]):
        raise OnlineSourceError("Bing continuation changed the query or engine.")
    if len(q.get("first", [])) != 1:
        raise OnlineSourceError("Missing or ambiguous Bing continuation offset.")
    try:
        offset = integer({"first": q["first"][0]}, "first", 1, 1, 10000)
    except ConfigurationError:
        raise OnlineSourceError("Invalid Bing continuation offset.") from None
    if offset <= current:
        raise OnlineSourceError("Bing continuation did not advance.")
    # Only this integer is retained. Never follow a returned URL or forward its credentials.
    return offset


def serpapi(request: PluginRequest, http: HTTP, observed: str) -> dict:
    o = request.options
    query = seeds(request)[0]
    mkt = market(o)
    device = choice(o, "device", "desktop", ("desktop", "tablet", "mobile"))
    first = integer(o, "first", 1, 1, 10000)
    pages = integer(o, "pages", 1, 1, 20)
    if pages > http.maximum:
        raise ConfigurationError("Requested SERP pages exceed max_requests.")
    params = {"engine": "bing", "q": query, "mkt": mkt, "device": device,
              "api_key": secret(o, "api_key", "SERPAPI_API_KEY"),
              "no_cache": "true" if boolean(o, "no_cache") else "false"}
    for k in ("location", "filters"):
        if k in o:
            params[k] = text(o, k)
    params["safeSearch"] = choice(o, "safe_search", "Moderate", ("Off", "Moderate", "Strict"))
    raw = {"schema": SERP_SCHEMA, "engine": "Bing", "source": "SerpApi Bing",
           "observed_at": observed,
           "context": {"query": query, "market": mkt, "language": mkt.split("-")[0],
                       "device": device, "location": params.get("location"),
                       "safe_search": params["safeSearch"], "time_filter": params.get("filters"),
                       "first": first, "requested_pages": pages},
           "organic": [], "related": [], "questions": [], "features": []}
    total = None
    next_first = first
    for _ in range(pages):
        first = next_first
        payload = provider_error(http.json("GET", "https://serpapi.com/search.json",
                                            params={**params, "first": first}))
        meta = obj(payload.get("search_metadata"))
        if meta.get("status") != "Success":
            raise OnlineSourceError("Bing SERP is not complete; no pending/error result was counted as empty.")
        echoed = obj(payload.get("search_parameters"))
        if echoed.get("engine") != "bing" or echoed.get("q") != query:
            raise OnlineSourceError("The provider returned a different query or search engine.")
        if "mkt" in echoed and str(echoed["mkt"]).casefold() != mkt.casefold():
            raise OnlineSourceError("The provider returned a different Bing market.")
        if "device" in echoed and echoed["device"] != device:
            raise OnlineSourceError("The provider returned a different device context.")
        if "first" in echoed and str(echoed["first"]) != str(first):
            raise OnlineSourceError("The provider returned a different pagination offset.")
        # A successful, structurally valid no-results report may omit organic_results.
        if "organic_results" not in payload and obj(payload.get("search_information") or {}).get("total_results") != 0:
            raise OnlineSourceError("No organic results or explicit Bing search information were returned.")
        for r in array(payload.get("organic_results", [])):
            r = obj(r)
            position = numeric(r.get("position"), integer_only=True)
            if position is None or position < 1:
                raise OnlineSourceError("Missing Bing organic-result position.")
            raw["organic"].append({"rank": first - 1 + position, "url": url(r.get("link")),
                                   "title": str(r.get("title") or ""), "snippet": str(r.get("snippet") or "")})
        raw["related"].extend(phrase(obj(r).get("query")) for r in array(payload.get("related_searches", [])))
        raw["questions"].extend(phrase(obj(r).get("question")) for r in array(payload.get("related_questions", [])))
        raw["features"].extend(k for k in SERP_FEATURES if payload.get(k))
        information = obj(payload.get("search_information") or {})
        if total is None:
            total = numeric(information.get("total_results"), integer_only=True)
        next_first = _next_first(payload, first, query)
        if next_first is None:
            break
    raw.update(total_results_estimate=total, next_first=next_first,
               more_available=next_first is not None)
    return validate_serp(raw)


def dfs_call(http: HTTP, endpoint: str, body: dict, o) -> tuple[list, dict]:
    login = secret(o, "login", "DATAFORSEO_LOGIN")
    password = secret(o, "password", "DATAFORSEO_PASSWORD")
    if ":" in login:
        raise ConfigurationError("DataForSEO login cannot contain a colon.")
    headers = {"Authorization": "Basic " + base64.b64encode((login + ":" + password).encode()).decode()}
    p = obj(http.json("POST", "https://api.dataforseo.com/v3/" + endpoint,
                      headers=headers, json=[body]))
    if p.get("status_code") != 20000 or p.get("tasks_error", 0):
        raise OnlineSourceError("DataForSEO rejected the task; check credentials, endpoint access and credits.")
    tasks = array(p.get("tasks"))
    if len(tasks) != 1 or obj(tasks[0]).get("status_code") != 20000:
        raise OnlineSourceError("DataForSEO task failed or is not complete.")
    task = tasks[0]
    result = array(task.get("result"))
    return result, {"provider_task_id": task.get("id"), "provider_reported_cost_usd": numeric(p.get("cost"))}


def dataforseo_serp(request: PluginRequest, http: HTTP, observed: str) -> tuple[dict, dict]:
    o = request.options
    query = seeds(request)[0]
    body = {"keyword": query, "location_code": integer(o, "location_code", 0, 1, 100000000),
            "language_code": code(o, "language", "en", r"[A-Za-z-]{2,12}"),
            "device": choice(o, "device", "desktop", ("desktop", "mobile")),
            "depth": integer(o, "depth", 10, 10, 100)}
    result, meta = dfs_call(http, "serp/bing/organic/live/advanced", body, o)
    if len(result) != 1:
        raise OnlineSourceError("Expected one Bing SERP result from DataForSEO.")
    page = obj(result[0])
    if page.get("keyword") != query or page.get("se_type") not in (None, "bing"):
        raise OnlineSourceError("DataForSEO returned a different query or engine.")
    for k in ("location_code", "language_code", "device"):
        if k in page and page[k] != body[k]:
            raise OnlineSourceError("DataForSEO returned a different search context.")
    raw = {"schema": SERP_SCHEMA, "engine": "Bing", "source": "DataForSEO Bing SERP",
           "observed_at": observed, "context": {"query": query,
               "market": "location_code:" + str(body["location_code"]), "language": body["language_code"],
               "device": body["device"], "depth": body["depth"]},
           "organic": [], "related": [], "questions": [], "features": [],
           "total_results_estimate": numeric(page.get("se_results_count"), integer_only=True)}
    rows = page.get("items")
    if rows is None and page.get("items_count") == 0:
        rows = []
    for row in array(rows):
        r = obj(row)
        kind = phrase(r.get("type"))
        if kind == "organic" and r.get("is_paid") is not True:
            raw["organic"].append({"rank": r.get("rank_group"), "url": r.get("url"),
                                   "title": r.get("title"), "snippet": r.get("description"),
                                   "absolute_feature_rank": r.get("rank_absolute")})
        else:
            raw["features"].append(kind)
            if kind == "related_searches":
                raw["related"].extend(phrase(x) for x in array(r.get("items", [])))
            elif kind == "people_also_ask":
                raw["questions"].extend(phrase(obj(x).get("title")) for x in array(r.get("items", [])))
    # Do not infer completeness from result counts or a full requested depth.
    return validate_serp(raw), meta


def _monthly(value: Any) -> list[dict]:
    result, seen = [], set()
    for r in array([] if value is None else value):
        r = obj(r)
        year, month = numeric(r.get("year"), integer_only=True), numeric(r.get("month"), integer_only=True)
        if year is None or not 1900 <= year <= 2200 or month is None or not 1 <= month <= 12:
            raise OnlineSourceError("Invalid monthly Bing volume bucket.")
        if (year, month) in seen:
            raise OnlineSourceError("Duplicate monthly Bing volume bucket.")
        seen.add((year, month))
        result.append({"year": year, "month": month,
                       "search_volume": numeric(r.get("search_volume"), integer_only=True)})
    return result


def dataforseo_keywords(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o, op = request.options, request.operation
    language = code(o, "language", "en", r"[A-Za-z-]{2,12}")
    if op == "url-ideas":
        body = {"target": url(text(o, "target")), "language_code": language,
                "exclude_brands": boolean(o, "exclude_brands")}
        if len(body["target"]) > 2000:
            raise ConfigurationError("URL ideas target must be at most 2000 characters.")
        rows, meta = dfs_call(http, "keywords_data/bing/keyword_suggestions_for_url/live", body, o)
        items = [candidate("DataForSEO Bing URL suggestions", obj(r).get("keyword"), {
            "provider_match_confidence": (numeric(r.get("confidence_score"), high=1), "fraction_0_1")},
            observed_at=observed, metadata={"target": body["target"], "language": language},
            note="Provider keyword-match confidence, not search volume or organic ranking probability.") for r in rows]
        return items, {**meta, "language": language}, []
    location = integer(o, "location_code", 0, 1, 100000000)
    if op == "competitor-keywords":
        if location != 2840:
            raise ConfigurationError("The documented Bing Labs ranked-keyword route supports US location 2840 only.")
        body = {"target": target_host(text(o, "target")), "location_code": location,
                "language_code": language, "limit": integer(o, "limit", 100, 1, 1000),
                "offset": integer(o, "offset", 0, 0, 1000000),
                "filters": ["ranked_serp_element.serp_item.type", "=", "organic"]}
        results, meta = dfs_call(http, "dataforseo_labs/bing/ranked_keywords/live", body, o)
        if len(results) != 1:
            raise OnlineSourceError("Expected one Bing Labs result.")
        data = obj(results[0])
        if data.get("se_type") not in (None, "bing"):
            raise OnlineSourceError("Expected Bing Labs data, not another search engine.")
        for key in ("target", "location_code", "language_code"):
            if key in data and data[key] != body[key]:
                raise OnlineSourceError("Bing Labs returned a different target or database context.")
        rows = data.get("items")
        if rows is None and data.get("items_count") == 0:
            rows = []
        items = []
        for raw in array(rows):
            r = obj(raw)
            kw = obj(r.get("keyword_data"))
            ranked = obj(r.get("ranked_serp_element"))
            position = obj(ranked.get("serp_item"))
            if position.get("type") != "organic" or position.get("is_paid") is True:
                continue
            info = obj(kw.get("keyword_info") or {})
            prop = obj(kw.get("keyword_properties") or {})
            items.append(candidate("DataForSEO Bing Labs", kw.get("keyword"), {
                "observed_organic_rank": (numeric(position.get("rank_group"), integer_only=True), "position_1_based"),
                "organic_difficulty": (numeric(prop.get("keyword_difficulty"), high=100), "provider_index_0_100"),
                "provider_search_volume": (numeric(info.get("search_volume"), integer_only=True), "provider_monthly_search_count"),
                "paid_competition": (numeric(info.get("competition"), high=1), "provider_index_0_1"),
                "cpc": (numeric(info.get("cpc")), "USD")}, observed_at=observed, geography=str(location),
                metadata={"target": body["target"], "url": url(position.get("url")),
                          "language": language, "monthly_searches": _monthly(info.get("monthly_searches")),
                          "metrics_updated_at": info.get("last_updated_time"),
                          "serp_updated_at": ranked.get("last_updated_time")},
                note="Provider database observations, not a fresh complete inventory or guaranteed ranking difficulty."))
        total = numeric(data.get("total_count"), integer_only=True)
        continuation = body["offset"] + len(rows)
        more = bool(rows) and (total is None or continuation < total)
        return items, {**meta, "total_count": total, "more_available": more,
                       "next_offset": continuation if more else None, "target": body["target"],
                       "database_location_code": location}, []
    maximum = 200 if op == "ideas" else 1000
    words = seeds(request, maximum)
    if any(len(w) > 100 for w in words):
        raise ConfigurationError("DataForSEO Bing keywords must be at most 100 characters each.")
    body = {"keywords": words, "location_code": location, "language_code": language,
            "search_partners": boolean(o, "search_partners")}
    if "device" in o:
        body["device"] = choice(o, "device", "desktop", ("desktop", "mobile", "tablet"))
    endpoint = "keywords_for_keywords" if op == "ideas" else "search_volume"
    rows, meta = dfs_call(http, "keywords_data/bing/" + endpoint + "/live", body, o)
    items = []
    for raw in rows:
        r = obj(raw)
        word = phrase(r.get("keyword"))
        for k in ("location_code", "language_code", "search_partners", "device"):
            if k in r and k in body and r[k] != body[k]:
                raise OnlineSourceError("Bing planning metrics returned a different targeting context.")
        items.append(candidate("DataForSEO Bing Ads", word, {
            "last_month_search_volume": (numeric(r.get("search_volume"), integer_only=True), "searches_last_month"),
            "cpc": (numeric(r.get("cpc")), "USD"),
            "paid_competition": (numeric(r.get("competition"), high=1), "provider_index_0_1")},
            observed_at=observed, geography=str(location), relationship="planning-estimate",
            metadata={"language": language, "search_partners": body["search_partners"],
                      "device": body.get("device"), "monthly_searches": _monthly(r.get("monthly_searches"))},
            note="Bing Ads planning data. search_volume is the last month's count, not an averaged monthly volume. "
                 "Paid competition is not organic difficulty."))
    present = {i.phrase.casefold() for i in items}
    return items, {**meta, "location_code": location, "language": language,
                   "search_partners": body["search_partners"],
                   "unreturned_input_terms": [w for w in words if w.casefold() not in present] if op == "metrics" else []}, []


def keywordtool(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    from .commercial import KeywordToolPlugin
    o = dict(request.options)
    # Prevent cross-engine defaults: the existing standalone provider defaults to Google.
    o.pop("provider", None)
    o["platform"] = "bing"
    class Capture:
        payload = None

        def json(self, *args, **kwargs):
            self.payload = http.json(*args, **kwargs)
            return self.payload

    capture = Capture()
    items, metadata, notes = KeywordToolPlugin().fetch(replace(request, options=o), capture, observed)
    for item in items:
        for evidence in item.evidence:
            numeric(evidence.value, high=1 if evidence.metric == "paid_competition" else None)
    payload = capture.payload or {}
    breakdown = payload.get("device_breakdown")
    if breakdown is not None:
        devices = {}
        for device, fields in obj(breakdown).items():
            if device not in {"desktop", "mobile", "tablet"}:
                raise OnlineSourceError("Unrecognised Keyword Tool device dimension.")
            fields = obj(fields)
            devices[device] = {"percentage": numeric(fields.get("percentage"), high=100),
                               "count": numeric(fields.get("count"), integer_only=True)}
        metadata["aggregate_device_breakdown"] = devices
        notes.append("Device breakdown covers the whole provider request, not each individual keyword.")
    records = list(obj(payload.get("results", {})).values())
    for i, raw in enumerate(records):
        raw = obj(raw)
        extensions = {}
        if "trend" in raw:
            extensions["provider_trend"] = number(raw["trend"])
        if "close_variants" in raw:
            extensions["close_variants"] = [phrase(v) for v in array(raw["close_variants"])]
        if raw.get("close_variants_source") is not None:
            extensions["close_variants_source"] = phrase(raw["close_variants_source"])
        items[i] = replace(items[i], metadata={**items[i].metadata, **extensions})
    metadata["engine"] = "Bing"
    return items, metadata, notes


def autocomplete(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    from .websites import check_robots
    o = request.options
    if not boolean(o, "allow_unofficial"):
        raise ConfigurationError("Browser autocomplete requires allow_unofficial=true; use suggestions for a provider API.")
    seed = seeds(request)[0]
    mkt = market(o)
    expand = choice(o, "expand", "none", ("none", "alphabet", "questions", "prepositions"))
    probes = [seed]
    if expand == "alphabet":
        probes.extend(seed + " " + c for c in string.ascii_lowercase)
    elif expand == "questions":
        probes.extend(c + " " + seed for c in ("how", "what", "why", "where", "when", "which"))
    elif expand == "prepositions":
        probes.extend(seed + " " + c for c in ("for", "with", "without", "near", "versus"))
    offset = integer(o, "probe_offset", 0, 0, len(probes) - 1)
    maximum = integer(o, "max_queries", 1, 1, 10)
    planned = probes[offset:offset + maximum]
    if len(planned) * 2 > http.maximum:
        raise ConfigurationError("Reserve two max_requests slots per probe (robots policy plus suggestions).")
    endpoint = "https://api.bing.com/osjson.aspx"
    items = []
    for probe in planned:
        from urllib.parse import urlencode
        check_robots(http, endpoint + "?" + urlencode({"query": probe, "mkt": mkt}))
        payload = array(http.json("GET", endpoint, params={"query": probe, "mkt": mkt}))
        if len(payload) < 2 or payload[0] != probe:
            raise OnlineSourceError("Bing autocomplete response did not echo the requested probe.")
        for rank, word in enumerate(array(payload[1]), 1):
            items.append(candidate("Bing browser autocomplete (experimental)", word,
                                   {"suggestion_position": (rank, "ordinal")}, observed_at=observed,
                                   geography=mkt, relationship="autocomplete-suggestion",
                                   metadata={"seed": seed, "probe": probe},
                                   note="Suggestion order is not search volume. Browser endpoints may change or block requests."))
    next_offset = offset + len(planned)
    return items, {"market": mkt, "queried_probes": planned,
                   "next_probe_offset": next_offset if next_offset < len(probes) else None,
                   "probes_not_queried": probes[next_offset:]}, []
