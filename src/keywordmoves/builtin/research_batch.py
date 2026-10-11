"""Explicit bounded research plans across existing source adapters."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from ..errors import ConfigurationError, InputError, KeywordMovesError
from ..models import PluginDescriptor, PluginRequest, PluginResult

FREE = {"datamuse", "wikipedia", "wikimedia-pageviews", "itunes-catalog"}
PAID = {"seranking"}
IMPORTS = {"native-export", "observed-evidence", "google-trends", "ubersuggest", "answerthepublic", "vidiq", "candidate-focus", "keywordtool"}
SECRET_KEYS = {"api_key", "access_token", "refresh_token", "developer_token", "password", "login", "client_secret", "token", "authorization", "cookie", "cookies"}


class ResearchBatchPlugin:
    descriptor = PluginDescriptor(
        "research-batch", "Run explicit bounded multi-source plans, retaining source results and unavailable tasks.",
        ("composition", "sources", "bounded-research"), ("collect", "validate-plan"),
    )

    def run(self, request, context):
        if request.operation not in self.descriptor.operations or len(request.inputs) != 1 or request.keywords:
            raise ConfigurationError("research-batch requires one plan JSON and collect or validate-plan.")
        path = Path(request.inputs[0])
        try:
            if path.stat().st_size > 5_000_000:
                raise InputError("Research plan exceeds 5 MB.")
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise InputError("Research plan is not readable UTF-8 JSON.") from exc
        if not isinstance(data, dict) or data.get("schema") != "keywordmoves-research-batch/v1":
            raise InputError("Expected keywordmoves-research-batch/v1.")
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 200:
            raise InputError("A research plan must contain 1-200 explicit tasks.")
        ids = set()
        for task in tasks:
            if not isinstance(task, dict):
                raise InputError("Research tasks must be objects.")
            for field in ("id", "subject", "plugin", "operation"):
                if not isinstance(task.get(field), str) or not task[field].strip():
                    raise InputError("Research tasks require text id, subject, plugin and operation.")
            if task["id"] in ids:
                raise InputError("Research task ids must be unique.")
            ids.add(task["id"])
            plugin = task["plugin"]
            if plugin not in FREE | PAID | IMPORTS:
                raise InputError("Source is not supported by the batch contract; use its dedicated module.")
            if plugin == "keywordtool" and task["operation"] != "import-json":
                raise InputError("Research batches permit only offline Keyword Tool import-json; live calls may be metered.")
            options = task.get("options", {})
            if (not isinstance(options, dict)
                or any(not isinstance(k, str) or k.lower() in SECRET_KEYS or k.lower().endswith(("_key", "_token", "_secret", "_password")) for k in options)
                or any(not isinstance(v, (str, int, float, bool, type(None))) for v in options.values())):
                raise InputError("Plan options must be an object without credentials; use environment/OS storage.")
            words, inputs = task.get("keywords", []), task.get("inputs", [])
            if not isinstance(words, list) or not all(isinstance(x, str) and x.strip() for x in words):
                raise InputError("Task keywords must be a list of non-empty strings.")
            if not isinstance(inputs, list) or not all(isinstance(x, str) and x.strip() for x in inputs):
                raise InputError("Task inputs must be a list of non-empty paths.")
            if plugin in PAID:
                approval = task.get("approval", {})
                if not isinstance(approval, dict) or approval.get("confirmed") is not True:
                    raise InputError("Each metered task requires its own explicit confirmed approval record.")
                if not isinstance(approval.get("reference"), str) or not approval["reference"].strip():
                    raise InputError("Metered tasks require an approval reference.")
                ceiling = approval.get("finite_charge_ceiling")
                if not isinstance(ceiling, (float, int)) or isinstance(ceiling, bool) or not 0 < ceiling <= 1_000_000:
                    raise InputError("Metered task requires a positive finite charge ceiling.")
                if not isinstance(approval.get("charge_unit"), str) or not approval["charge_unit"].strip():
                    raise InputError("Metered task requires its charge unit.")
                if (approval["charge_unit"] != "credits"
                    or options.get("approved_max_credits") != ceiling
                    or options.get("paid_requests_approved") is not True):
                    raise InputError("SE Ranking approval must match the adapter credit ceiling and paid opt-in.")
        if request.operation == "validate-plan":
            return PluginResult(self.descriptor.name, request.operation, metadata={"valid": True, "task_count": len(tasks), "network_requests": 0,
                "plan_path": str(path), "plan_sha256": hashlib.sha256(raw).hexdigest()})
        from ..registry import PluginRegistry
        registry = PluginRegistry()
        results, candidates = [], []
        for task in tasks:
            options = dict(task.get("options", {}))
            if task["plugin"] not in IMPORTS:
                options.setdefault("max_requests", 1)
            try:
                result = registry.get(task["plugin"]).run(
                    PluginRequest(task["operation"], tuple(task.get("keywords", [])),
                                  tuple(Path(x) for x in task.get("inputs", [])), options), context)
            except KeywordMovesError as exc:
                # Do not retain arbitrary upstream error bodies or secrets in a composite result.
                results.append({"id": task["id"], "subject": task["subject"], "plugin": task["plugin"],
                                "availability": "unavailable", "error_kind": type(exc).__name__})
                continue
            results.append({"id": task["id"], "subject": task["subject"], "plugin": task["plugin"],
                            "availability": "returned" if result.keywords else "no-candidates",
                            "result": result.to_dict()})
            candidates.extend(replace(k, metadata={**dict(k.metadata), "subject": task["subject"],
                                                   "batch_task": task["id"], "source_plugin": task["plugin"]}) for k in result.keywords)
        return PluginResult(self.descriptor.name, request.operation, tuple(candidates),
                            ("Sources, units and task subjects remain separate; no averaged volume or universal score.",
                             "Returned candidates require human fit/intent review; missing results are unavailable, not zero.",
                             "Explicit tasks only; no automatic seed expansion, retries, pagination or provider fallback.",),
                            {"tasks": results, "task_count": len(tasks), "plan_path": str(path), "plan_sha256": hashlib.sha256(raw).hexdigest(),
                             "unavailable_tasks": sum(t["availability"] == "unavailable" for t in results)})
