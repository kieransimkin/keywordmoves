from __future__ import annotations

import json
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


class ObservedEvidencePlugin:
    """Import dated browser, account and public-tool observations without scraping."""

    descriptor = PluginDescriptor(
        name="observed-evidence",
        summary="Validate and import dated observations from browser-only or authenticated keyword sources.",
        capabilities=("import", "validate", "first-party", "public-tool"),
        operations=("import-observations",),
    )

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        if request.operation not in self.descriptor.operations:
            raise ConfigurationError(
                "observed-evidence operation must be import-observations."
            )
        if len(request.inputs) != 1:
            raise ConfigurationError("Observed evidence import expects exactly one JSON input.")
        path = Path(request.inputs[0])
        if not path.is_file():
            raise InputError(f"Observed evidence file does not exist: {path}.")
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InputError(f"Observed evidence is not valid UTF-8 JSON: {path}.") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "keywordmoves-observations/v1":
            raise InputError("Observed evidence must use schema keywordmoves-observations/v1.")
        rows = payload.get("observations")
        if not isinstance(rows, list) or not rows:
            raise InputError("Observed evidence must contain a non-empty observations array.")

        candidates: list[KeywordCandidate] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise InputError(f"Observation {index} must be an object.")
            required = ("phrase", "source", "metric", "observed_at", "platform")
            missing = [field for field in required if not str(row.get(field, "")).strip()]
            if missing:
                raise InputError(
                    f"Observation {index} is missing required fields: {', '.join(missing)}."
                )
            phrase = str(row["phrase"]).strip()
            evidence = KeywordEvidence(
                source=str(row["source"]).strip(),
                metric=str(row["metric"]).strip(),
                value=row.get("value"),
                unit=str(row["unit"]).strip() if row.get("unit") is not None else None,
                observed_at=str(row["observed_at"]).strip(),
                geography=str(row["geography"]).strip() if row.get("geography") else None,
                notes=str(row["notes"]).strip() if row.get("notes") else None,
            )
            metadata = {
                "platform": str(row["platform"]).strip(),
                "scope": str(row["scope"]).strip() if row.get("scope") else None,
                "target_surface": str(row["target_surface"]).strip() if row.get("target_surface") else None,
                "source_url": str(row["source_url"]).strip() if row.get("source_url") else None,
                "availability": str(row.get("availability", "observed")).strip(),
            }
            candidates.append(
                KeywordCandidate(
                    phrase=phrase,
                    relationship=str(row.get("relationship", "observed-evidence")).strip(),
                    score=float(row["score"]) if row.get("score") is not None else None,
                    evidence=(evidence,),
                    metadata=metadata,
                )
            )
        return PluginResult(
            plugin=self.descriptor.name,
            operation=request.operation,
            keywords=tuple(candidates),
            notes=(
                "Imported observations preserve source metrics and unavailable states; they do not infer demand across platforms.",
                "The importer records browser evidence but does not scrape or authenticate to any source.",
            ),
            metadata={"source_file": str(path.resolve()), "observation_count": len(candidates)},
        )
