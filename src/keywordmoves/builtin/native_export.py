"""Reviewed tabular exports with explicit columns and source-native metric labels."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)
from ..monitoring.validation import KINDS, canonical, now_stamp, reject_secrets, stamp

OPTIONS = {
    "platform", "source", "metric", "unit", "observed_at", "geography", "scope",
    "window", "evidence_kind", "subject", "phrase_column", "value_column",
    "metric_column", "unit_column", "date_column", "subject_column", "geography_column",
    "period_start", "period_end", "normalization_id", "dimensions_json",
    "completeness", "approximate", "delimiter",
}


def _text(options: dict, key: str) -> str:
    value = options.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"native-export requires {key} or its explicit column mapping.")
    return value.strip()


def _value(raw: str, unit: str) -> tuple[Any, bool]:
    if raw in {"", "-", "—", "N/A", "n/a"}:
        return None, False
    numeric = raw.strip()
    if numeric.endswith("%"):
        if unit not in {"percent", "percent_growth", "percentage_points"}:
            raise InputError("Percentage displays require an explicitly compatible unit.")
        numeric = numeric[:-1].strip()
    if re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", numeric):
        number = float(numeric.replace(",", ""))
        if not math.isfinite(number):
            raise InputError("Non-finite export values are not accepted.")
        return int(number) if number.is_integer() else number, False
    if numeric.casefold() in {"nan", "inf", "infinity", "-inf", "+inf"}:
        raise InputError("Non-finite export values are not accepted.")
    censored = bool(re.match(r"^[<>~≈]", raw) or re.fullmatch(r"\d+\s*[-–]\s*\d+", raw))
    return raw, censored


class NativeExportPlugin:
    descriptor = PluginDescriptor(
        "native-export", "Import reviewed platform CSV exports with explicit source metrics.",
        ("import", "validate", "native-observations"), ("import-csv",),
    )

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        if request.operation != "import-csv" or len(request.inputs) != 1 or request.keywords:
            raise ConfigurationError("native-export import-csv expects exactly one CSV input.")
        options = dict(request.options)
        if set(options) - OPTIONS:
            raise ConfigurationError("Unknown native-export option; use docs/native-export.md.")
        path = Path(request.inputs[0])
        try:
            if path.stat().st_size > 5_000_000:
                raise InputError("Native CSV exceeds the 5 MB bound.")
            raw = path.read_bytes()
            content = raw.decode("utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise InputError("Native export must be readable UTF-8 CSV.") from exc
        delimiter = options.get("delimiter", ",")
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise ConfigurationError("delimiter must be exactly one character.")
        reader = csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter, strict=True)
        try:
            fields = reader.fieldnames
        except csv.Error as exc:
            raise InputError("Malformed CSV header.") from exc
        if not fields or len({field.strip().casefold() for field in fields}) != len(fields):
            raise InputError("Native exports need a header with unambiguous column names.")
        mappings = {key: options[key] for key in options if key.endswith("_column")}
        for column in mappings.values():
            if column not in fields:
                raise InputError("A declared column is absent from the native export.")
        phrase_column = _text(options, "phrase_column")
        value_column = _text(options, "value_column")
        if phrase_column not in fields or value_column not in fields:
            raise InputError("The phrase/value columns must exist in the export.")
        common = {key: _text(options, key) for key in
                  ("platform", "source", "geography", "scope", "window", "evidence_kind")
                  if key != "geography" or "geography_column" not in options}
        if common["evidence_kind"] not in KINDS:
            raise ConfigurationError("Use a documented evidence_kind.")
        for key in ("metric", "unit", "observed_at"):
            column = {"metric": "metric_column", "unit": "unit_column",
                      "observed_at": "date_column"}[key]
            if column not in options:
                common[key] = _text(options, key)
        completeness = options.get("completeness", "unknown")
        if completeness not in {"complete", "partial", "unknown"}:
            raise ConfigurationError("completeness must be complete, partial or unknown.")
        approximate = options.get("approximate", common["evidence_kind"] == "search_volume_estimate")
        if not isinstance(approximate, bool):
            raise ConfigurationError("approximate must be true or false.")
        try:
            dimensions = json.loads(str(options.get("dimensions_json", "{}")))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("dimensions_json must be a JSON object.") from exc
        if not isinstance(dimensions, dict):
            raise ConfigurationError("dimensions_json must be a JSON object.")
        canonical(dimensions)
        reject_secrets(dimensions)
        keywords = []
        try:
            for row_number, row in enumerate(reader, start=2):
                if row_number > 50001:
                    raise InputError("Native CSV exceeds the 50000-row bound.")
                if None in row or any(value is None for value in row.values()):
                    raise InputError(f"Malformed or truncated CSV row {row_number}.")
                phrase = row[phrase_column].strip()
                if not phrase:
                    raise InputError(f"Missing phrase at CSV row {row_number}.")
                metric = row[options["metric_column"]].strip() if "metric_column" in options else common["metric"]
                unit = row[options["unit_column"]].strip() if "unit_column" in options else common["unit"]
                captured = row[options["date_column"]].strip() if "date_column" in options else common["observed_at"]
                captured = stamp(captured)
                if captured > now_stamp():
                    raise InputError("Native report captures cannot be in the future.")
                geography = row[options["geography_column"]].strip() if "geography_column" in options else common["geography"]
                if not metric or not unit or not geography:
                    raise InputError("Every metric needs its actual unit and geographic scope.")
                display = row[value_column].strip()
                value, censored = _value(display, unit)
                metadata = {
                    **{key: common[key] for key in ("platform", "scope", "window", "evidence_kind")},
                    "raw_value": display, "csv_row": row_number,
                    "availability": "unavailable" if value is None else "observed",
                    "censored": censored, "approximate": approximate,
                    "completeness": completeness, "dimensions": dimensions,
                    **{key: options[key] for key in ("period_start", "period_end", "normalization_id")
                       if key in options},
                }
                if "subject_column" in options:
                    metadata["subject"] = row[options["subject_column"]].strip()
                elif "subject" in options:
                    metadata["subject"] = _text(options, "subject")
                keywords.append(KeywordCandidate(
                    phrase=phrase, relationship="native-export-observation", score=None,
                    evidence=(KeywordEvidence(common["source"], metric, value, unit, captured,
                                              geography, "Reviewed export; source labels retained."),),
                    metadata=metadata,
                ))
        except csv.Error as exc:
            raise InputError("Malformed quoted native CSV data.") from exc
        return PluginResult(
            self.descriptor.name, request.operation, tuple(keywords),
            ("This imports a reviewed file; it does not obtain platform access or infer search volume.",
             "Missing, rounded and censored displays remain distinct from exact measurements."),
            {"source_file": str(path.resolve()), "source_sha256": hashlib.sha256(raw).hexdigest(),
             "row_count": len(keywords), "column_mapping": mappings,
             "output_truncated": False},
        )
