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


def test_wrong_schema_and_operation_fail():
    with pytest.raises(InputError, match="schema"):
        run(FIXTURES / "observed_evidence_wrong_schema.json")
    with pytest.raises(ConfigurationError):
        ObservedEvidencePlugin().run(
            PluginRequest(operation="scrape", inputs=(FIXTURES / "observed_evidence.json",)),
            ExecutionContext(llms=None),
        )
