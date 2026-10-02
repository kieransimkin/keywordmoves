"""Bounded local import routes for reviewed Bing exports and search-page HTML."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigurationError, InputError
from .bing_search_analysis import (
    BWT_SCHEMA,
    OBS_SCHEMA,
    SERP_SCHEMA,
    captured,
    observations,
    validate_bwt,
    validate_serp,
)
from .common import boolean, choice, integer, obj, seeds, text


def read_text(path: Path, options: Mapping[str, Any]) -> str:
    maximum = integer(options, "max_input_bytes", 5_000_000, 1024, 20_000_000)
    try:
        with Path(path).open("rb") as f:
            raw = f.read(maximum + 1)
        if len(raw) > maximum:
            raise InputError("Bing input exceeds max_input_bytes; it was not truncated.")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise InputError("Cannot read Bing input as UTF-8; check path, permissions and encoding.") from None


def read_json(path: Path, options: Mapping[str, Any]) -> Any:
    def reject(value):
        raise ValueError(value)
    try:
        return json.loads(read_text(path, options), parse_constant=reject)
    except (ValueError, TypeError, RecursionError):
        raise InputError("Bing input must be valid finite JSON.") from None


def unwrap(data: Any, key: str) -> dict:
    data = obj(data)
    if data.get("plugin") == "bing-search":
        return obj(obj(data.get("metadata")).get(key))
    return data


def _csv(path: Path, options) -> list[dict]:
    delimiter = text(options, "delimiter", ",")
    if len(delimiter) != 1:
        raise ConfigurationError("CSV delimiter must be a single character.")
    try:
        reader = csv.DictReader(io.StringIO(read_text(path, options)), delimiter=delimiter)
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)):
            raise InputError("CSV requires a nonempty, nonduplicate header.")
        rows = []
        for r in reader:
            if None in r or any(v is None for v in r.values()):
                raise InputError("CSV row has an unexpected number of columns.")
            rows.append(r)
            if len(rows) > 50000:
                raise InputError("CSV exceeds 50000 rows.")
    except csv.Error:
        raise InputError("Malformed Bing CSV.") from None
    return rows


def _cell(row, o, option, *, required=False):
    if option not in o:
        if required:
            raise ConfigurationError(f"CSV requires an explicit {option} mapping.")
        return None
    column = text(o, option)
    if column not in row:
        raise InputError(f"CSV does not contain mapped column {column!r}.")
    return row[column] if row[column] != "" else None


def bwt_import(path: Path, o) -> dict:
    if path.suffix.casefold() != ".csv":
        data = read_json(path, o)
        if choice(o, "input_format", "canonical", ("canonical", "bwt-native")) == "canonical":
            return validate_bwt(unwrap(data, "report"))
        from .bing_webmaster import PERFORMANCE, performance_report
        kind = choice(o, "report_type", "queries", tuple(sorted(PERFORMANCE)))
        if isinstance(data, dict):
            if "d" not in data:
                raise InputError("A native BWT JSON object must contain the d envelope.")
            data = data["d"]
        context = {"site_url": text(o, "site_url"), "query": o.get("query"), "page": o.get("page_url")}
        if kind in {"query-pages", "query-history", "detail"} and not context["query"]:
            raise ConfigurationError("This native export needs its original query option.")
        if kind in {"page-queries", "detail"} and not context["page"]:
            raise ConfigurationError("This native export needs its original page_url option.")
        report = performance_report(data, kind, context, captured(o.get("observed_at")))
        report["source"] = text(o, "source")
        return report
    if not boolean(o, "period_totals"):
        raise ConfigurationError("CSV performance import requires period_totals=true and actual period boundaries.")
    rows = []
    for raw in _csv(path, o):
        row = {"query": _cell(raw, o, "query_column"), "page": _cell(raw, o, "page_column"),
               "clicks": _cell(raw, o, "clicks_column", required=True),
               "impressions": _cell(raw, o, "impressions_column", required=True),
               "average_impression_position": _cell(raw, o, "impression_position_column"),
               "average_click_position": _cell(raw, o, "click_position_column"),
               "ctr": _cell(raw, o, "ctr_column")}
        if not row["query"] and not row["page"]:
            raise InputError("CSV performance rows need a query or page, not an anonymised placeholder.")
        if row["ctr"] is not None and boolean(o, "ctr_is_percent"):
            try:
                row["ctr"] = float(row["ctr"].rstrip("%")) / 100
            except ValueError:
                raise InputError("Invalid CTR percentage.") from None
        rows.append(row)
    return validate_bwt({"schema": BWT_SCHEMA, "engine": "Bing", "source": text(o, "source"),
                         "observed_at": captured(o.get("observed_at")), "rows": rows,
                         "context": {"site_url": text(o, "site_url"), "scope": text(o, "scope"),
                                     "report_type": text(o, "report_type", "query-pages"),
                                     "granularity": "period", "start_date": o.get("start_date"),
                                     "end_date": o.get("end_date")}})


def observation_import(path: Path, o) -> dict:
    if path.suffix.casefold() != ".csv":
        return observations(unwrap(read_json(path, o), "report"))
    records = []
    for row in _csv(path, o):
        records.append({"phrase": _cell(row, o, "phrase_column", required=True),
                        "value": _cell(row, o, "value_column", required=True),
                        "metric": _cell(row, o, "metric_column") or text(o, "metric"),
                        "unit": _cell(row, o, "unit_column") or text(o, "unit"),
                        "source": text(o, "source"), "scope": text(o, "scope"),
                        "observed_at": captured(o.get("observed_at")), "geography": o.get("geography"),
                        "period_start": o.get("period_start"), "period_end": o.get("period_end"),
                        "approximate": boolean(o, "approximate")})
    return observations({"schema": OBS_SCHEMA, "engine": "Bing", "observations": records})


def serp_html(path: Path, request) -> dict:
    o = request.options
    if not boolean(o, "organic_only"):
        raise ConfigurationError("Review selectors and declare organic_only=true; ads must not enter organic ranks.")
    try:
        from bs4 import BeautifulSoup
        from soupsieve import SelectorSyntaxError
    except ImportError:
        raise ConfigurationError("HTML import requires keywordmoves[online].") from None
    from .bing_search_providers import market
    body = read_text(path, o)
    soup = BeautifulSoup(body, "html.parser")
    query = seeds(request)[0]
    organic = []
    try:
        rows = soup.select(text(o, "row_selector"))
        if not rows:
            if "no_results_selector" not in o or not soup.select(text(o, "no_results_selector")):
                raise InputError("No results matched; changed markup or a challenge is not a zero-result observation.")
        for rank, row in enumerate(rows, integer(o, "first", 1, 1, 10000)):
            link = row.select_one(text(o, "link_selector"))
            if link is None or not link.get("href"):
                raise InputError("A selected organic row is missing its link.")
            title = row.select_one(text(o, "title_selector")) if "title_selector" in o else link
            snippet = row.select_one(text(o, "snippet_selector")) if "snippet_selector" in o else None
            href = link["href"]
            # No opaque click-tracking URLs are counted as competitor pages.
            from urllib.parse import urlsplit
            parsed = urlsplit(href)
            if parsed.hostname in {"bing.com", "www.bing.com"} and parsed.path.startswith(("/ck/", "/aclk")):
                raise InputError("Export direct destination URLs instead of Bing click-tracking links.")
            organic.append({"rank": rank, "url": href,
                            "title": title.get_text(" ", strip=True) if title else "",
                            "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
    except SelectorSyntaxError:
        raise ConfigurationError("Invalid CSS selector; no import was produced.") from None
    return validate_serp({"schema": SERP_SCHEMA, "engine": "Bing", "source": text(o, "source"),
                          "observed_at": captured(o.get("observed_at")),
                          "context": {"query": query, "market": market(o),
                                      "device": text(o, "device"), "location": o.get("location"),
                                      "safe_search": o.get("safe_search"), "time_filter": o.get("filters"),
                                      "first": integer(o, "first", 1, 1, 10000), "requested_pages": 1},
                          "organic": organic})
