"""Bounded offline inputs and public-page review for Google Search research."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigurationError, InputError
from ..models import PluginRequest
from .common import (
    OnlineSourceError,
    array,
    boolean,
    candidate,
    integer,
    iso_date,
    obj,
    phrase,
    seeds,
    text,
)
from .google_search_analysis import SERP_SCHEMA, normal_url, numeric, validate_serp

OBS_SCHEMA = "keywordmoves-google-observations/v1"


def read_path(path: Path, options: Mapping[str, Any]) -> str:
    maximum = integer(options, "max_response_bytes", 2_000_000, 1024, 10_000_000)
    try:
        with Path(path).open("rb") as handle:
            value = handle.read(maximum+1)
        if len(value) > maximum:
            raise InputError("Input exceeds max_response_bytes.")
        return value.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise InputError("Inputs must be readable UTF-8 files.") from None


def read_json(path: Path, options: Mapping[str, Any]) -> Any:
    try:
        return json.loads(read_path(path, options), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise InputError("Expected finite, valid JSON input.") from None


def require_files(request: PluginRequest, count: int = 1) -> None:
    if len(request.inputs) != count:
        raise ConfigurationError(f"Supply exactly {count} --input file(s) for {request.operation}.")


def soup_for(body: str) -> Any:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConfigurationError("HTML imports require keywordmoves[online].") from None
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
    if soup.select_one("input[type=password], .g-recaptcha, .h-captcha, #challenge-form") or any(
            s in title for s in ("captcha", "access denied", "just a moment", "unusual traffic", "before you continue", "sign in")):
        raise OnlineSourceError("This is a consent/access challenge, not a usable results page.")
    return soup


def select(soup: Any, selector: str) -> list:
    try:
        return soup.select(selector)
    except Exception as exc:
        # Beautiful Soup delegates syntax validation to soupsieve. Never echo HTML.
        from soupsieve.util import SelectorSyntaxError
        if isinstance(exc, SelectorSyntaxError):
            raise ConfigurationError("Invalid CSS selector.") from None
        raise


def html_serp(request: PluginRequest) -> dict:
    require_files(request)
    o = request.options
    body = read_path(request.inputs[0], o)
    soup = soup_for(body)
    organic_selector, title_selector, link_selector = (text(o, k) for k in ("organic_selector", "title_selector", "link_selector"))
    nodes = select(soup, organic_selector)
    if not nodes:
        raise InputError("No reviewed organic-result containers matched; this is not a zero-competition claim.")
    maximum = integer(o, "max_rows", 100, 1, 1000)
    if len(nodes) > maximum:
        raise InputError("Too many HTML results; narrow the selector or explicitly raise max_rows.")
    organic = []
    for index, node in enumerate(nodes, 1):
        titles, links = select(node, title_selector), select(node, link_selector)
        if len(titles) != 1 or len(links) != 1 or not links[0].get("href"):
            raise InputError("Each result container must have one title and one direct destination link.")
        # Redirect URLs are not unwrapped or followed: provenance must be explicit.
        url = normal_url(links[0]["href"])
        rank = node.get(text(o, "rank_attribute")) if "rank_attribute" in o else index
        snippet = ""
        if "snippet_selector" in o:
            snippet = " ".join(n.get_text(" ", strip=True) for n in select(node, text(o, "snippet_selector")))
        organic.append({"url": url, "position": rank, "title": titles[0].get_text(" ", strip=True), "snippet": snippet})
    return validate_serp({"schema": SERP_SCHEMA, "query": seeds(request)[0], "source": text(o, "source"),
                          "scope": text(o, "scope"), "observed_at": iso_date(o, "observed_at"),
                          "country": text(o, "country"), "language": text(o, "language", "en"),
                          "device": text(o, "device", "desktop"), "location": o.get("location"), "engine": "google",
                          "organic": organic, "features_returned": [], "suggestions": [],
                          "html_selection": {k: o.get(k) for k in ("organic_selector", "title_selector", "link_selector", "rank_attribute")},
                          "exhaustive": False})


def page_audit(body: str, request: PluginRequest, observed: str) -> tuple[list, dict]:
    soup = soup_for(body)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = soup.select_one('meta[name="description"]')
    canonical = soup.select_one('link[rel="canonical"]')
    robots = soup.select_one('meta[name="robots"]')
    h1 = [v.get_text(" ", strip=True) for v in soup.find_all("h1")]
    for node in soup.select("script,style,template,noscript"):
        node.decompose()
    visible = " ".join((soup.body or soup).stripped_strings)
    tokens = re.findall(r"\b\w+(?:['’-]\w+)*\b", visible, re.UNICODE)
    metadata = {"url": request.options.get("url"), "title": title,
                "meta_description": description.get("content") if description else None,
                "canonical_link": canonical.get("href") if canonical else None,
                "meta_robots": robots.get("content") if robots else None, "h1": h1,
                "visible_word_count": len(tokens), "rendering": "static-html-not-javascript-rendered"}
    items = []
    for query in seeds(request, 20):
        pattern = re.compile(r"(?<!\w)" + re.escape(" ".join(query.split())) + r"(?!\w)", re.I)
        items.append(candidate("Public/saved page HTML analysis", query, {
            "title_phrase_occurrences": (len(pattern.findall(title)), "count"),
            "h1_phrase_occurrences": (sum(len(pattern.findall(h)) for h in h1), "count"),
            "visible_text_phrase_occurrences": (len(pattern.findall(visible)), "count"),
        }, observed_at=observed, relationship="page-keyword-coverage", metadata=metadata,
            note="Literal static-HTML coverage, not an ideal keyword-density recommendation, quality score or ranking prediction."))
    return items, metadata


def import_observations(request: PluginRequest) -> tuple[list, dict]:
    require_files(request)
    path, o = request.inputs[0], request.options
    if path.suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(read_path(path, o)))
        required = ("phrase", "metric", "value", "unit", "source", "scope", "observed_at")
        mapping = {k: o.get(k+"_column", k) for k in required}
        if not set(mapping.values()).issubset(reader.fieldnames or []):
            raise InputError("Observation CSV requires phrase,metric,value,unit,source,scope,observed_at columns or explicit mappings.")
        rows = []
        for raw in reader:
            if None in raw or any(v is None for v in raw.values()):
                raise InputError("Malformed observation CSV.")
            row = {k: raw[v] for k, v in mapping.items()}
            row.update({k: raw.get(k) for k in ("country", "language", "device", "window")})
            if "approximate" in raw:
                row["approximate"] = boolean(raw, "approximate")
            row["value"] = numeric(row["value"])
            rows.append(row)
    else:
        payload = obj(read_json(path, o))
        if payload.get("schema") != OBS_SCHEMA:
            raise InputError(f"Expected {OBS_SCHEMA}.")
        rows = array(payload.get("observations"))
    if len(rows) > 10000:
        raise InputError("At most 10000 observations can be imported per file.")
    items, clean = [], []
    for raw in rows:
        row = obj(raw)
        word, metric, unit, source, scope = [phrase(row.get(k)) for k in ("phrase", "metric", "unit", "source", "scope")]
        observed = iso_date(row, "observed_at")
        value = numeric(row.get("value"), low=None)
        approximate = boolean(row, "approximate")
        metadata = {"scope": scope, "country": row.get("country"), "language": row.get("language"),
                    "device": row.get("device"), "window": row.get("window"), "approximate": approximate}
        clean.append({"phrase": word, "metric": metric, "unit": unit, "source": source, "scope": scope,
                      "observed_at": observed, "value": value, **metadata})
        items.append(candidate(source, word, {metric: (value, unit)}, observed_at=observed,
                               geography=row.get("country"), relationship="imported-observation", metadata=metadata,
                               note="Source-labelled observation; no conversion between demand, ad competition and organic difficulty."))
    return items, {"observations_report": {"schema": OBS_SCHEMA, "observations": clean}}


def compare_observations(before: dict, after: dict, observed: str) -> list:
    def indexed(report):
        if report.get("schema") != OBS_SCHEMA:
            report = obj(obj(report.get("metadata")).get("observations_report"))
        if report.get("schema") != OBS_SCHEMA:
            raise InputError(f"Expected {OBS_SCHEMA} snapshots.")
        result = {}
        for row in array(report.get("observations")):
            row = obj(row)
            for name in ("phrase", "metric", "unit", "source", "scope"):
                phrase(row.get(name))
            for name in ("country", "language", "device", "window"):
                if row.get(name) is not None and not isinstance(row[name], str):
                    raise InputError("Observation context values must be strings or null.")
            key = tuple(row.get(k) for k in ("phrase", "metric", "unit", "source", "scope", "country", "language", "device", "window"))
            if key in result:
                raise InputError("Duplicate observation identities cannot be compared.")
            result[key] = row
        return result
    old, new = indexed(before), indexed(after)
    if not (old.keys() & new.keys()):
        raise InputError("No compatible observation identities; source/scope/geography/unit/window must match.")
    items = []
    for key in sorted(old.keys() & new.keys(), key=str):
        a, b = old[key], new[key]
        try:
            days = (datetime.fromisoformat(iso_date(b, "observed_at")) - datetime.fromisoformat(iso_date(a, "observed_at"))).days
        except (ValueError, TypeError):
            raise InputError("Observations require valid capture dates.") from None
        if days <= 0:
            raise InputError("Observation captures must be chronological, at least one day apart.")
        x, y = numeric(a.get("value"), low=None), numeric(b.get("value"), low=None)
        exact = not boolean(a, "approximate") and not boolean(b, "approximate") and x is not None and y is not None
        delta = y-x if exact else None
        items.append(candidate("Comparable observation change", key[0], {
            "change": (delta, key[2]+"_change"), "change_per_day": (delta/days if delta is not None else None, key[2]+"_change_per_day"),
            "percent_change": (delta/x*100 if exact and x else None, "percent")},
            observed_at=observed, relationship="observation-change", metadata={"metric": key[1], "source": key[3], "scope": key[4], "approximate_excluded": not exact},
            note="Net change between compatible observations, not a growth forecast; rounded values and zero percentage baselines are excluded."))
    return items


def trends_import(request: PluginRequest) -> tuple[list, dict]:
    require_files(request)
    o = request.options
    if o.get("search_property") != "web":
        raise ConfigurationError("Confirm the export used Google Web Search with search_property=web; the CSV may not encode the property.")
    observed, scope, country = iso_date(o, "observed_at"), text(o, "scope"), text(o, "country")
    rows = list(csv.reader(io.StringIO(read_path(request.inputs[0], o))))
    index = next((i for i, row in enumerate(rows) if row and row[0].casefold() in {"day", "week", "month", "date"}), None)
    if index is None or len(rows[index]) < 2:
        raise InputError("Expected a Google Trends interest-over-time CSV export.")
    header = rows[index]
    series = {name: [] for name in header[1:]}
    for row in rows[index+1:]:
        if not row or not any(row):
            continue
        if len(row) != len(header):
            raise InputError("Inconsistent Trends CSV column count.")
        for name, raw in zip(header[1:], row[1:]):
            value = None if raw in {"<1", "", "-"} else numeric(raw, high=100)
            series[name].append({"date": row[0], "value": value, "display": raw, "censored_below_one": raw == "<1"})
    items = [candidate("Google Trends CSV", name, {"returned_time_points": (len(points), "count")},
                       observed_at=observed, geography=country, relationship="relative-search-interest",
                       metadata={"scope": scope, "series": points},
                       note="Relative interest, not search counts; <1 is retained as censored rather than changed to zero.")
             for name, points in series.items()]
    return items, {"search_property": "web", "search_property_confirmed_by_caller": True, "scope": scope}
