import json
from pathlib import Path

import pytest

from keywordmoves.builtin.observed_evidence import ObservedEvidencePlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest

FIXTURES = Path(__file__).parent / "fixtures"


def run(path: Path):
    return ObservedEvidencePlugin().run(
        PluginRequest(operation="import-observations", inputs=(path,)),
        ExecutionContext(llms=None),
    )


def test_import_preserves_platform_metric_and_unavailable_state():
    result = run(FIXTURES / "observed_evidence.json")
    assert len(result.keywords) == 2
    assert result.keywords[0].evidence[0].metric == "result_position"
    assert result.keywords[0].metadata["platform"] == "YouTube"
    assert result.keywords[1].metadata["availability"] == "unavailable"
    assert result.metadata["observation_count"] == 2


def test_missing_required_field_fails():
    with pytest.raises(InputError, match="missing required fields"):
        run(FIXTURES / "observed_evidence_missing.json")


def test_subject_and_capture_lineage_survive_import(tmp_path):
    payload = json.loads((FIXTURES / "observed_evidence.json").read_text(encoding="utf-8"))
    fields = {"subject": "A song", "seed_keyword": "a seed",
              "capture_file": "capture.json", "capture_sha256": "a" * 64,
              "evidence_kind": "native-search-sample",
              "limitations": "Sample language is not search volume."}
    payload["observations"][0].update(fields)
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert fields.items() <= run(path).keywords[0].metadata.items()


def test_subject_metadata_rejects_non_text(tmp_path):
    payload = json.loads((FIXTURES / "observed_evidence.json").read_text(encoding="utf-8"))
    payload["observations"][0]["subject"] = {"unexpected": "object"}
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InputError, match="subject must be text"):
        run(path)


def test_wrong_schema_and_operation_fail():
    with pytest.raises(InputError, match="schema"):
        run(FIXTURES / "observed_evidence_wrong_schema.json")
    with pytest.raises(ConfigurationError):
        ObservedEvidencePlugin().run(
            PluginRequest(operation="scrape", inputs=(FIXTURES / "observed_evidence.json",)),
            ExecutionContext(llms=None),
        )
