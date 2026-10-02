"""Bounded TikTok JSON/CSV and rendered HTML imports; never executes page code."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ConfigurationError, InputError
from .common import integer, text
from .tiktok_analysis import hashtag, records


def decode_json(body: str) -> Any:
    def invalid_constant(value):
        raise ValueError(value)
    try:
        return json.loads(body, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise InputError("Not valid finite JSON; save a reviewed result, not a login/error page.") from None


def read(path: Path, maximum: int = 5_000_000) -> str:
    try:
        with Path(path).open("rb") as file:
            raw = file.read(maximum + 1)
        if len(raw) > maximum:
            raise InputError("Input exceeds max_input_bytes; split it instead of silently truncating.")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise InputError("Could not read a UTF-8 input file; check its path, encoding and permissions.") from None


def _mapping(value: Any, option: str) -> dict[str, str]:
    value = decode_json(value) if isinstance(value, str) else value
    if not isinstance(value, dict) or not value or any(
        not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in value.items()
    ):
        raise ConfigurationError(f"{option} must be a JSON object mapping output fields to source fields/selectors.")
    return value


def load_records(paths: tuple[Path, ...], options: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    if not paths:
        raise InputError("Supply one or more --input JSON/CSV files.")
    maximum = integer(options, "max_records", 2000, 1, 10000)
    max_bytes = integer(options, "max_input_bytes", 5_000_000, 1024, 20_000_000)
    combined = []
    consumed = 0
    for path in dict.fromkeys(Path(p).resolve() for p in paths):
        body = read(path, max_bytes)
        consumed += len(body.encode("utf-8"))
        if consumed > max_bytes:
            raise InputError("Combined input exceeds max_input_bytes.")
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(io.StringIO(body))
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise InputError("CSV needs unique named columns.")
            rows = []
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise InputError("CSV rows must match the header width.")
                rows.append(row)
                if len(rows) + len(combined) > maximum:
                    raise InputError("CSV exceeds max_records.")
        elif path.suffix.lower() == ".json":
            payload = decode_json(body)
            if "records_path" in options:
                for part in text(options, "records_path").split("."):
                    if not isinstance(payload, dict) or part not in payload:
                        raise InputError("records_path does not identify an existing JSON field.")
                    payload = payload[part]
            elif isinstance(payload, dict):
                if "schema" in payload and payload["schema"] != "keywordmoves-tiktok/v1":
                    raise InputError("Unknown TikTok import schema.")
                if "error" in payload and isinstance(payload["error"], dict) and payload["error"].get("code") != "ok":
                    raise InputError("Saved API response reports an error, not an empty result.")
                payload = payload.get("data", payload)
                if isinstance(payload, dict):
                    payload = payload.get(kind)
            rows = records(payload, maximum)
        else:
            raise InputError("Record imports accept .json or .csv files; use import-html for rendered HTML.")
        if "column_map" in options:
            mapping = _mapping(options["column_map"], "column_map")
            mapped = []
            for row in rows:
                if any(source not in row for source in mapping.values()):
                    raise InputError("A column_map source column is absent; refusing to invent null measurements.")
                mapped.append({out: row[source] for out, source in mapping.items()})
            rows = mapped
        # JSON arrays are the supported representation of CSV list-valued cells.
        for row in rows:
            for key in ("hashtags", "hashtag_names", "approximate_metrics"):
                if isinstance(row.get(key), str):
                    row[key] = decode_json(row[key]) if row[key].strip() else []
        combined.extend(rows)
        records(combined, maximum)
    return combined


def reference_records(paths: tuple[Path, ...], options: Mapping[str, Any]) -> list[dict[str, Any]]:
    maximum = integer(options, "max_input_bytes", 5_000_000, 1024, 20_000_000)
    found = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            found.extend(p for p in sorted(path.rglob("*")) if p.is_file() and p.suffix.lower() in {".txt", ".md", ".lrc", ".csv"})
        else:
            found.append(path)
        if len(found) > 1000:
            raise InputError("Too many reference files; select a smaller directory.")
    rows, total = [], 0
    for path in dict.fromkeys(p.resolve() for p in found):
        if path.suffix.lower() not in {".txt", ".md", ".lrc", ".csv"}:
            raise InputError("Reference inputs must be .txt, .md, .lrc or .csv files.")
        body = read(path, maximum)
        total += len(body.encode("utf-8"))
        rows.append({"id": str(path), "caption": body})
    if "text" in options:
        body = options["text"]
        if not isinstance(body, str):
            raise InputError("Inline reference text must be a string.")
        rows.append({"id": "inline", "caption": body})
        total += len(body.encode("utf-8"))
    if total > maximum:
        raise InputError("Combined reference exceeds max_input_bytes; no silent truncation.")
    if not rows or not any(r["caption"].strip() for r in rows):
        raise InputError("Supply non-empty reference text via --input or --option text=...")
    return rows


def _soup(body: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConfigurationError("Install keywordmoves[online] for HTML extraction.") from None
    return BeautifulSoup(body, "html.parser")


def html_records(body: str, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Selector-driven parsing for user-reviewed Creative Center/Studio/tool pages."""
    soup = _soup(body)
    selector = text(options, "record_selector")
    fields = _mapping(options.get("field_selectors"), "field_selectors")
    try:
        nodes = soup.select(selector)
        if not nodes:
            if "empty_selector" in options and soup.select_one(text(options, "empty_selector")):
                return []
            raise InputError("No matching records. This may be changed markup, JavaScript, a login gate or a challenge.")
        if len(nodes) > integer(options, "max_records", 2000, 1, 10000):
            raise InputError("HTML exceeds max_records.")
        rows = []
        for node in nodes:
            row = {}
            for key, css in fields.items():
                child = node if css == ":self" else node.select_one(css)
                if child is None:
                    raise InputError("A configured field selector did not match; no zero/null was invented.")
                row[key] = child.get_text(" ", strip=True)
            rows.append(row)
        return rows
    except InputError:
        raise
    except Exception:
        # SoupSieve's selector exception types are optional; don't echo HTML or selectors.
        raise ConfigurationError("Invalid HTML selector configuration.") from None


def challenge_page(body: str, tag: str) -> list[dict[str, Any]]:
    """Experimental known page-state shapes. A matching tag and actual counts are required.

    This is deliberately not a private API client, JS executor or challenge solver.
    Schema drift produces an explicit failure, never a guessed zero.
    """
    target = hashtag(tag)
    soup = _soup(body)
    candidates = []
    state = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if state:
        payload = decode_json(state.string or state.get_text())
        if isinstance(payload, dict):
            scope = payload.get("__DEFAULT_SCOPE__", {})
            if isinstance(scope, dict):
                detail = scope.get("webapp.challenge-detail", {})
                info = detail.get("challengeInfo", {}) if isinstance(detail, dict) else {}
                if isinstance(info, dict) and isinstance(info.get("challenge"), dict):
                    candidates.append((info["challenge"], info.get("stats", {})))
    legacy = soup.find("script", id="SIGI_STATE")
    if legacy:
        payload = decode_json(legacy.string or legacy.get_text())
        challenge_module = payload.get("ChallengeModule", {}) if isinstance(payload, dict) else {}
        if isinstance(challenge_module, dict):
            for row in challenge_module.values():
                if isinstance(row, dict):
                    candidates.append((row, row.get("stats", {})))
    for challenge, stats in candidates:
        if not isinstance(stats, dict):
            continue
        name = challenge.get("title")
        try:
            matches = isinstance(name, str) and hashtag(name) == target
        except InputError:
            matches = False
        if not matches:
            continue
        if not any(k in stats for k in ("videoCount", "viewCount")):
            # Some revisions put counts inside the challenge itself.
            stats = challenge
        row = {"hashtag": target, "hashtag_id": challenge.get("id")}
        for native, metric in (("videoCount", "reported_post_count"), ("viewCount", "reported_view_count")):
            if native in stats and stats[native] is not None:
                row[metric] = stats[native]
        if len(row) > 2:
            return [row]
    raise InputError("No verified matching hashtag count in the page. Login/challenge/markup drift or unavailable data; use a reviewed export.")
