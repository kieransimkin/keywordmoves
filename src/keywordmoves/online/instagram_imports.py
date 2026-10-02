"""Bounded Instagram exports and explicitly configured saved/public HTML."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ..errors import ConfigurationError, InputError
from .common import OnlineSourceError, boolean, integer, text
from .instagram_analysis import hashtag


def read_input(path: Path, maximum: int) -> str:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise InputError("Instagram input exceeds max_input_bytes; split the export before importing.")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise InputError("Instagram inputs must be readable UTF-8 files.") from None


def json_input(body: str) -> Any:
    def invalid(_: str) -> None:
        raise ValueError
    try:
        return json.loads(body, parse_constant=invalid)
    except (ValueError, TypeError, RecursionError):
        raise InputError("Instagram input must contain valid, finite JSON.") from None


def records(payload: Any, maximum: int, kind: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if payload.get("error") or payload.get("errors"):
            raise OnlineSourceError("The saved Instagram response contains an error, not a zero observation.")
        if "schema" in payload and payload["schema"] != "keywordmoves-instagram/v1":
            raise InputError("Unsupported Instagram import schema.")
        if kind == "hashtags":
            # A saved native tag-search response is accepted; no private endpoint is called.
            if "hashtags" in payload and isinstance(payload["hashtags"], list):
                payload = [p.get("hashtag", p) if isinstance(p, dict) else p for p in payload["hashtags"]]
            else:
                payload = payload.get("hashtag_stats", payload.get("data", [payload]))
        else:
            payload = payload.get("posts", payload.get("ig_posts", payload.get("data", [payload])))
    if not isinstance(payload, list) or len(payload) > maximum:
        raise InputError("Instagram records must be an array within max_records; no silent truncation.")
    if any(not isinstance(row, dict) for row in payload):
        raise InputError("Each Instagram record must be a JSON object.")
    for row in payload:
        if kind == "media" and not any(k in row for k in ("caption", "title", "text", "hashtags", "id")):
            raise InputError("Unrecognised media record; use a documented export or the canonical schema.")
        if row.get("error") or row.get("errors"):
            raise OnlineSourceError("An Instagram record reports an error; import stopped.")
    return payload


def read_records(paths: tuple[Path, ...], options: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if not paths:
        raise InputError("Supply --input with an Instagram JSON or CSV export.")
    maximum = integer(options, "max_records", 2000, 1, 10000)
    budget = integer(options, "max_input_bytes", 5_000_000, 1024, 20_000_000)
    output = []
    for path in dict.fromkeys(Path(p).resolve() for p in paths):
        body = read_input(path, budget)
        budget -= len(body.encode("utf-8"))
        if path.suffix.lower() == ".csv":
            parsed = list(csv.DictReader(io.StringIO(body)))
            if kind == "hashtags":
                name_column = text(options, "hashtag_column", "hashtag")
                count_column = text(options, "post_count_column", "post_count")
                if parsed and name_column not in parsed[0]:
                    raise InputError("The configured hashtag_column is absent from the CSV.")
                parsed = [{**row, "name": row[name_column], "post_count": None,
                           "post_count_display": row.get(count_column),
                           "count_is_approximate": boolean(row, "count_is_approximate")}
                          for row in parsed]
        else:
            parsed = json_input(body)
        output.extend(records(parsed, maximum, kind))
        if len(output) > maximum:
            raise InputError("Combined Instagram input exceeds max_records.")
    return output


def saved_html(body: str, options: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from bs4 import BeautifulSoup
        from soupsieve import SelectorSyntaxError
    except ImportError:
        raise ConfigurationError("HTML extraction needs pip install 'keywordmoves[online]'.") from None
    soup = BeautifulSoup(body, "html.parser")
    selector = text(options, "selector")
    try:
        nodes = soup.select(selector)
        output = []
        maximum = integer(options, "max_records", 2000, 1, 10000)
        if len(nodes) > maximum:
            raise InputError("HTML result count exceeds max_records.")
        for node in nodes:
            name_node = node.select_one(options["name_selector"]) if "name_selector" in options else node
            count_node = node.select_one(options["count_selector"]) if "count_selector" in options else None
            if name_node is None:
                raise InputError("The configured name_selector did not match a result.")
            output.append({"name": hashtag(name_node.get_text(" ", strip=True)),
                           "post_count_display": count_node.get_text(" ", strip=True) if count_node else None})
    except (ValueError, TypeError, NotImplementedError, SelectorSyntaxError):
        raise ConfigurationError("Invalid Instagram HTML selectors.") from None
    if not output:
        raise InputError("No configured hashtag nodes were found; not evidence of zero demand.")
    return output


def public_hashtag_html(body: str, tag: str) -> list[dict[str, Any]]:
    """Conservative public-page parser; fail closed when markup no longer exposes counts."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConfigurationError("Public HTML extraction needs keywordmoves[online].") from None
    soup = BeautifulSoup(body, "html.parser")
    canonical = soup.find("meta", property="og:url") or soup.find("link", rel="canonical")
    url = canonical.get("content", canonical.get("href", "")) if canonical else ""
    try:
        parsed = urlsplit(url)
        matched = (parsed.hostname in {"instagram.com", "www.instagram.com"}
                   and parsed.scheme == "https" and not parsed.username and not parsed.password
                   and unquote(parsed.path).rstrip("/").lower() == "/explore/tags/" + tag[1:])
    except ValueError:
        matched = False
    if not matched:
        raise OnlineSourceError("The public page is not the requested hashtag page; it may be a login or challenge.")
    description = soup.find("meta", property="og:description")
    description_text = description.get("content", "") if description else ""
    if not isinstance(description_text, str) or len(description_text) > 2000:
        raise OnlineSourceError("Unexpected public hashtag description length or type.")
    match = re.search(r"(?<![\w.])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*[kmbg]?\+?)\s+posts?\b",
                      description_text, re.I)
    if not match:
        raise OnlineSourceError("No recognised public post-count display; use a reviewed export or API.")
    return [{"name": tag, "post_count_display": match[1], "count_is_approximate": True}]
