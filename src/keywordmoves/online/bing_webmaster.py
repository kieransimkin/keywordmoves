"""Read-only Bing Webmaster API adapter, separate from retired Bing Search APIs.

Each method has its own published argument list. Top-report APIs do not accept
invented date/device filters or Search Console-style pagination parameters.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..models import PluginRequest
from .bing_search_analysis import (
    BWT_SCHEMA,
    bwt_candidates,
    native_date,
    numeric,
    period,
    url,
    validate_bwt,
)
from .common import (
    HTTP,
    ConfigurationError,
    OnlineSourceError,
    array,
    candidate,
    choice,
    code,
    integer,
    obj,
    phrase,
    secret,
    seeds,
    text,
)

# operation: (published method, native result kind, extra required arguments)
METHODS = {
    "bwt-sites": ("GetUserSites", "sites", ()),
    "bwt-queries": ("GetQueryStats", "queries", ("site_url",)),
    "bwt-pages": ("GetPageStats", "pages", ("site_url",)),
    "bwt-page-queries": ("GetPageQueryStats", "page-queries", ("site_url", "page_url")),
    "bwt-query-pages": ("GetQueryPageStats", "query-pages", ("site_url", "query")),
    "bwt-query-history": ("GetQueryTrafficStats", "query-history", ("site_url", "query")),
    "bwt-query-page-detail": ("GetQueryPageDetailStats", "detail", ("site_url", "query", "page_url")),
    "bwt-traffic": ("GetRankAndTrafficStats", "traffic", ("site_url",)),
    "bwt-keyword": ("GetKeyword", "keyword", ("q", "country", "language", "period")),
    "bwt-related": ("GetRelatedKeywords", "related", ("q", "country", "language", "period")),
    "bwt-keyword-history": ("GetKeywordStats", "keyword-history", ("q", "country", "language")),
    "bwt-links": ("GetLinkCounts", "links", ("site_url",)),
    "bwt-url-links": ("GetUrlLinks", "url-links", ("site_url", "link")),
    "bwt-sitemaps": ("GetFeeds", "sitemaps", ("site_url",)),
    "bwt-url-info": ("GetUrlInfo", "url-info", ("site_url", "url")),
    "bwt-crawl": ("GetCrawlStats", "crawl", ("site_url",)),
    "bwt-crawl-issues": ("GetCrawlIssues", "crawl-issues", ("site_url",)),
}
PERFORMANCE = {"queries", "pages", "page-queries", "query-pages", "query-history", "detail", "traffic"}
AUTH_OPTIONS = {"auth", "access_token", "api_key", "query_encoding"}


def arguments(request: PluginRequest) -> tuple[str, str, dict, dict]:
    o = request.options
    method, kind, required = METHODS[request.operation]
    args: dict[str, Any] = {}
    context = {"site_url": None, "query": None, "page": None}
    if request.keywords and not ({"query", "q"} & set(required)):
        raise ConfigurationError("This Webmaster operation does not accept --keyword.")
    for key in required:
        if key in {"query", "q"}:
            args[key] = seeds(request)[0]
            context["query"] = args[key]
        elif key == "site_url":
            args["siteUrl"] = url(text(o, "site_url"))
            context["site_url"] = args["siteUrl"]
        elif key == "page_url":
            args["page"] = url(text(o, "page_url"))
            context["page"] = args["page"]
        elif key == "period":
            start, end = period(o.get("start_date"), o.get("end_date"))
            args.update(startDate=start, endDate=end)
        elif key in {"country", "language"}:
            args[key] = code(o, key, pattern=r"[A-Za-z]{2}(?:-[A-Za-z]{2})?")
        elif key == "url":
            value = text(o, key)
            if value.startswith("domain:"):
                from .bing_search_analysis import target_host
                value = "domain:" + target_host(value[7:])
            else:
                value = url(value)
            args[key] = value
        else:
            args[key] = url(text(o, key))
    return method, kind, args, context


def _call(http: HTTP, method: str, args: Mapping[str, Any], o: Mapping[str, Any]) -> Any:
    auth = choice(o, "auth", "api-key", ("api-key", "oauth"))
    encoding = choice(o, "query_encoding", "documented", ("documented", "plain"))
    # Method examples use raw siteUrl, JSON-quoted query/page/url strings, and
    # ordinary numeric page indexes. Encoding is explicit, never retry-guessed.
    params = {k: json.dumps(v, ensure_ascii=False) if encoding == "documented" and
              isinstance(v, str) and k not in {"siteUrl", "startDate", "endDate"} else v
              for k, v in args.items()}
    headers = {"Accept": "application/json"}
    if auth == "oauth":
        headers["Authorization"] = "Bearer " + secret(o, "access_token", "BING_WEBMASTER_ACCESS_TOKEN")
        host = "www.bing.com"
    else:
        params["apikey"] = secret(o, "api_key", "BING_WEBMASTER_API_KEY")
        host = "ssl.bing.com"
    response = obj(http.json("GET", f"https://{host}/webmaster/api.svc/json/{method}",
                             params=params, headers=headers))
    if "ErrorCode" in response or response.get("error") or "d" not in response:
        raise OnlineSourceError("Bing Webmaster returned an API error or changed response envelope.")
    data = response["d"]
    if isinstance(data, dict) and ("ErrorCode" in data or data.get("error")):
        raise OnlineSourceError("Bing Webmaster returned an API error.")
    return data


def performance_report(data: Any, kind: str, context: Mapping[str, Any], observed: str) -> dict:
    rows = []
    for raw in array(data):
        r = obj(raw)
        query, page = context.get("query"), context.get("page")
        if kind in {"queries", "page-queries"}:
            query = phrase(r.get("Query"))
        elif kind in {"pages", "query-pages"}:
            # QueryStats is reused by Microsoft; here Query contains the page URL.
            # Reject a non-URL rather than misrepresent an ambiguous response.
            page = url(r.get("Query"))
        row = {"query": query, "page": page, "date": native_date(r.get("Date")),
               "position_bucket": numeric(r.get("Position"), integer_only=True) if kind == "detail" else None,
               "clicks": numeric(r.get("Clicks"), integer_only=True),
               "impressions": numeric(r.get("Impressions"), integer_only=True),
               "average_click_position": numeric(r.get("AvgClickPosition")),
               "average_impression_position": numeric(r.get("AvgImpressionPosition"))}
        if row["date"] is None:
            raise OnlineSourceError("Bing performance row has no usable native bucket date.")
        rows.append(row)
    return validate_bwt({"schema": BWT_SCHEMA, "engine": "Bing", "source": "Bing Webmaster API",
                         "observed_at": observed,
                         "context": {**context, "report_type": kind, "scope": "native-top-report:" + kind,
                                     "granularity": "native-buckets"}, "rows": rows})


_FIELDS = {
    "sites": {"Url": "url", "IsVerified": "bool"},
    "sitemaps": {"Url": "url", "Compressed": "bool", "FileSize": "count", "LastCrawled": "date",
                 "Submitted": "date", "Status": "scalar", "Type": "scalar", "UrlCount": "count"},
    "url-info": {"Url": "str", "AnchorCount": "count", "DiscoveryDate": "date", "DocumentSize": "count",
                 "HttpStatus": "count", "IsPage": "bool", "LastCrawledDate": "date", "TotalChildUrlCount": "count"},
    "crawl": {**{k: "count" for k in ("AllOtherCodes", "BlockedByRobotsTxt", "Code2xx", "Code301",
                                      "Code302", "Code4xx", "Code5xx", "ContainsMalware", "CrawlErrors",
                                      "CrawledPages", "InIndex", "InLinks")}, "Date": "date"},
    "crawl-issues": {"Url": "url", "HttpCode": "count", "Issues": "scalar", "InLinks": "count"},
    "links": {"Url": "url", "Count": "count"},
    "url-links": {"Url": "url", "AnchorText": "str"},
}


def _record(raw: Any, kind: str) -> dict:
    raw = obj(raw)
    clean = {}
    required = "Date" if kind == "crawl" else "Url"
    if raw.get(required) is None:
        raise OnlineSourceError("Bing diagnostic row is missing its identifier or date.")
    for k, t in _FIELDS[kind].items():
        v = raw.get(k)
        if v is None:
            clean[k] = None
        elif t == "url":
            clean[k] = url(v)
        elif t == "date":
            clean[k] = native_date(v)
        elif t == "count":
            clean[k] = numeric(v, integer_only=True)
        elif t == "bool":
            if not isinstance(v, bool):
                raise OnlineSourceError("Unexpected Webmaster boolean field.")
            clean[k] = v
        elif t == "str":
            if not isinstance(v, str):
                raise OnlineSourceError("Unexpected Webmaster text field.")
            clean[k] = v
        else:
            if not isinstance(v, (str, int)) or isinstance(v, bool):
                raise OnlineSourceError("Unexpected Webmaster scalar field.")
            clean[k] = v
    return clean


def fetch(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    method, kind, args, context = arguments(request)
    o = request.options
    base = {"method": method, "access": "Bing Webmaster API", "context": context,
            "query_encoding": o.get("query_encoding", "documented"), "complete_inventory": False}
    if kind in {"links", "url-links"}:
        page = integer(o, "page_index", 0, 0, 32767)
        pages = integer(o, "pages", 1, 1, 20)
        if page + pages - 1 > 32767:
            raise ConfigurationError("Bing link page indexes cannot exceed 32767.")
        if pages > http.maximum:
            raise ConfigurationError("Requested link pages exceed max_requests.")
        records, total = [], None
        for _ in range(pages):
            data = obj(_call(http, method, {**args, "page": page}, o))
            batch = array(data.get("Links" if kind == "links" else "Details"))
            total = numeric(data.get("TotalPages"), integer_only=True)
            if total is None:
                raise OnlineSourceError("Missing TotalPages in Bing link response.")
            if total == 0 and batch:
                raise OnlineSourceError("Inconsistent Bing link pagination.")
            records.extend(_record(r, kind) for r in batch)
            page += 1
            if page >= total:
                break
        return [], {**base, "records": records, "total_pages": total,
                    "next_page_index": page if total is not None and page < total else None}, [
                        "Link observations are not an engine-specific authority score or ranking guarantee."]
    data = _call(http, method, args, o)
    if kind in PERFORMANCE:
        report = performance_report(data, kind, context, observed)
        return bwt_candidates(report), {**base, "report": report}, [
            "Native Date values are preserved; bucket duration/retention are not inferred.",
            "Top-query/page methods have no documented date/device filters or row pagination."]
    if kind in {"keyword", "related", "keyword-history"}:
        rows = [obj(data)] if kind == "keyword" else array(data)
        items = []
        for raw in rows:
            r = obj(raw)
            bucket = native_date(r.get("Date"))
            items.append(candidate("Bing Webmaster keyword research", r.get("Query", args["q"]), {
                "keyword_impressions": (numeric(r.get("Impressions"), integer_only=True),
                                        "impressions_in_reported_period"),
                "broad_keyword_impressions": (numeric(r.get("BroadImpressions"), integer_only=True),
                                              "broad_impressions_in_reported_period")},
                observed_at=observed, geography=args["country"], relationship="keyword-research",
                metadata={"seed": args["q"], "language": args["language"], "native_bucket": bucket,
                          "start_date": args.get("startDate"), "end_date": args.get("endDate"),
                          "method": method},
                note="Bing-reported keyword impressions, not property impressions, monthly averages or SEO difficulty."))
        return items, {**base, "country": args["country"], "language": args["language"]}, []
    if kind == "url-info" and data is None:
        return [], {**base, "records": [], "availability": "no-record-returned"}, [
            "No record returned; this is not a verified zero or an indexing verdict."]
    records = [_record(r, kind) for r in ([obj(data)] if kind == "url-info" else array(data))]
    return [], {**base, "records": records}, [
        "Read-only diagnostic records. No URL, sitemap or IndexNow submission was performed.",
        "The IsPage flag distinguishes page/domain records; it is not a standalone indexed-status boolean."]
