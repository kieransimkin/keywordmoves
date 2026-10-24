from __future__ import annotations

import calendar
import csv
import hashlib
import io
import math
import re
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
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
from ..monitoring.validation import stamp
from ..online.common import boolean


def _rows(path: Path | None, captured_bytes: bytes | None = None) -> tuple[list[list[str]], str]:
    if captured_bytes is None and (path is None or not path.is_file()):
        raise InputError(f"Google Trends export does not exist: {path}.")
    try:
        if captured_bytes is None and path.stat().st_size > 5_000_000:
            raise InputError("Google Trends CSV exceeds the 5 MB import bound.")
        raw = captured_bytes if captured_bytes is not None else path.read_bytes()
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


def _period_end(value: str, granularity: str) -> date:
    try:
        start = date.fromisoformat(value)
        if value != start.isoformat():
            raise ValueError
        if granularity == "week":
            return start + timedelta(days=6)
        if granularity == "month":
            if start.day != 1:
                raise ValueError
            return start.replace(day=calendar.monthrange(start.year, start.month)[1])
        return start
    except (ValueError, OverflowError) as exc:
        raise InputError("Completed-period summaries need valid ISO period start dates.") from exc


class GoogleTrendsPlugin:
    descriptor = PluginDescriptor(
        name="google-trends",
        summary="Normalise Google Trends CSV exports into keyword evidence.",
        capabilities=("discover", "analyse", "relative-popularity"),
        operations=("import-interest", "import-related", "collect-browser", "assess-interest", "plan-event-cycles"),
    )

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        if request.operation == "plan-event-cycles":
            from .event_cycles import plan_event_cycles
            return plan_event_cycles(request)
        if request.operation == "collect-browser":
            from ..trends_browser import collect_browser
            return collect_browser(request, context)
        if request.operation == "assess-interest":
            options = {**request.options, "completed_periods_only": True}
            if options.get("search_property") not in {"youtube", "web"}:
                raise ConfigurationError("assess-interest requires search_property=youtube or web.")
            rows, _ = _rows(Path(request.inputs[0])) if len(request.inputs) == 1 else ([], None)
            if not rows:
                raise ConfigurationError("assess-interest requires one native CSV and observed_at.")
            index = _header_index(rows, {"week", "day", "month", "date"})
            names = [_clean_series_name(n).casefold() for n in rows[index][1:] if n.casefold() != "ispartial"]
            periods = [r[0] for r in rows[index + 1:] if r and any(r)]
            if len(names) != len(set(names)) or len(periods) != len(set(periods)) or periods != sorted(periods):
                raise InputError("Demand assessment requires unique terms and chronological non-duplicate periods.")
            parsed = self.import_csv(replace(request, operation="import-interest", options=options))
            comparable = len(parsed.keywords) > 1 and len({str(k.metadata.get("summary_window")) for k in parsed.keywords}) == 1
            means = sorted({k.evidence[0].value for k in parsed.keywords if k.evidence[0].value is not None}, reverse=True)
            keywords = []
            for k in parsed.keywords:
                value = k.evidence[0].value
                points = [p for p in k.metadata["series"] if p.get("eligible_for_summary")]
                missing = any(p["value"] is None and not p["censored"] for p in points)
                censored_count = sum(p["censored"] for p in points)
                bounds = None
                if points and not missing:
                    total = sum(p["value"] or 0 for p in points)
                    bounds = {"lower": math.floor(total / len(points) * 10000) / 10000,
                              "upper": math.ceil((total + censored_count) / len(points) * 10000) / 10000,
                              "upper_exclusive": bool(censored_count),
                              "basis": "bounds on displayed indices with <1 censoring; not a confidence interval or volume estimate"}
                profile = {"mean_index": value, "unit": "index_0_100", "normalization_id": parsed.metadata["source_sha256"],
                           "mean_index_bounds": bounds,
                           "within_export_comparable": comparable, "comparison_terms": names,
                           "cohort_rank": means.index(value) + 1 if comparable and value is not None else None,
                           "positive_periods": sum(p["value"] is not None and p["value"] > 0 for p in points),
                           "threshold_zero_periods": sum(p["value"] == 0 for p in points),
                           "unknown_or_censored_periods": sum(p["value"] is None for p in points),
                           "absolute_monthly_searches": None,
                           "confidence": "relative sampled display only; low-volume values may include statistical noise",
                           "ranking_limit": "Compare ranks/indices only inside this exact export, property, geography and completed window. Never compare independently normalised exports or interpret zero as no searches."}
                keywords.append(replace(k, score=None, metadata={**k.metadata, "demand_profile": profile}))
            return replace(parsed, operation="assess-interest", keywords=tuple(keywords),
                           metadata={**parsed.metadata, "within_export_comparable": comparable}, notes=(
                               "Demand assessment preserves native relative indices; no absolute-volume model or universal score.", *parsed.notes))
        return self.import_csv(request)

    def import_csv(self, request: PluginRequest, *, captured_bytes: bytes | None = None) -> PluginResult:
        if request.operation not in {"import-interest", "import-related"}:
            raise ConfigurationError(
                f"google-trends operation must be one of: {', '.join(self.descriptor.operations)}."
            )
        if captured_bytes is None and len(request.inputs) != 1:
            raise ConfigurationError("Google Trends import expects exactly one CSV input.")
        if captured_bytes is not None and request.inputs:
            raise ConfigurationError("Captured Trends CSV does not accept input paths.")
        path = Path(request.inputs[0]) if captured_bytes is None else None
        rows, raw_sha = _rows(path, captured_bytes)
        options = {**request.options, "source_sha256": raw_sha}
        geography = request.options.get("geography")
        observed_at = request.options.get("observed_at")
        if request.operation == "import-interest":
            return self._interest(rows, path, geography, observed_at, options)
        return self._related(rows, path, geography, observed_at, options)

    def _interest(
        self, rows: list[list[str]], path: Path | None, geography: str | None,
        observed_at: str | None, options: dict,
    ) -> PluginResult:
        index = _header_index(rows, {"week", "day", "month", "date", "time", "region", "subregion"})
        header = rows[index]
        if len(header) < 2:
            raise InputError("The Google Trends interest export has no keyword series.")
        series: dict[str, list[dict]] = defaultdict(list)
        completed_only = boolean(options, "completed_periods_only")
        captured = None
        if completed_only:
            if header[0].casefold() not in {"week", "day", "month", "date"}:
                raise ConfigurationError("Completed-period summaries support Week, Day, Date or Month exports.")
            captured = date.fromisoformat(stamp(observed_at)[:10])
        partial = False
        for row in rows[index + 1:]:
            if not row or not any(row):
                continue
            if len(row) != len(header) or not row[0]:
                raise InputError("Malformed or truncated Trends interest row.")
            row_partial = False
            for column, raw in enumerate(row[1:], start=1):
                if header[column].casefold() == "ispartial":
                    if raw.casefold() not in {"true", "false"}:
                        raise InputError("IsPartial must be true or false.")
                    row_partial = row_partial or raw.casefold() == "true"
            end = _period_end(row[0], header[0].casefold()) if completed_only else None
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
                point = {"period": row[0], "raw_value": raw, "value": value, "censored": censored}
                if completed_only:
                    point.update(period_end=end.isoformat(), is_partial=row_partial,
                                 eligible_for_summary=end < captured and not row_partial)
                series[_clean_series_name(header[column])].append(point)
        candidates = []
        for phrase, points in series.items():
            if not phrase or not points:
                continue
            summary = [p for p in points if p.get("eligible_for_summary", True)]
            values = [point["value"] for point in summary if point["value"] is not None]
            complete = bool(summary) and len(values) == len(summary) and (completed_only or not partial)
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
                    **({"completed_periods_only": True, "summary_observations": len(summary),
                        "excluded_incomplete_periods": len(points) - len(summary),
                        "summary_window": {"start": min(p["period"] for p in summary),
                                           "end": max(p["period_end"] for p in summary)} if summary else None}
                       if completed_only else {}),
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
            {"source_file": str(path.resolve()) if path else None, "source_sha256": options["source_sha256"],
             "observed_at": observed_at, "geography": geography,
             "completed_periods_only": completed_only,
             **{key: options[key] for key in ("window", "scope", "category", "search_property",
                                             "normalization_id") if key in options}},
        )

    def _related(
        self, rows: list[list[str]], path: Path | None, geography: str | None,
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
            metadata={"source_file": str(path.resolve()) if path else None, "source_sha256": sha,
                      "export_headers": headers, "duplicate_rows": duplicate_rows,
                      "geography": geography, "observed_at": observed_at,
                      **{key: options[key] for key in (
                          "seed_keyword", "window", "scope", "category", "search_property",
                          "normalization_id") if key in options}},
        )
