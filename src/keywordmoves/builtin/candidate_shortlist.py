"""Select a diverse shortlist from explicit editorial reviews, not demand scores."""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import replace

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginDescriptor, PluginResult
from ..monitoring.validation import canonical, finite, label, normal, read_json, reject_secrets, stamp


class CandidateShortlistPlugin:
    descriptor = PluginDescriptor(
        "candidate-shortlist", "Select explicitly reviewed, distinct-intent candidates without inventing demand or padding coverage.",
        ("composition", "editorial-review", "diversity"), ("select",),
    )

    def run(self, request, context):
        del context
        if request.operation != "select" or request.keywords or len(request.inputs) != 2:
            raise ConfigurationError("candidate-shortlist/select requires review JSON then one saved candidate-pool result; no seeds.")
        options = dict(request.options)
        if set(options) - {"limit", "minimum", "max_per_group", "allow_adjacent", "intent"}:
            raise ConfigurationError("Unknown candidate-shortlist option.")
        values = {}
        for name, default in (("limit", 5), ("minimum", 5), ("max_per_group", 2)):
            value = options.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
                raise ConfigurationError(f"{name} must be an integer from 1 to 50.")
            values[name] = value
        if values["minimum"] > values["limit"]:
            raise ConfigurationError("minimum cannot exceed limit; shortfalls never receive filler.")
        adjacent = options.get("allow_adjacent", False)
        intent = options.get("intent", "listening")
        if not isinstance(adjacent, bool) or not isinstance(intent, str) or intent not in {"listening", "navigation"}:
            raise ConfigurationError("allow_adjacent must be boolean; intent must be listening or navigation.")

        review_raw, review = read_json(request.inputs[0])
        source_raw, source = read_json(request.inputs[1])
        for data in (review, source):
            canonical(data)
            reject_secrets(data)
        if not isinstance(review, dict) or review.get("schema") != "keywordmoves-shortlist-review/v1":
            raise InputError("Expected keywordmoves-shortlist-review/v1.")
        if set(review) - {"schema", "subject", "platform", "reviewed_at", "reviewer_kind", "source_sha256", "decisions"}:
            raise InputError("Unknown shortlist-review field.")
        subject, platform = label(review.get("subject"), "subject"), label(review.get("platform"), "platform")
        reviewed_at = stamp(review.get("reviewed_at"))
        if not isinstance(review.get("reviewer_kind"), str) or review["reviewer_kind"] not in {"human", "analyst", "automated"}:
            raise InputError("reviewer_kind must declare human, analyst or automated; it is a caller claim, not verified authority.")
        source_sha = hashlib.sha256(source_raw).hexdigest()
        if review.get("source_sha256") != source_sha:
            raise InputError("Review is stale or refers to different source bytes.")
        if (not isinstance(source, dict) or not isinstance(source.get("plugin"), str)
                or not isinstance(source.get("keywords"), list) or not isinstance(source.get("metadata"), dict)):
            raise InputError("Expected a saved PluginResult candidate pool with declared target scope.")
        if source["metadata"].get("target_subject") != subject or source["metadata"].get("target_platform") != platform:
            raise InputError("Candidate pool target subject/platform differ from the review.")
        if not 1 <= len(source["keywords"]) <= 50000:
            raise InputError("Candidate pools require 1-50000 rows; no silent truncation.")
        candidates = {}
        for row in source["keywords"]:
            if not isinstance(row, dict):
                raise InputError("Candidate rows must be objects.")
            phrase = label(row.get("phrase"), "phrase")
            key = normal(phrase)
            if key in candidates:
                raise InputError("Duplicate normalized candidate phrase; combine its source evidence explicitly first.")
            metadata, evidence = row.get("metadata", {}), row.get("evidence", [])
            if not isinstance(metadata, dict) or not isinstance(evidence, list):
                raise InputError("Candidate metadata/evidence types are invalid.")
            if metadata.get("target_subject", subject) != subject or metadata.get("target_platform", platform) != platform:
                raise InputError("Candidate-level target scope conflicts with the pool.")
            if row.get("score") is not None:
                finite(row["score"], "score")
            try:
                candidate = KeywordCandidate(phrase, row.get("relationship", "candidate"), row.get("score"),
                    tuple(KeywordEvidence(**e) for e in evidence), metadata)
            except (TypeError, ValueError) as exc:
                raise InputError("Invalid candidate/evidence fields.") from exc
            candidates[key] = candidate

        decisions = review.get("decisions")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 50000:
            raise InputError("Review requires 1-50000 explicit decisions.")
        reviewed, eligible, rejected = set(), [], []
        for row in decisions:
            if not isinstance(row, dict) or set(row) != {"phrase", "fit", "intent", "clarity", "group", "priority", "rationale"}:
                raise InputError("Each decision requires exactly phrase, fit, intent, clarity, group, priority and rationale.")
            phrase = label(row["phrase"], "review phrase")
            key = normal(phrase)
            if key in reviewed or key not in candidates:
                raise InputError("Duplicate or orphan review decision.")
            reviewed.add(key)
            if (not all(isinstance(row[k], str) for k in ("fit", "intent", "clarity"))
                    or row["fit"] not in {"direct", "adjacent", "reject"}
                    or row["intent"] not in {"listening", "navigation", "mixed", "information"}
                    or row["clarity"] not in {"natural", "awkward"}):
                raise InputError("Invalid explicit fit/intent/clarity assessment.")
            priority = row["priority"]
            if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 50000:
                raise InputError("Editorial priority must be a positive bounded integer, never a demand score.")
            group, rationale = label(row["group"], "intent group"), label(row["rationale"], "rationale")
            reason = None
            if row["fit"] == "reject" or (row["fit"] == "adjacent" and not adjacent):
                reason = "fit_ineligible"
            elif row["intent"] != intent:
                reason = "different_intent"
            elif row["clarity"] != "natural":
                reason = "awkward_wording"
            if reason:
                rejected.append({"phrase": phrase, "reason": reason, "rationale": rationale})
            else:
                eligible.append((priority, normal(phrase), group, rationale, row, candidates[key]))
        for key, candidate in candidates.items():
            if key not in reviewed:
                rejected.append({"phrase": candidate.phrase, "reason": "unreviewed"})

        eligible.sort(key=lambda item: item[:2])
        counts, selected = Counter(), []
        review_sha = hashlib.sha256(review_raw).hexdigest()
        for priority, _, group, rationale, decision, candidate in eligible:
            if counts[normal(group)] >= values["max_per_group"]:
                rejected.append({"phrase": candidate.phrase, "reason": "intent_group_cap", "group": group})
                continue
            if len(selected) >= values["limit"]:
                rejected.append({"phrase": candidate.phrase, "reason": "outside_shortlist_limit"})
                continue
            counts[normal(group)] += 1
            selected.append(replace(candidate, metadata={**candidate.metadata, "shortlist_review": {
                "subject": subject, "platform": platform, "reviewed_at": reviewed_at,
                "reviewer_kind": review["reviewer_kind"], "editorial_priority": priority,
                "fit": decision["fit"], "intent": intent, "group": group, "rationale": rationale,
                "source_sha256": source_sha, "review_sha256": review_sha, "not_a_demand_score": True,
                "implementation_authorised": False}}))
        shortfall = max(0, values["minimum"] - len(selected))
        settings = {**values, "allow_adjacent": adjacent, "intent": intent}
        return PluginResult(self.descriptor.name, "select", tuple(selected), (
            "Selection follows explicit caller editorial priorities; it does not establish globally best keywords or validate their fit.",
            "Original candidate scores, evidence and native scopes remain unchanged; editorial priority is not demand.",
            "Unknown demand stays unknown; no volume transfer, cross-source average, lifecycle promotion or filler.",
        ), {"subject": subject, "platform": platform, "reviewer_kind": review["reviewer_kind"],
            "reviewed_at": reviewed_at, "input_rows": len(candidates), "reviewed_rows": len(reviewed),
            "eligible_rows": len(eligible), "selected_rows": len(selected), "shortfall": shortfall,
            "coverage_state": "shortfall" if shortfall else "minimum_met", "rejected": rejected,
            "source_sha256": source_sha, "review_sha256": review_sha, "settings": settings,
            "settings_sha256": hashlib.sha256(canonical(settings).encode()).hexdigest(), "network_requests": 0})
