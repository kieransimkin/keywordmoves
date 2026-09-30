from __future__ import annotations

import csv
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


def _rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        raise InputError(f"Google Trends export does not exist: {path}.")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [[cell.strip() for cell in row] for row in csv.reader(handle)]
    except UnicodeDecodeError as exc:
        raise InputError(f"Google Trends export is not UTF-8: {path}.") from exc


def _number(value: str) -> float | None:
    cleaned = value.strip().replace(",", "")
    if cleaned in {"", "-", "<1"}:
        return 0.0 if cleaned == "<1" else None
    cleaned = cleaned.rstrip("%")
    try:
        return float(cleaned)
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
        rows = _rows(path)
        geography = request.options.get("geography")
        observed_at = request.options.get("observed_at")
        if request.operation == "import-interest":
            return self._interest(rows, path, geography, observed_at)
        return self._related(rows, path, geography, observed_at)

    def _interest(
        self,
        rows: list[list[str]],
        path: Path,
        geography: str | None,
        observed_at: str | None,
    ) -> PluginResult:
        index = _header_index(rows, {"week", "day", "month", "date", "region", "subregion"})
        header = rows[index]
        if len(header) < 2:
            raise InputError("The Google Trends interest export has no keyword series.")
        series: dict[str, list[float]] = defaultdict(list)
        for row in rows[index + 1 :]:
            if not row or not row[0]:
                continue
            for column, raw in enumerate(row[1:], start=1):
                if column >= len(header):
                    break
                value = _number(raw)
                if value is not None:
                    series[_clean_series_name(header[column])].append(value)
        candidates: list[KeywordCandidate] = []
        for phrase, values in series.items():
            if not phrase or not values:
                continue
            average = round(mean(values), 4)
            evidence = (
                KeywordEvidence(
                    source="Google Trends CSV export",
                    metric="relative_interest_mean",
                    value=average,
                    unit="index_0_100",
                    observed_at=str(observed_at) if observed_at else None,
                    geography=str(geography) if geography else None,
                    notes="Relative, normalised interest in this export; not absolute search volume.",
                ),
                KeywordEvidence(
                    source="Google Trends CSV export",
                    metric="relative_interest_peak",
                    value=max(values),
                    unit="index_0_100",
                    observed_at=str(observed_at) if observed_at else None,
                    geography=str(geography) if geography else None,
                ),
            )
            candidates.append(
                KeywordCandidate(
                    phrase=phrase.casefold(),
                    relationship="trend-series",
                    score=round(average / 100.0, 4),
                    evidence=evidence,
                    metadata={"observations": len(values)},
                )
            )
        if not candidates:
            raise InputError("No numeric interest observations were found in the export.")
        return PluginResult(
            plugin=self.descriptor.name,
            operation="import-interest",
            keywords=tuple(candidates),
            notes=("Google Trends values are relative indices, not search-volume counts.",),
            metadata={"source_file": str(path.resolve())},
        )

    def _related(
        self,
        rows: list[list[str]],
        path: Path,
        geography: str | None,
        observed_at: str | None,
    ) -> PluginResult:
        section = "related"
        candidates: list[KeywordCandidate] = []
        seen: set[str] = set()
        for row in rows:
            if not row or not any(row):
                continue
            first = row[0].strip()
            if first.casefold() in {"top", "rising", "breakout"}:
                section = first.casefold()
                continue
            if first.casefold() in {"related queries", "query", "queries", "topic", "topics"}:
                continue
            phrase = first.casefold()
            if not phrase or phrase in seen:
                continue
            raw_value = row[1].strip() if len(row) > 1 else ""
            numeric = _number(raw_value)
            breakout = raw_value.casefold() == "breakout"
            if numeric is None and not breakout:
                continue
            seen.add(phrase)
            evidence = KeywordEvidence(
                source="Google Trends CSV export",
                metric=f"related_{section}",
                value="Breakout" if breakout else numeric,
                unit="relative_index_or_growth",
                observed_at=str(observed_at) if observed_at else None,
                geography=str(geography) if geography else None,
                notes="A related-query signal from the exported Trends comparison.",
            )
            candidates.append(
                KeywordCandidate(
                    phrase=phrase,
                    relationship=f"trends-{section}",
                    score=None if breakout or numeric is None else min(float(numeric) / 100.0, 1.0),
                    evidence=(evidence,),
                )
            )
        if not candidates:
            raise InputError("No related queries were found in the export.")
        return PluginResult(
            plugin=self.descriptor.name,
            operation="import-related",
            keywords=tuple(candidates),
            notes=("Related and breakout labels are Google Trends signals, not search-volume counts.",),
            metadata={"source_file": str(path.resolve())},
        )

