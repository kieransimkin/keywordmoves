"""Explicit, bounded imports for YouTube samples, reports and subtitle text."""
from __future__ import annotations

import csv
import html
import io
import json
import re
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigurationError, InputError
from .common import boolean, integer, text
from .youtube_analysis import hashtag, records, string


def decode_json(value: str) -> Any:
    def invalid(_: str) -> None:
        raise ValueError
    try:
        return json.loads(value, parse_constant=invalid)
    except (ValueError, RecursionError):
        raise InputError("Input is not valid finite JSON.") from None


def read(path: Path, options: Mapping[str, Any]) -> str:
    max_bytes = integer(options, "max_input_bytes", 5_000_000, 1, 20_000_000)
    try:
        with Path(path).open("rb") as f:
            value = f.read(max_bytes + 1)
        if len(value) > max_bytes:
            raise InputError("Input exceeds max_input_bytes; no truncation was performed.")
        return value.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise InputError("Cannot read the input as UTF-8; check its path, encoding and permissions.") from None


def load(path: Path, options: Mapping[str, Any], kind: str) -> list[dict]:
    content = read(path, options)
    if path.suffix.casefold() == ".csv":
        delimiter = text(options, "delimiter", ",")
        if len(delimiter) != 1:
            raise ConfigurationError("delimiter must be one character.")
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise InputError("Missing or duplicate CSV headers.")
        raw = list(reader)
        if any(None in r or None in r.values() for r in raw):
            raise InputError("Malformed CSV row; field counts must match the header.")
        mappings = {
            "id": "id_column", "title": "title_column", "description": "description_column",
            "text": "text_column", "viewCount": "views_column", "likeCount": "likes_column",
            "commentCount": "comments_column", "published_at": "published_column",
            "channel_id": "channel_column", "phrase": "phrase_column", "metric": "metric_column",
            "value": "value_column", "unit": "unit_column", "window": "window_column",
        }
        mapped = []
        for r in raw:
            row = dict(r)
            for field, key in mappings.items():
                if key in options:
                    column = text(options, key)
                    if column not in r:
                        raise InputError(f"CSV is missing the explicitly mapped {key}.")
                    row[field] = r[column]
            if "tags_column" in options:
                column = text(options, "tags_column")
                if column not in r:
                    raise InputError("CSV is missing tags_column.")
                row["tags"] = [v.strip() for v in r[column].split(text(options, "tags_delimiter", "|")) if v.strip()]
            if kind == "observations":
                for k in ("metric", "unit", "kind", "window"):
                    if k in options:
                        row[k] = options[k]
                if "approximate" in row:
                    row["approximate"] = boolean(row, "approximate")
            mapped.append(row)
        value = mapped
    else:
        value = decode_json(content)
        if "records_path" in options:
            for part in text(options, "records_path").split("."):
                if not isinstance(value, dict) or part not in value:
                    raise InputError("records_path does not exist.")
                value = value[part]
        elif isinstance(value, dict):
            for key in ("items", "videos", "observations", "comments"):
                if key in value:
                    value = value[key]
                    break
    return records(value, integer(options, "max_records", 10000, 1, 50000))


def transcript(value: str, origin: str, fmt: str) -> list[dict]:
    """Preserve cue boundaries. Drop exact duplicates, not overlapping spoken words."""
    if fmt == "txt":
        return [{"id": f"{origin}:{i}", "description": line} for i, line in enumerate(value.splitlines()) if line.strip()]
    if fmt not in {"srt", "vtt"}:
        raise ConfigurationError("Transcript format must be txt, srt or vtt.")
    timing = re.compile(r"((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d{3})[^\n]*")
    matches = list(timing.finditer(value))
    if not matches and value.strip():
        raise InputError("No valid subtitle cues found; this is not a transcript file.")
    out, seen = [], set()
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i+1].start() if i+1 < len(matches) else len(value)
        body = value[start:end].strip().split("\n\n", 1)[0]
        # VTT voice/class/time tags are markup, not keyword terms.
        body = html.unescape(re.sub(r"<[^>]*>", "", body)).strip()
        def seconds(v: str) -> float:
            parts = v.replace(",", ".").split(":")
            if any(float(x) >= 60 for x in parts[-2:]):
                raise InputError("Invalid subtitle timestamp.")
            return sum(float(p) * 60**j for j, p in enumerate(reversed(parts)))
        a, b = seconds(m[1]), seconds(m[2])
        if b < a:
            raise InputError("Subtitle cue ends before it starts.")
        key = (a, b, body)
        if body and key not in seen:
            seen.add(key)
            out.append({"id": f"{origin}:{i}", "description": body, "start_seconds": a, "end_seconds": b})
    return out


def soup(value: str) -> Any:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConfigurationError("Install keywordmoves[online] for HTML imports.") from None
    doc = BeautifulSoup(value, "html.parser")
    title = doc.title.get_text(" ", strip=True).casefold() if doc.title else ""
    if any(t in title for t in ("sign in", "consent", "captcha", "verify you", "access denied")) or doc.select("input[name='captcha'], form[action*='accounts.google.com']"):
        raise InputError("Login, consent or challenge page; not keyword results.")
    return doc


def html_observations(value: str, options: Mapping[str, Any]) -> list[dict]:
    doc = soup(value)
    try:
        blocks = doc.select(text(options, "item_selector"))
        rows = []
        for block in blocks:
            word = block.select_one(text(options, "phrase_selector")) if "phrase_selector" in options else block
            if word is None:
                raise InputError("A result has no matching phrase_selector.")
            counter = block.select_one(text(options, "value_selector")) if "value_selector" in options else None
            if "value_selector" in options and counter is None:
                raise InputError("A result has no matching value_selector.")
            rows.append({"phrase": word.get_text(" ", strip=True),
                         "value": counter.get_text(" ", strip=True) if counter else None,
                         "metric": text(options, "metric", "observed_suggestion"),
                         "unit": text(options, "unit", "unmeasured"),
                         "kind": text(options, "kind", "hashtag"), "window": options.get("window")})
        if not rows and not ("empty_selector" in options and doc.select(text(options, "empty_selector"))):
            raise InputError("No result selector matched; an empty/dynamic page is not zero demand.")
        return records(rows, integer(options, "max_records", 10000, 1, 50000))
    except InputError:
        raise
    except Exception:
        raise InputError("Invalid CSS selector or unexpected HTML structure.") from None


def hashtag_page(value: str, expected: str) -> list[dict]:
    """Experimental exact-match header parsing; never execute page JavaScript."""
    doc = soup(value)
    payloads = []
    for node in doc.find_all("script"):
        script = node.string or node.get_text()
        match = re.search(r"(?:var\s+)?ytInitialData\s*=\s*|window\[['\"]ytInitialData['\"]\]\s*=\s*", script)
        if match:
            try:
                val, _ = json.JSONDecoder().raw_decode(script[match.end():].lstrip())
                payloads.append(val)
            except (ValueError, RecursionError):
                raise InputError("The hashtag page's embedded JSON is unreadable.") from None
    def walk(v: Any, depth: int = 0):
        if depth > 60:
            raise InputError("Embedded page nesting is too deep.")
        if isinstance(v, dict):
            if "hashtagHeaderRenderer" in v:
                yield v["hashtagHeaderRenderer"]
            for child in v.values():
                yield from walk(child, depth+1)
        elif isinstance(v, list):
            for child in v:
                yield from walk(child, depth+1)
    def label(v: Any) -> str:
        if not isinstance(v, dict):
            return ""
        return string(v.get("simpleText")) or "".join(string(x.get("text")) for x in v.get("runs", []))
    rows = []
    for p in payloads:
        for header in walk(p):
            if hashtag(label(header.get("title"))) != expected:
                continue
            for key, metric, unit in (("numVideos", "reported_video_count", "videos"), ("numChannels", "reported_channel_count", "channels")):
                displayed = label(header.get(key)).strip()
                # Only English labelled numeric displays have a reviewed interpretation.
                match = re.fullmatch(r"([\d,.]+(?:\s*[KMB])?)\s+(?:videos?|channels?)", displayed, re.I)
                if match:
                    rows.append({"phrase": expected, "kind": "hashtag", "metric": metric,
                                 "value": match[1], "unit": unit,
                                 "notes": "Experimental public-page display; coverage/rounding controlled by YouTube."})
    if not rows:
        raise InputError("No exact matching hashtag header with interpretable counts; layout may have changed.")
    return rows
