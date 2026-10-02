"""Documented commercial APIs: explicit accounts, geography and source-specific metrics."""
from __future__ import annotations

import base64
import csv
import io
from dataclasses import replace

from ..models import PluginDescriptor
from .common import (
    ConfigurationError,
    OnlinePlugin,
    OnlineSourceError,
    array,
    boolean,
    candidate,
    choice,
    code,
    integer,
    number,
    obj,
    provider_error,
    secret,
    seeds,
    text,
)


class DataForSEOPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("dataforseo", "DataForSEO Labs Google ideas, suggestions and keyword metrics.",
                                  ("discover", "analyse", "official-api"), ("ideas", "suggestions", "metrics"))
    hosts = ("api.dataforseo.com",)

    def fetch(self, request, http, observed):
        o = request.options
        terms = seeds(request, 1 if request.operation == "suggestions" else 100)
        location = integer(o, "location_code", 0, 1, 999999)
        language = code(o, "language", "en", r"[a-z]{2}(-[A-Z]{2})?")
        limit = integer(o, "limit", 50, 1, 1000)
        endpoints = {"ideas": "keyword_ideas", "suggestions": "keyword_suggestions", "metrics": "keyword_overview"}
        body = {"location_code": location, "language_code": language}
        body["keyword" if request.operation == "suggestions" else "keywords"] = terms[0] if request.operation == "suggestions" else terms
        if request.operation != "metrics":
            body.update(limit=limit, offset=integer(o, "offset", 0, 0, 1000000))
        credentials = secret(o, "login", "DATAFORSEO_LOGIN") + ":" + secret(o, "password", "DATAFORSEO_PASSWORD")
        headers = {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}
        data = obj(http.json("POST", f"https://api.dataforseo.com/v3/dataforseo_labs/google/{endpoints[request.operation]}/live",
                            headers=headers, json=[body]))
        if data.get("status_code") != 20000:
            raise OnlineSourceError("DataForSEO rejected the request; check API access, credits and parameters.")
        tasks = array(data.get("tasks"))
        if len(tasks) != 1 or obj(tasks[0]).get("status_code") != 20000:
            raise OnlineSourceError("DataForSEO task did not succeed; no partial data was accepted.")
        results = array(tasks[0].get("result") or [])
        items, total = [], None
        for result in results:
            result = obj(result)
            total = result.get("total_count", total)
            for row in array(result.get("items") or []):
                row = obj(row)
                info = obj(row.get("keyword_info") or {})
                properties = obj(row.get("keyword_properties") or {})
                items.append(candidate("DataForSEO Labs Google", row.get("keyword"), {
                    "estimated_search_volume": (number(info.get("search_volume")), "searches_per_month"),
                    "cpc": (number(info.get("cpc")), "USD"),
                    "paid_competition": (number(info.get("competition")), "index_0_1"),
                    "organic_keyword_difficulty": (number(properties.get("keyword_difficulty")), "index_0_100"),
                }, observed_at=observed, geography=str(location),
                    metadata={"language": language, "provider_updated_at": info.get("last_updated_time"),
                              "monthly_searches": info.get("monthly_searches", [])},
                    note="Provider estimates; paid-ad competition and organic difficulty are different measures."))
        return items, {"location_code": location, "language": language, "total_count": total,
                       "reported_cost": number(data.get("cost"))}, ["This paid live endpoint may charge for every request, including repeated requests."]


class SemrushPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("semrush", "Semrush related/broad-match keywords and single-keyword overview.",
                                  ("discover", "analyse", "official-api"), ("related", "broad-match", "metrics"))
    hosts = ("api.semrush.com",)

    def fetch(self, request, http, observed):
        o = request.options
        seed = seeds(request)[0]
        database = code(o, "database", pattern=r"[a-z]{2}")
        report = {"related": "phrase_related", "broad-match": "phrase_fullsearch", "metrics": "phrase_this"}[request.operation]
        params = {"type": report, "key": secret(o, "api_key", "SEMRUSH_API_KEY"),
                  "phrase": seed, "database": database,
                  "display_limit": integer(o, "limit", 50, 1, 1000),
                  "export_columns": "Ph,Nq,Cp,Co", "export_decode": 1, "export_escape": 1}
        _, data = http.request("GET", "https://api.semrush.com/", params=params)
        if data.strip().startswith("ERROR 50"):
            return [], {"database": database}, ["Semrush reported no matching data; this is not evidence of zero demand."]
        if data.lstrip().startswith("ERROR"):
            raise OnlineSourceError("Semrush reported an API error; check API units, database and subscription.")
        reader = csv.DictReader(io.StringIO(data), delimiter=";")
        required = {"Keyword", "Search Volume", "CPC", "Competition"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise OnlineSourceError("Semrush returned an unexpected CSV header or a website challenge.")
        items = []
        for row in reader:
            if None in row or any(row[k] is None for k in required):
                raise OnlineSourceError("Semrush returned a malformed CSV record.")
            items.append(candidate("Semrush Analytics API", row["Keyword"], {
                "estimated_search_volume": (number(row["Search Volume"]), "searches_per_month"),
                "cpc": (number(row["CPC"]), "USD"),
                "paid_competition": (number(row["Competition"]), "index_0_1"),
            }, observed_at=observed, geography=database, note="Database-specific provider estimates, not exact observed demand."))
        return items, {"database": database, "report": report}, ["A single bounded report is requested; this plugin does not auto-page or auto-retry paid calls."]


class AhrefsPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("ahrefs", "Ahrefs Keywords Explorer matching terms and question keywords.",
                                  ("discover", "analyse", "official-api"), ("matching-terms", "questions"))
    hosts = ("api.ahrefs.com",)

    def fetch(self, request, http, observed):
        o = request.options
        keywords = seeds(request, 20)
        if any("," in word for word in keywords):
            raise ConfigurationError("Ahrefs keywords cannot contain commas (the API uses comma-separated input).")
        country = code(o, "country").lower()
        params = {"keywords": ",".join(keywords), "country": country,
                  "select": "keyword,volume,difficulty,cpc", "output": "json",
                  "limit": integer(o, "limit", 50, 1, 1000),
                  "terms": "questions" if request.operation == "questions" else "all",
                  "match_mode": choice(o, "match_mode", "terms", ("terms", "phrase"))}
        headers = {"Authorization": "Bearer " + secret(o, "api_key", "AHREFS_API_KEY")}
        data = provider_error(http.json("GET", "https://api.ahrefs.com/v3/keywords-explorer/matching-terms", params=params, headers=headers))
        items = []
        for row in array(data.get("keywords")):
            row = obj(row)
            items.append(candidate("Ahrefs Keywords Explorer", row.get("keyword"), {
                "estimated_search_volume": (number(row.get("volume")), "searches_per_month"),
                "organic_keyword_difficulty": (number(row.get("difficulty")), "index_0_100"),
                "cpc": (number(row.get("cpc")), "USD_cents"),
            }, observed_at=observed, geography=country,
                note="Ahrefs estimates; CPC is preserved in cents, not dollars."))
        return items, {"country": country}, ["API access and units depend on the Ahrefs account. No website fallback is attempted."]


class KeywordToolPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("keywordtool", "Keyword Tool v2 multi-platform suggestions and volume metrics.",
                                  ("discover", "analyse", "official-api"), ("suggestions", "metrics"))
    hosts = ("api.keywordtool.io",)
    interval = 4.0
    platforms = ("google", "bing", "youtube", "perplexity", "amazon", "ebay", "app-store",
                 "play-store", "instagram", "twitter", "reddit", "pinterest", "etsy", "tiktok", "naver",
                 "google-trends")
    suggestion_types = {
        "google": ("suggestions", "questions", "prepositions", "related"),
        "bing": ("suggestions", "questions", "prepositions", "related"),
        "youtube": ("suggestions", "questions", "prepositions", "hashtags"),
        "perplexity": ("suggestions", "questions", "prepositions"),
        "amazon": ("suggestions", "prepositions"), "ebay": ("suggestions", "prepositions"),
        "instagram": ("hashtags", "people"), "twitter": ("suggestions", "hashtags"),
        "reddit": ("suggestions", "communities", "profiles"),
        "google-trends": ("top", "rising"),
    }

    def fetch(self, request, http, observed):
        o = request.options
        platform = choice(o, "platform", "google", self.platforms)
        key = secret(o, "api_key", "KEYWORDTOOL_API_KEY")
        terms = seeds(request, 100 if request.operation == "metrics" else 1)
        if any(len(term) > 80 or len(term.split()) > 10 for term in terms):
            raise ConfigurationError("Keyword Tool keywords must be at most 80 characters and 10 words.")
        metrics = request.operation == "metrics" or boolean(o, "metrics")
        if platform == "google-trends" and metrics:
            raise ConfigurationError("Keyword Tool's Google Trends route supports suggestions, not volume metrics.")
        body = {"apikey": key, "keyword": terms if request.operation == "metrics" else terms[0],
                "output": "json"}
        country, metric_geo = None, None
        language = code(o, "language", "en")
        currency = code(o, "currency", "USD", r"[A-Za-z]{3}").upper()
        if request.operation == "suggestions" or platform not in {"google", "bing"}:
            country = code(o, "country").upper()
            body.update(country=country, language=language)
        if request.operation == "suggestions":
            kinds = self.suggestion_types.get(platform, ("suggestions",))
            body.update(metrics=metrics, type=choice(o, "suggestion_type", kinds[0], kinds))
        if metrics:
            body["metrics_currency"] = currency
            if platform in {"google", "bing"}:
                location = integer(o, "location_code", 0, 1, 9999999)
                body.update(metrics_location=[location], metrics_language=[language])
                metric_geo = f"{platform}:location:{location}"
                networks = (("googlesearchnetwork", "googlesearch") if platform == "google" else
                            ("ownedandoperatedandsyndicatedsearch", "ownedandoperatedonly", "syndicatedsearchonly"))
                body["metrics_network"] = choice(o, "network", networks[0], networks)
                if platform == "bing":
                    body["metrics_source"] = choice(o, "metrics_source", "keyword_planner", ("keyword_planner", "historical_data"))
            else:
                metric_geo = country
        if "category" in o:
            body["category"] = text(o, "category")
        version = "v2-sandbox" if boolean(o, "sandbox") else "v2"
        route = "volume" if request.operation == "metrics" else "suggestions"
        data = provider_error(http.json("POST", f"https://api.keywordtool.io/{version}/search/{route}/{platform}", json=body))
        results = obj(data.get("results"))
        items = []
        for index, (word, row) in enumerate(results.items(), 1):
            row = obj(row)
            values = {"returned_order": (index, "ordinal")}
            if metrics:
                values.update(estimated_search_volume=(number(row.get("volume")), "searches_per_month"),
                              cpc=(number(row.get("cpc")), currency),
                              paid_competition=(number(row.get("cmp")), "index_0_1"))
            monthly = {k: v for k, v in row.items() if k.startswith("m") and k[1:2].isdigit()}
            item = candidate("Keyword Tool API", row.get("string", word), values,
                             observed_at=observed, geography=metric_geo,
                             metadata={"platform": platform, "monthly_metrics": monthly,
                                       "suggestion_country": country, "metrics_geography": metric_geo,
                                       "metrics_network": body.get("metrics_network"),
                                       "metrics_source": body.get("metrics_source"), "language": language},
                             note="Returned order is not demand. Metrics use their own explicit geography and provider scope.")
            item = replace(item, evidence=tuple(replace(e, geography=country)
                                                if e.metric == "returned_order" else e
                                                for e in item.evidence))
            items.append(item)
        notice = data.get("notice")
        meta = {"platform": platform, "total_keywords": data.get("total_keywords"),
                "sandbox": boolean(o, "sandbox"), "partial_notice": bool(notice)}
        if notice:
            meta["completeness"] = "partial provider response; missing metrics remain null"
        return items, meta, ["The local output limit is not a billing cap: this API may return/charge for a larger result set."]


class KeywordsEverywherePlugin(OnlinePlugin):
    descriptor = PluginDescriptor("keywords-everywhere", "Keywords Everywhere batch volume, CPC and competition data.",
                                  ("analyse", "official-api"), ("metrics",))
    hosts = ("api.keywordseverywhere.com",)

    def fetch(self, request, http, observed):
        o = request.options
        terms = seeds(request, 100)
        country = code(o, "country").lower()
        currency = code(o, "currency", "usd", r"[A-Za-z]{3}").lower()
        headers = {"Authorization": "Bearer " + secret(o, "api_key", "KEYWORDS_EVERYWHERE_API_KEY")}
        data = provider_error(http.json("POST", "https://api.keywordseverywhere.com/v1/get_keyword_data",
                                        headers=headers, data={"dataSource": "gkp", "country": country,
                                                              "currency": currency, "kw[]": terms}))
        items = []
        for row in array(data.get("data")):
            row = obj(row)
            cpc = obj(row.get("cpc") or {})
            items.append(candidate("Keywords Everywhere", row.get("keyword"), {
                "estimated_search_volume": (number(row.get("vol")), "searches_per_month"),
                "cpc": (number(cpc.get("value")), currency.upper()),
                "paid_competition": (number(row.get("competition")), "index_0_1"),
            }, observed_at=observed, geography=country, metadata={"trend": row.get("trend", [])},
                note="Historical provider planning data; not organic SEO difficulty."))
        return items, {"country": country, "credits_consumed": number(data.get("credits_consumed"))}, []
