import json

import pytest

from keywordmoves.builtin.research_batch import ResearchBatchPlugin
from keywordmoves.errors import InputError
from keywordmoves.models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginRequest,
    PluginResult,
)


def invoke(tmp_path, tasks, operation="validate-plan"):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": tasks}), encoding="utf-8")
    return ResearchBatchPlugin().run(PluginRequest(operation, inputs=(path,)), ExecutionContext(None))


def task(**kw):
    return {"id": "one", "subject": "Example Song", "plugin": "datamuse", "operation": "related", "keywords": ["example"], **kw}


def test_plan_validation_offline(tmp_path):
    result = invoke(tmp_path, [task()])
    assert result.metadata["network_requests"] == 0
    assert len(result.metadata["plan_sha256"]) == 64


@pytest.mark.parametrize("tasks", [[task(), task()], [task(plugin="unknown")], [task(options={"api_key": "secret"})],
                                     [task(plugin="ahrefs")], [task(subject=None)], [task(keywords="example")]])
def test_bad_plans_fail_before_collection(tmp_path, tasks):
    with pytest.raises(InputError):
        invoke(tmp_path, tasks)


@pytest.mark.parametrize("ceiling", [0, float("nan"), float("inf"), True])
def test_metered_approval_requires_finite_amount(tmp_path, ceiling):
    with pytest.raises(InputError):
        invoke(tmp_path, [task(plugin="seranking", approval={"confirmed": True, "reference": "synthetic", "charge_unit": "credits", "finite_charge_ceiling": ceiling})])


def test_nested_options_and_mismatched_paid_ceiling_rejected(tmp_path):
    with pytest.raises(InputError):
        invoke(tmp_path, [task(options={"nested": {"api_key": "secret"}})])
    with pytest.raises(InputError):
        invoke(tmp_path, [task(plugin="seranking", options={"paid_requests_approved": True, "approved_max_credits": 200},
             approval={"confirmed": True, "reference": "synthetic", "charge_unit": "credits", "finite_charge_ceiling": 100})])


def test_imports_receive_only_their_explicit_options(tmp_path, monkeypatch):
    class Source:
        def run(self, request, context):
            assert "max_requests" not in request.options
            return PluginResult("native-export", "import-csv")
    monkeypatch.setattr("keywordmoves.registry.PluginRegistry.get", lambda self, name: Source())
    result = invoke(tmp_path, [task(plugin="native-export", operation="import-csv", keywords=[], inputs=["example.csv"])], "collect")
    assert result.metadata["tasks"][0]["availability"] == "no-candidates"


def test_collection_preserves_subject_and_source_units(tmp_path, monkeypatch):
    class Source:
        def run(self, request, context):
            assert request.options["max_requests"] == 1
            if request.keywords == ("missing",):
                raise InputError("secret-sensitive upstream body")
            return PluginResult("synthetic-source", "related", (KeywordCandidate("same phrase", evidence=(KeywordEvidence("Source", "lexical_score", 5, "provider_score"),), metadata={"platform": "Reference language"}),))
    monkeypatch.setattr("keywordmoves.registry.PluginRegistry.get", lambda self, name: Source())
    result = invoke(tmp_path, [task(), task(id="two", subject="Second Song"), task(id="three", keywords=["missing"])], "collect")
    assert len(result.keywords) == 2
    assert {k.metadata["subject"] for k in result.keywords} == {"Example Song", "Second Song"}
    assert result.metadata["unavailable_tasks"] == 1
    assert "secret-sensitive" not in json.dumps(result.to_dict())
    assert all(k.evidence[0].unit == "provider_score" for k in result.keywords)
