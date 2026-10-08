from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)


def _rows(path: Path) -> tuple[list[list[str]], str]:
    if not path.is_file():
        raise InputError(f"Google Trends export does not exist: {path}.")
    try:
        if path.stat().st_size > 5_000_000:
            raise InputError("Google Trends CSV exceeds the 5 MB import bound.")
        raw = path.read_bytes()
        if len(raw) > 5_000_000:
            raise InputError("Google Trends CSV exceeds the 5 MB import bound.")
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        return [[cell.strip() for cell in row] for row in reader], hashlib.sha256(raw).hexdigest()
    except (UnicodeDecodeError, csv.Error, OSError) as exc:
        raise InputError(f"Google Trends export must be readable, valid UTF-8 CSV: {path}.") from exc


def _number(value: str) -> float | None:
    cleaned = value.strip()
    if cleaned not in {"", "-", "<1"} and not re.fullmatch(
            r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?%?", cleaned):
        return None
    cleaned = cleaned.replace(",", "")
    if cleaned in {"", "-", "<1"}:
        return None
    cleaned = cleaned.rstrip("%")
    try:
        value = float(cleaned)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def _header_index(rows: list[list[str]], first_cells: set[str]) -> int:
    for index, row in enumerate(rows):
        if row and row[0].strip().casefold() in first_cells:
            return index
    raise InputError("The CSV does not contain a recognised Google Trends export header.")


def _clean_series_name(value: str) -> str:
    return re.sub(r"\s*:\s*\([^)]*\)\s*$", "", value).strip()


class GoogleTrendsPlugin:
    descriptor = PluginDescriptor(
        name="google-trends",
        summary="Normalise Google Trends CSV exports into keyword evidence.",
        capabilities=("discover", "analyse", "relative-popularity"),
        operations=("import-interest", "import-related"),
    )

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        if request.operation not in self.descriptor.operations:
            raise ConfigurationError(
                f"google-trends operation must be one of: {', '.join(self.descriptor.operations)}."
            )
        if len(request.inputs) != 1:
            raise ConfigurationError("Google Trends import expects exactly one CSV input.")
        path = Path(request.inputs[0])
        rows, raw_sha = _rows(path)
        options = {**request.options, "source_sha256": raw_sha}
        geography = request.options.get("geography")
        observed_at = request.options.get("observed_at")
        if request.operation == "import-interest":
            return self._interest(rows, path, geography, observed_at, options)
        return self._related(rows, path, geography, observed_at, options)

    def _interest(
        self, rows: list[list[str]], path: Path, geography: str | None,
        observed_at: str | None, options: dict,
    ) -> PluginResult:
        index = _header_index(rows, {"week", "day", "month", "date", "time", "region", "subregion"})
        header = rows[index]
        if len(header) < 2:
            raise InputError("The Google Trends interest export has no keyword series.")
        series: dict[str, list[dict]] = defaultdict(list)
        partial = False
        for row in rows[index + 1:]:
            if not row or not any(row):
                continue
            if len(row) != len(header) or not row[0]:
                raise InputError("Malformed or truncated Trends interest row.")
            for column, raw in enumerate(row[1:], start=1):
                if header[column].casefold() == "ispartial":
                    if raw.casefold() not in {"true", "false"}:
                        raise InputError("IsPartial must be true or false.")
                    partial = partial or raw.casefold() == "true"
                    continue
                censored = raw == "<1"
                missing = raw in {"", "-"}
                value = _number(raw)
                if not (censored or missing) and (
                        value is None or not 0 <= value <= 100 or "%" in raw):
                    raise InputError("Interest values must be finite 0..100 indices or explicit missing values.")
                series[_clean_series_name(header[column])].append({
                    "period": row[0], "raw_value": raw, "value": value, "censored": censored})
        candidates = []
        for phrase, points in series.items():
            if not phrase or not points:
                continue
            values = [point["value"] for point in points if point["value"] is not None]
            complete = len(values) == len(points) and not partial
            average = round(mean(values), 4) if complete else None
            peak = max(values) if complete else None
            evidence = (
                KeywordEvidence("Google Trends CSV export", "relative_interest_mean", average,
                                "index_0_100", str(observed_at) if observed_at else None,
                                str(geography) if geography else None,
                                "Relative sampled index; a low-volume zero is not proof of no searches."),
                KeywordEvidence("Google Trends CSV export", "relative_interest_peak", peak,
                                "index_0_100", str(observed_at) if observed_at else None,
                                str(geography) if geography else None),
            )
            candidates.append(KeywordCandidate(
                phrase=phrase.casefold(), relationship="trend-series",
                score=round(average / 100.0, 4) if average is not None else None,
                evidence=evidence, metadata={
                    "observations": len(points), "numeric_observations": len(values),
                    "series": points, "censored": any(point["censored"] for point in points),
                    "completeness": "complete" if complete else "partial",
                    "availability": "observed" if values else "no-data",
                    "platform": "YouTube" if options.get("search_property") == "youtube" else "Google",
                    "evidence_kind": "relative_interest",
                    **{key: options[key] for key in ("window", "scope", "category", "search_property",
                                                    "normalization_id") if key in options},
                }))
        if not candidates:
            raise InputError("No interest series were found; unavailable data are not zero demand.")
        return PluginResult(
            self.descriptor.name, "import-interest", tuple(candidates),
            ("Google Trends values are relative indices, not search-volume counts.",
             "Censored, missing or partial series do not receive exact summary values."),
            {"source_file": str(path.resolve()), "source_sha256": options["source_sha256"],
             "observed_at": observed_at, "geography": geography,
             **{key: options[key] for key in ("window", "scope", "category", "search_property",
                                             "normalization_id") if key in options}},
        )

    def _related(
        self, rows: list[list[str]], path: Path, geography: str | None,
        observed_at: str | None, options: dict,
    ) -> PluginResult:
        section = options.get("section")
        if section is not None and section not in {"top", "rising"}:
            raise ConfigurationError("section must be top or rising.")
        candidates: list[KeywordCandidate] = []
        seen: dict[tuple[str, str], str] = {}
        headers, duplicate_rows = [], 0
        for number, row in enumerate(rows, start=1):
            if not row or not any(row):
                continue
            first = row[0].strip()
            if first.casefold() in {"top", "rising"} and not any(row[1:]):
                section = first.casefold()
                continue
            if first.casefold() in {"related queries", "related topics"} and not any(row[1:]):
                headers.append(row)
                continue
            if first.casefold() in {"query", "queries", "topic", "topics"} and (
                    len(row) < 2 or row[1].casefold() in {"value", "score", "interest"}):
                continue
            if section is None:
                if len(row) >= 2 and (_number(row[1]) is not None
                                     or row[1].casefold() == "breakout"):
                    raise InputError("Related-query values need a Top/Rising section or section option.")
                headers.append(row)
                continue
            if len(row) != 2 or not first:
                raise InputError(f"Malformed related-query data at CSV row {number}.")
            raw = row[1].strip()
            breakout = raw.casefold() == "breakout"
            unavailable = raw in {"", "-"}
            censored = raw == "<1"
            value = _number(raw)
            if section == "top":
                if breakout or "%" in raw or (value is not None and not 0 <= value <= 100):
                    raise InputError("Top query values must be 0..100 relative indices.")
                unit, metric = "index_0_100", "related_top"
            elif breakout:
                unit, metric = "growth_label", "related_rising_breakout"
                value = "Breakout"
            else:
                unit, metric = "percent_growth", "related_rising"
                if censored:
                    raise InputError("A censored Top index cannot be used as Rising growth.")
            if not (unavailable or censored or breakout) and value is None:
                raise InputError(f"Invalid or non-finite Trends value at CSV row {number}.")
            key = (section, first.casefold())
            signature = str(value) + ":" + raw
            if key in seen:
                if seen[key] != signature:
                    raise InputError("Conflicting duplicate query values in the same Trends section.")
                duplicate_rows += 1
                continue
            seen[key] = signature
            evidence = KeywordEvidence(
                source="Google Trends CSV export", metric=metric, value=value, unit=unit,
                observed_at=str(observed_at) if observed_at else None,
                geography=str(geography) if geography else None,
                notes=("Breakout means growth greater than 5000%; no absolute volume."
                       if breakout else "Source-relative Top index or Rising growth, not search volume."),
            )
            context = {key: options[key] for key in (
                "seed_keyword", "window", "scope", "category", "search_property", "normalization_id")
                       if key in options}
            candidates.append(KeywordCandidate(
                phrase=first.casefold(), relationship=f"trends-{section}", score=None,
                evidence=(evidence,), metadata={
                    **context, "section": section, "raw_value": raw, "csv_row": number,
                    "platform": "YouTube" if options.get("search_property") == "youtube" else "Google",
                    "evidence_kind": "relative_interest",
                    "availability": "no-data" if unavailable else "observed",
                    "censored": censored, "approximate": False, "completeness": "complete",
                    "breakout_lower_bound_percent": 5000 if breakout else None,
                },
            ))
        if not candidates:
            raise InputError("No related queries were found; this is unavailable evidence, not zero demand.")
        sha = options["source_sha256"]
        return PluginResult(
            plugin=self.descriptor.name, operation="import-related", keywords=tuple(candidates),
            notes=("Top, Rising and Breakout retain separate units; no cross-platform demand score.",
                   "Different Trends exports may use different normalisation and cannot be compared blindly."),
            metadata={"source_file": str(path.resolve()), "source_sha256": sha,
                      "export_headers": headers, "duplicate_rows": duplicate_rows,
                      "geography": geography, "observed_at": observed_at,
                      **{key: options[key] for key in (
                          "seed_keyword", "window", "scope", "category", "search_property",
                          "normalization_id") if key in options}},
        )
