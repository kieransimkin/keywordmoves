"""Google's first-party keyword planning and property performance APIs."""
from __future__ import annotations

from urllib.parse import quote

from ..models import PluginDescriptor
from .common import (
    ConfigurationError,
    OnlinePlugin,
    OnlineSourceError,
    array,
    candidate,
    code,
    integer,
    iso_date,
    number,
    obj,
    provider_error,
    secret,
    seeds,
    text,
)


class GoogleAdsPlugin(OnlinePlugin):
    descriptor = PluginDescriptor(
        "google-ads", "Google Ads keyword ideas and historical planning metrics (account required).",
        ("discover", "analyse", "official-api"), ("ideas", "metrics"),
    )
    hosts = ("googleads.googleapis.com",)

    def fetch(self, request, http, observed):
        options = request.options
        keywords = seeds(request, 20 if request.operation == "ideas" else 100)
        customer = text(options, "customer_id").replace("-", "")
        if not customer.isdigit():
            raise ConfigurationError("customer_id must be numeric, optionally hyphenated.")
        version = code(options, "api_version", "v25", r"v[0-9]+")
        locations = text(options, "location_codes").split(",")
        if not all(v.strip().isdigit() for v in locations) or len(locations) > 10:
            raise ConfigurationError("location_codes must contain up to 10 comma-separated numeric IDs.")
        locations = ["geoTargetConstants/" + v.strip() for v in locations]
        language = code(options, "language_id", "1000", r"[0-9]+")
        headers = {
            "Authorization": "Bearer " + secret(options, "access_token", "GOOGLE_ADS_ACCESS_TOKEN"),
            "developer-token": secret(options, "developer_token", "GOOGLE_ADS_DEVELOPER_TOKEN"),
        }
        if "login_customer_id" in options:
            login = text(options, "login_customer_id").replace("-", "")
            if not login.isdigit():
                raise ConfigurationError("login_customer_id must be numeric.")
            headers["login-customer-id"] = login
        common = {"language": "languageConstants/" + language,
                  "geoTargetConstants": locations, "keywordPlanNetwork": "GOOGLE_SEARCH"}
        limit = integer(options, "limit", 50, 1, 1000)
        page_size = integer(options, "page_size", limit, 1, 1000)
        pages = integer(options, "max_pages", 1, 1, 10)
        suffix = "generateKeywordIdeas" if request.operation == "ideas" else "generateKeywordHistoricalMetrics"
        url = f"https://{self.hosts[0]}/{version}/customers/{customer}:{suffix}"
        items, token = [], options.get("page_token")
        if token is not None:
            token = text(options, "page_token")
        for _ in range(pages if request.operation == "ideas" else 1):
            body = dict(common)
            if request.operation == "ideas":
                body.update(keywordSeed={"keywords": keywords}, pageSize=page_size)
                if token:
                    body["pageToken"] = token
            else:
                body["keywords"] = keywords
            data = provider_error(http.json("POST", url, headers=headers, json=body))
            for row in array(data.get("results", [])):
                row = obj(row)
                metric_key = "keywordIdeaMetrics" if request.operation == "ideas" else "keywordMetrics"
                metrics = obj(row.get(metric_key) or {})
                items.append(candidate("Google Ads API", row.get("text"), {
                    "avg_monthly_searches": (number(metrics.get("avgMonthlySearches")), "searches_per_month"),
                    "paid_competition": (metrics.get("competition"), "enum"),
                    "paid_competition_index": (number(metrics.get("competitionIndex")), "index_0_100"),
                    "low_top_of_page_bid": (number(metrics.get("lowTopOfPageBidMicros")), "account_currency_micros"),
                    "high_top_of_page_bid": (number(metrics.get("highTopOfPageBidMicros")), "account_currency_micros"),
                }, observed_at=observed, geography=",".join(locations),
                    metadata={"monthly_search_volumes": metrics.get("monthlySearchVolumes", []),
                              "close_variants": row.get("closeVariants", []), "language_id": language},
                    note="Planning estimates and close-variant aggregation, not exact demand or organic difficulty."))
            token = data.get("nextPageToken")
            if token is not None and not isinstance(token, str):
                raise OnlineSourceError("Google Ads returned an invalid pagination token.")
            if not token or len(items) >= limit:
                break
        return items, {"api_version": version, "next_page_token": token,
                       "language_id": language, "location_codes": locations}, [
            "Uses a read-only planning operation; no campaigns are created.",
            "OAuth access tokens must be renewed externally. Bid units use the Ads account currency.",
        ]


class SearchConsolePlugin(OnlinePlugin):
    descriptor = PluginDescriptor(
        "search-console", "Query clicks, impressions and positions for an authorised Search Console property.",
        ("analyse", "first-party", "official-api"), ("queries",),
    )
    hosts = ("www.googleapis.com",)

    def fetch(self, request, http, observed):
        options = request.options
        site = text(options, "site_url")
        if not (site.startswith("https://") or site.startswith("http://") or site.startswith("sc-domain:")):
            raise ConfigurationError("site_url must identify an exact URL-prefix or sc-domain property.")
        start, end = iso_date(options, "start_date"), iso_date(options, "end_date")
        if start > end:
            raise ConfigurationError("start_date must be on or before end_date.")
        from .common import choice

        search_type = choice(options, "search_type", "web", ("web", "image", "video", "news"))
        limit = integer(options, "limit", 50, 1, 1000)
        page_size = integer(options, "page_size", limit, 1, 1000)
        offset = integer(options, "start_row", 0, 0, 1_000_000)
        pages = integer(options, "max_pages", 1, 1, 10)
        headers = {"Authorization": "Bearer " + secret(options, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN")}
        body = {"startDate": start, "endDate": end, "dimensions": ["query"],
                "type": search_type, "dataState": "final"}
        filters = []
        if request.keywords:
            filters.append({"dimension": "query", "operator": "contains", "expression": seeds(request)[0]})
        country = None
        if "country" in options:
            country = code(options, "country", pattern=r"[A-Za-z]{3}").lower()
            filters.append({"dimension": "country", "operator": "equals", "expression": country})
        if filters:
            body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
        url = f"https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}/searchAnalytics/query"
        items, more = [], False
        for _ in range(pages):
            data = provider_error(http.json("POST", url, headers=headers,
                                            json={**body, "rowLimit": page_size, "startRow": offset}))
            rows = array(data.get("rows", []))
            for row in rows:
                row = obj(row)
                keys = array(row.get("keys"))
                if len(keys) != 1:
                    raise OnlineSourceError("Search Console returned unexpected query dimensions.")
                items.append(candidate("Google Search Console", keys[0], {
                    "clicks": (number(row.get("clicks")), "count"),
                    "impressions": (number(row.get("impressions")), "count"),
                    "ctr": (number(row.get("ctr")), "fraction_0_1"),
                    "average_position": (number(row.get("position")), "position"),
                }, observed_at=observed, geography=country, relationship="observed-query",
                    metadata={"site_url": site, "start_date": start, "end_date": end,
                              "date_timezone": "America/Los_Angeles", "search_type": search_type},
                    note="Performance on the authorised property, not total market searches."))
            offset += len(rows)
            more = len(rows) == page_size
            if not more or len(items) >= limit:
                break
        return items, {"site_url": site, "start_date": start, "end_date": end,
                       "next_start_row": offset if more else None}, [
            "Dates use Search Console's Pacific-time boundaries; anonymised queries and other rows may be omitted.",
            "Pagination does not make this API an exhaustive query export.",
        ]
