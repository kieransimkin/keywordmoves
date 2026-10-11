"""Additional documented databases and topical attention, with separate units."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from ..errors import ConfigurationError
from ..models import PluginDescriptor
from .common import (
    OnlinePlugin,
    OnlineSourceError,
    array,
    boolean,
    candidate,
    choice,
    code,
    integer,
    iso_date,
    number,
    obj,
    provider_error,
    secret,
    seeds,
)


class SERankingPlugin(OnlinePlugin):
    descriptor = PluginDescriptor(
        "seranking", "SE Ranking regional keyword estimates, related terms and questions.",
        ("discover", "metrics", "official-api", "paid"),
        ("metrics", "related", "similar", "questions", "longtail"),
    )
    hosts = ("api.seranking.com",)

    def fetch(self, request, http, observed):
        o = request.options
        if not boolean(o, "paid_requests_approved"):
            raise ConfigurationError("SE Ranking requires paid_requests_approved=true after exact-request approval.")
        terms = seeds(request, 1000 if request.operation == "metrics" else 1)
        limit = integer(o, "limit", 20, 1, 1000)
        credits = 100 if request.operation == "metrics" else (1 if request.operation == "longtail" else 10) * limit
        ceiling = integer(o, "approved_max_credits", 0, 1, 100000)
        if credits > ceiling:
            raise ConfigurationError("The documented request credit bound exceeds approved_max_credits.")
        source = code(o, "source").lower()
        headers = {"Authorization": "Token " + secret(o, "api_key", "SERANKING_API_KEY")}
        if request.operation == "metrics":
            rows = array(http.json("POST", "https://api.seranking.com/v1/keywords/export",
                                   params={"source": source}, json={"keywords": terms}, headers=headers))
            total = len(rows)
        else:
            params = {"source": source, "keyword": terms[0], "limit": limit, "offset": 0}
            if request.operation != "longtail":
                params.update({"history_trend": str(boolean(o, "history_trend")).lower(),
                               "sort": choice(o, "sort", "volume", ("keyword", "volume", "cpc", "difficulty", "competition")),
                               "sort_order": choice(o, "sort_order", "desc", ("asc", "desc"))})
            data = provider_error(http.json(
                "GET", "https://api.seranking.com/v1/keywords/" + request.operation,
                params=params, headers=headers))
            rows = array(data.get("keywords"))
            total = number(data.get("total"))
        items = []
        for raw in rows:
            if request.operation == "longtail":
                items.append(candidate("SE Ranking", raw, {}, observed_at=observed,
                                       geography=source, relationship="long-tail-suggestion",
                                       metadata={"seed": terms[0], "platform": "Google Search", "database": source,
                                                 "availability": "language-only"},
                                       note="Long-tail string only; no volume or difficulty returned by this endpoint."))
                continue
            row = obj(raw)
            found = row.get("is_data_found", True)
            if not isinstance(found, bool):
                raise OnlineSourceError("SE Ranking is_data_found must be boolean.")
            metrics = {
                "estimated_monthly_searches": (number(row.get("volume")) if found else None, "searches_per_month"),
                "paid_competition": (number(row.get("competition")) if found else None, "index_0_1"),
                "organic_difficulty": (number(row.get("difficulty")) if found else None, "provider_index_0_100"),
            }
            # Currency is not assumed from a country code; retain it as provider-native.
            if row.get("cpc") is not None and found:
                metrics["cpc"] = (number(row["cpc"]), "provider_currency_unspecified")
            if row.get("relevance") is not None and found:
                metrics["related_relevance"] = (number(row["relevance"]), "provider_score")
            for metric, (value, _unit) in metrics.items():
                maximum = {"paid_competition": 1, "organic_difficulty": 100}.get(metric)
                if value is not None and (value < 0 or (maximum is not None and value > maximum)):
                    raise OnlineSourceError("SE Ranking returned an out-of-range metric.")
            items.append(candidate("SE Ranking", row.get("keyword"), metrics,
                                   observed_at=observed, geography=source,
                                   relationship=request.operation,
                                   metadata={"seed": terms[0] if len(terms) == 1 else None,
                                             "platform": "Google Search", "database": source,
                                             "availability": "observed" if found else "unavailable",
                                             "intents": row.get("intents"), "history_trend": row.get("history_trend")},
                                   note="Third-party estimates; paid competition and organic difficulty are separate. Not YouTube demand."))
        return items, {"database": source, "total_reported": total,
                       "documented_request_credit_bound": credits, "approved_max_credits": ceiling,
                       "billing_cap_enforced_by_provider": False}, [
            "One request only; no automatic pagination, retries or provider switching.",
            "The local credit guard uses the documented tariff; it is not a provider-enforced account spending cap.",
        ]


class WikimediaPageviewsPlugin(OnlinePlugin):
    descriptor = PluginDescriptor(
        "wikimedia-pageviews", "Human-classified Wikipedia article views as topical attention, not search volume.",
        ("topics", "attention", "official-api"), ("article",),
    )
    hosts = ("wikimedia.org",)

    def fetch(self, request, http, observed):
        o = request.options
        article = seeds(request)[0].replace(" ", "_")
        project = code(o, "project", "en.wikipedia.org", r"[a-z]{2,3}(?:-[a-z]+)?\.wikipedia\.org")
        start, end = iso_date(o, "start_date"), iso_date(o, "end_date")
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        if first < date(2015, 7, 1) or last > yesterday or first > last or (last-first).days > 365:
            raise ConfigurationError("Pageview window must be 1-366 past days starting on/after 2015-07-01.")
        access = choice(o, "access", "all-access", ("all-access", "desktop", "mobile-app", "mobile-web"))
        path = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/user/{quote(article, safe='')}/daily/{first:%Y%m%d}00/{last:%Y%m%d}00"
        data = provider_error(http.json("GET", path))
        rows = array(data.get("items"))
        series, seen = [], set()
        for raw in rows:
            row = obj(raw)
            stamp = str(row.get("timestamp", ""))
            try:
                day = datetime.strptime(stamp, "%Y%m%d%H").date()
            except ValueError:
                raise OnlineSourceError("Wikimedia returned an invalid daily timestamp.") from None
            views = number(row.get("views"))
            if not first <= day <= last or day in seen or views is None or views < 0 or int(views) != views:
                raise OnlineSourceError("Wikimedia returned an invalid, repeated or out-of-window daily value.")
            # AQS request domains include .org; its response project identifier omits it.
            if "project" in row and row["project"] not in (project, project.removesuffix(".org")):
                raise OnlineSourceError("Wikimedia returned a different project.")
            for key, expected in (("access", access), ("agent", "user"), ("article", article), ("granularity", "daily")):
                if key in row and row[key] != expected:
                    raise OnlineSourceError("Wikimedia returned a different measurement scope.")
            seen.add(day)
            series.append({"date": day.isoformat(), "views": views})
        expected_days = (last-first).days + 1
        complete = len(seen) == expected_days
        items = [candidate("Wikimedia Pageviews", article.replace("_", " "),
                           {"article_views": (sum(d["views"] for d in series) if rows else None, "pageviews")},
                           observed_at=observed, relationship="topical-attention",
                           metadata={"platform": "Wikipedia", "project": project, "access": access, "agent": "user",
                                     "period_start": start, "period_end": end, "daily_series": sorted(series, key=lambda x: x["date"]),
                                     "days_returned": len(seen), "days_expected": expected_days,
                                     "availability": "observed" if rows else "unavailable",
                                     "source_url": path, "complete_window": complete},
                           note="Article-reading attention. Not keyword searches, music-listening intent, UK demand or song reach; no missing-day zero imputation.")]
        return items, {"completeness": "complete window" if complete else "incomplete window; missing days unknown"}, [
            "Wikipedia language/project is not a geographic audience filter.",
            "Topical attention must not be described as song demand or used to chase unrelated current events.",
        ]


class ITunesCatalogPlugin(OnlinePlugin):
    descriptor = PluginDescriptor(
        "itunes-catalog", "Apple iTunes song-title and genre context, without popularity claims or media downloads.",
        ("discover", "music-catalogue", "official-api"), ("songs",),
    )
    hosts = ("itunes.apple.com",)
    interval = 3.1

    def fetch(self, request, http, observed):
        o = request.options
        country = code(o, "country").upper()
        seed = seeds(request)[0]
        limit = integer(o, "limit", 20, 1, 200)
        attribute = choice(o, "attribute", "songTerm", ("songTerm", "artistTerm"))
        data = provider_error(http.json("GET", "https://itunes.apple.com/search", params={
            "term": seed, "country": country, "media": "music", "entity": "song",
            "attribute": attribute, "limit": limit,
        }))
        rows = array(data.get("results"))
        count = number(data.get("resultCount"))
        if count != len(rows):
            raise OnlineSourceError("Apple resultCount does not match returned records.")
        items = []
        for raw in rows:
            row = obj(raw)
            if row.get("kind") != "song" or row.get("wrapperType") != "track":
                raise OnlineSourceError("Apple returned an unexpected catalogue entity.")
            items.append(candidate("Apple iTunes Search", row.get("trackName"), {},
                observed_at=observed, geography=f"storefront:{country}", relationship="catalogue-title",
                metadata={"platform": "Apple iTunes", "seed": seed,
                          "artist": row.get("artistName"), "collection": row.get("collectionName"),
                          "genre": row.get("primaryGenreName"), "track_id": row.get("trackId"),
                          "track_url": row.get("trackViewUrl"), "explicitness": row.get("trackExplicitness")},
                note="Catalogue title/genre context only. Storefront availability is not audience geography or search/listening demand."))
        return items, {"storefront": country, "attribute": attribute, "result_count": count}, [
            "No preview audio or artwork is downloaded or retained; promotional-media terms remain external.",
            "Apple documents approximately 20 requests per minute; coordinate callers across processes and cache results.",
            "Other artists' titles are context, not automatically suitable targets or affiliations.",
        ]
