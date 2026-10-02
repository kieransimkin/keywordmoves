"""Read-only Search Console integration for the Google Search workbench.

OAuth tokens are supplied by the caller; this module neither creates credentials,
changes properties/sitemaps nor submits indexing requests.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from ..errors import ConfigurationError, InputError
from ..models import PluginRequest
from .common import (
    HTTP,
    OnlineSourceError,
    array,
    candidate,
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
from .google_search_analysis import GSC_DIMENSIONS, GSC_SCHEMA, gsc_report, normal_url, numeric

HOSTS = ("www.googleapis.com", "searchconsole.googleapis.com")
SEARCH_TYPES = ("web", "image", "video", "news", "discover", "googleNews")
FILTER_DIMENSIONS = ("query", "page", "country", "device", "searchAppearance")
FILTER_OPERATORS = ("equals", "notEquals", "contains", "notContains", "includingRegex", "excludingRegex")


def dimensions(options: Mapping[str, Any], default: str = "query") -> list[str]:
    value = options.get("dimensions", default)
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if (not isinstance(value, list) or any(not isinstance(d, str) or d not in GSC_DIMENSIONS for d in value)
            or len(value) != len(set(value))):
        raise ConfigurationError("dimensions must be unique query,page,country,device,date,hour,searchAppearance values.")
    if "searchAppearance" in value and len(value) != 1:
        raise ConfigurationError("Query searchAppearance alone first, then use an appearance filter with other dimensions.")
    return value


def site_url(options: Mapping[str, Any]) -> str:
    site = text(options, "site_url")
    if site.startswith("sc-domain:"):
        if not re.fullmatch(r"sc-domain:[A-Za-z0-9.-]+", site) or "." not in site:
            raise ConfigurationError("site_url must identify a valid sc-domain property.")
    else:
        try:
            normal_url(site)
        except OnlineSourceError:
            raise ConfigurationError("site_url must be an exact Search Console URL-prefix or sc-domain property.") from None
        if not site.endswith("/") or urlsplit(site).query or urlsplit(site).fragment:
            raise ConfigurationError("A URL-prefix property must end in / and have no query or fragment.")
    return site


def request_body(request: PluginRequest, *, default_dimensions: str = "query") -> tuple[str, dict]:
    o = request.options
    site = site_url(o)
    start, end = iso_date(o, "start_date"), iso_date(o, "end_date")
    if start > end:
        raise ConfigurationError("start_date must not follow end_date.")
    dims = dimensions(o, default_dimensions)
    kind = choice(o, "search_type", "web", SEARCH_TYPES)
    state = choice(o, "data_state", "final", ("final", "all", "hourly_all"))
    if ("hour" in dims) != (state == "hourly_all"):
        raise ConfigurationError("hour dimensions and data_state=hourly_all must be selected together.")
    aggregation = choice(o, "aggregation", "auto", ("auto", "byPage", "byProperty", "byNewsShowcasePanel"))
    filters = []
    if request.keywords:
        filters.append({"dimension": "query", "operator": choice(o, "query_operator", "contains", FILTER_OPERATORS),
                        "expression": seeds(request)[0]})
    if "country" in o:
        filters.append({"dimension": "country", "operator": "equals",
                        "expression": code(o, "country", pattern=r"[A-Za-z]{3}").lower()})
    if "device" in o:
        filters.append({"dimension": "device", "operator": "equals",
                        "expression": choice(o, "device", "DESKTOP", ("DESKTOP", "MOBILE", "TABLET"))})
    for key, dim in (("page", "page"), ("appearance", "searchAppearance")):
        if key in o:
            filters.append({"dimension": dim, "operator": "equals", "expression": text(o, key)})
    if "filters_json" in o:
        value = o["filters_json"]
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except (ValueError, RecursionError):
            raise ConfigurationError("filters_json must be a JSON array of filter objects.") from None
        if not isinstance(value, list):
            raise ConfigurationError("filters_json must be a JSON array.")
        filters.extend(value)
    if len(filters) > 20:
        raise ConfigurationError("At most 20 filters are accepted per request.")
    for f in filters:
        if (not isinstance(f, dict) or set(f) != {"dimension", "operator", "expression"}
                or f["dimension"] not in FILTER_DIMENSIONS or f["operator"] not in FILTER_OPERATORS
                or not isinstance(f["expression"], str) or not 1 <= len(f["expression"]) <= 4096):
            raise ConfigurationError("Invalid Search Console filter; use dimension, operator and expression.")
    if aggregation == "byProperty" and ("page" in dims or any(f["dimension"] == "page" for f in filters)
                                         or kind in ("discover", "googleNews")):
        raise ConfigurationError("byProperty cannot be used with page grouping/filtering or Discover/Google News.")
    if aggregation == "byNewsShowcasePanel":
        if (kind not in ("discover", "googleNews") or "page" in dims
                or any(f["dimension"] == "page" for f in filters)
                or not any(f == {"dimension": "searchAppearance", "operator": "equals", "expression": "NEWS_SHOWCASE"} for f in filters)
                or any(f["dimension"] == "searchAppearance" and f["expression"] != "NEWS_SHOWCASE" for f in filters)):
            raise ConfigurationError("News Showcase aggregation requires its appearance filter and Discover or Google News, without pages.")
    body = {"startDate": start, "endDate": end, "dimensions": dims, "type": kind,
            "dataState": state, "aggregationType": aggregation}
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
    return site, body


def metadata_from_body(site: str, body: Mapping[str, Any], observed: str) -> dict:
    return {"site_url": site, "start_date": body["startDate"], "end_date": body["endDate"],
            "dimensions": list(body["dimensions"]), "search_type": body["type"],
            "data_state": body["dataState"], "aggregation_type": body["aggregationType"],
            "filters": body.get("dimensionFilterGroups", []), "observed_at": observed,
            "source_scope": "Search Console Search Analytics API", "complete_query_inventory": False}


def fetch_report(request: PluginRequest, http: HTTP, observed: str, default_dimensions: str = "query") -> dict:
    site, body = request_body(request, default_dimensions=default_dimensions)
    o = request.options
    size = integer(o, "page_size", 1000, 1, 25000)
    pages = integer(o, "pages", 1, 1, 20)
    maximum = integer(o, "max_rows", 25000, 1, 50000)
    offset = integer(o, "start_row", 0, 0, 1_000_000)
    if pages > http.maximum - http.requests:
        raise ConfigurationError("pages exceeds the remaining max_requests budget.")
    headers = {"Authorization": "Bearer " + secret(o, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN")}
    url = "https://www.googleapis.com/webmasters/v3/sites/" + quote(site, safe="") + "/searchAnalytics/query"
    rows, incomplete, more, aggregation = [], {}, False, None
    for _ in range(pages):
        take = min(size, maximum - len(rows))
        if take <= 0:
            break
        data = provider_error(http.json("POST", url, headers=headers,
                                       json={**body, "rowLimit": take, "startRow": offset}))
        batch = array(data.get("rows", []))
        if len(batch) > take:
            raise OnlineSourceError("Search Console returned more rows than requested.")
        current = data.get("responseAggregationType", body["aggregationType"])
        if aggregation is not None and aggregation != current:
            raise OnlineSourceError("Search Console aggregation changed across pages.")
        aggregation = current
        if "metadata" in data:
            native = obj(data["metadata"])
            for k in ("first_incomplete_date", "first_incomplete_hour"):
                if k in native:
                    incomplete[k] = native[k]
        rows.extend(batch)
        offset += len(batch)
        more = len(batch) == take
        if not more:
            break
    metadata = metadata_from_body(site, body, observed)
    metadata.update(aggregation_type=aggregation or body["aggregationType"],
                    next_start_row=offset if more else None, pagination_may_have_more=more,
                    incomplete_data=incomplete, request_count=http.requests,
                    retained_rows=len(rows))
    return gsc_report(rows, metadata)


def inventory(request: PluginRequest, http: HTTP, observed: str) -> tuple[list, dict, list]:
    o = request.options
    if request.operation in ("gsc-sites", "gsc-sitemaps") and request.keywords:
        raise ConfigurationError("Property and sitemap inventories do not take keyword seeds.")
    headers = {"Authorization": "Bearer " + secret(o, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN")}
    if request.operation == "gsc-sites":
        data = provider_error(http.json("GET", "https://www.googleapis.com/webmasters/v3/sites", headers=headers))
        items = [candidate("Google Search Console", row["siteUrl"], {}, observed_at=observed,
                           relationship="authorised-property", metadata={"permission_level": row.get("permissionLevel")})
                 for row in [obj(v) for v in array(data.get("siteEntry", []))]]
        return items, {}, ["Lists accessible properties; it does not add or verify a property."]
    site = site_url(o)
    if request.operation == "gsc-sitemaps":
        data = provider_error(http.json("GET", "https://www.googleapis.com/webmasters/v3/sites/" + quote(site, safe="") + "/sitemaps", headers=headers))
        items = []
        for row in array(data.get("sitemap", [])):
            row = obj(row)
            items.append(candidate("Google Search Console", row["path"],
                                   {"errors": (numeric(row.get("errors")), "count"),
                                    "warnings": (numeric(row.get("warnings")), "count")},
                                   observed_at=observed, relationship="sitemap-status",
                                   metadata={k: row.get(k) for k in ("lastSubmitted", "lastDownloaded", "isPending", "isSitemapsIndex", "contents")}))
        return items, {"site_url": site}, ["Sitemap inventory/status, not a keyword or ranking report; no changes are made."]
    urls = seeds(request, 10)
    if len(urls) > http.maximum - http.requests:
        raise ConfigurationError("URL count exceeds the remaining max_requests budget.")
    parsed_site = urlsplit(site) if not site.startswith("sc-domain:") else None
    for url in urls:
        normal_url(url)
        host = urlsplit(url).hostname or ""
        domain = site.removeprefix("sc-domain:").lower()
        valid = host == domain or host.endswith("." + domain) if parsed_site is None else (
            urlsplit(url).scheme == parsed_site.scheme and urlsplit(url).netloc == parsed_site.netloc
            and urlsplit(url).path.startswith(parsed_site.path))
        if not valid:
            raise ConfigurationError("Every inspection URL must belong to the supplied Search Console property.")
    items = []
    for url in urls:
        data = provider_error(http.json("POST", "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                                       headers=headers, json={"inspectionUrl": url, "siteUrl": site,
                                                             "languageCode": text(o, "language", "en-GB")}))
        result = obj(data.get("inspectionResult"))
        status = obj(result.get("indexStatusResult", {}))
        items.append(candidate("Google URL Inspection", url, {}, observed_at=observed,
                               relationship="indexed-url-status", metadata={
                                   "site_url": site, "index_status": status,
                                   "rich_results": result.get("richResultsResult"),
                                   "inspection_result_link": result.get("inspectionResultLink")},
                               note="Indexed-version status only, not a live URL test or a ranking forecast."))
    return items, {"site_url": site}, ["No indexing requests, property changes or sitemap submissions were made."]


def import_report(content: str, suffix: str, options: Mapping[str, Any]) -> dict:
    if suffix == ".json":
        try:
            payload = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError):
            raise InputError("Expected finite JSON data.") from None
        if isinstance(payload, dict) and payload.get("schema") == GSC_SCHEMA:
            report = gsc_report(array(payload.get("rows")), payload)
            return report
        if isinstance(payload, dict) and payload.get("plugin") == "google-search":
            report = obj(obj(payload.get("metadata")).get("gsc_report"))
            if report.get("schema") != GSC_SCHEMA:
                raise InputError("The input does not contain a Search Console report.")
            return gsc_report(array(report.get("rows")), report)
        rows = array(obj(payload).get("rows")) if isinstance(payload, dict) else array(payload)
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        dims = dimensions(options)
        aliases = {"query": ("query", "Query", "Top queries"), "page": ("page", "Page", "Top pages", "url"),
                   "ctr": ("ctr", "CTR", "Ctr"), "date": ("date", "Date"), "country": ("country", "Country"), "device": ("device", "Device")}
        selected = {}
        for name in [*dims, "clicks", "impressions", "ctr", "position"]:
            selected[name] = options.get(name + "_column") or next((h for h in aliases.get(name, (name, name.title())) if h in headers), None)
            if selected[name] not in headers:
                raise InputError(f"Missing {name} column; set {name}_column explicitly.")
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise InputError("Malformed CSV record.")
            values: dict[str, Any] = {"dimensions": {d: row[selected[d]] for d in dims}}
            for name in ("clicks", "impressions", "ctr", "position"):
                raw = row[selected[name]].strip()
                if name == "ctr" and raw.endswith("%"):
                    values[name] = numeric(raw[:-1], high=100) / 100
                else:
                    if re.fullmatch(r"[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?", raw):
                        raw = raw.replace(",", "")
                    values[name] = numeric(raw)
            rows.append(values)
    else:
        raise InputError("Search Console imports must be .json or .csv.")
    if len(rows) > 50000:
        raise InputError("Import exceeds 50000 rows; aggregate or split it before import.")
    observed = iso_date(options, "observed_at")
    site, body = request_body(PluginRequest("gsc-import", options=options))
    metadata = metadata_from_body(site, body, observed)
    metadata.update(source_scope=text(options, "scope"), source=text(options, "source"),
                    imported=True, pagination_may_have_more=None)
    return gsc_report(rows, metadata)


def bulk_sql(options: Mapping[str, Any]) -> tuple[str, dict]:
    if {"country", "device", "page", "appearance", "filters_json", "query_operator", "data_state"}.intersection(options):
        raise ConfigurationError("gsc-bulk-sql supports explicit table/property/date/type/grouping options, not Search Analytics filters or data_state.")
    table = text(options, "table")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.searchdata_(site|url)_impression", table):
        raise ConfigurationError("table must be project.dataset.searchdata_site_impression or searchdata_url_impression.")
    start, end = iso_date(options, "start_date"), iso_date(options, "end_date")
    if start > end:
        raise ConfigurationError("Invalid bulk-export date range.")
    dims = dimensions(options)
    if any(d not in ("query", "page", "country", "device", "date") for d in dims):
        raise ConfigurationError("Bulk SQL supports query,page,country,device,date dimensions.")
    is_url = table.endswith("searchdata_url_impression")
    if "page" in dims and not is_url:
        raise ConfigurationError("The page dimension requires the URL-level export table.")
    if not dims:
        raise ConfigurationError("Supply at least one grouping dimension for this keyword export.")
    columns = {"query": "query", "page": "url AS page", "date": "data_date AS date", "country": "country", "device": "device"}
    total = "sum_position" if is_url else "sum_top_position"
    kind = choice(options, "search_type", "web", SEARCH_TYPES)
    site = site_url(options)
    # Parameters, not interpolated user strings, carry property/date/type values.
    sql = "SELECT\n  " + ",\n  ".join(columns[d] for d in dims) + ",\n" + (
        "  SUM(clicks) AS clicks,\n  SUM(impressions) AS impressions,\n"
        "  SAFE_DIVIDE(SUM(clicks), SUM(impressions)) AS ctr,\n"
        f"  SAFE_DIVIDE(SUM({total}), SUM(impressions)) + 1 AS position\n"
        f"FROM `{table}`\n"
        "WHERE data_date BETWEEN @start_date AND @end_date\n"
        "  AND site_url = @site_url AND LOWER(search_type) = LOWER(@search_type)\n"
        "  AND NOT is_anonymized_query AND query IS NOT NULL AND query != ''\n"
        "GROUP BY " + ", ".join(str(i+1) for i in range(len(dims))) + "\nORDER BY impressions DESC\n"
    )
    return sql, {"parameters": {"start_date": start, "end_date": end, "site_url": site, "search_type": kind},
                 "parameter_types": {"start_date": "DATE", "end_date": "DATE", "site_url": "STRING", "search_type": "STRING"},
                 "aggregation": "byPage" if is_url else "byProperty", "dimensions": dims,
                 "source_scope": "BigQuery:" + table, "query_executed": False}
