"""Offline literal hashtag discovery, explicit review and final-copy checking."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from datetime import datetime

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginDescriptor, PluginResult
from ..monitoring.validation import (
    canonical,
    digest,
    label,
    normal,
    read_json,
    reject_secrets,
    stamp,
)
from ..online.youtube_analysis import hashtag, hashtag_spans

# Conservative editorial ceilings, NOT platform limits or proven optimal counts.
PROFILES = {
    "youtube": (3, "description"), "tiktok": (5, "caption"),
    "instagram": (5, "caption"), "facebook": (2, "caption"),
    "x": (2, "post"), "bluesky": (2, "post"),
    "mastodon": (3, "post"), "linkedin": (3, "post"),
    "threads": (0, "native_topic"), "pinterest": (0, "keywords_and_topics"),
    "reddit": (0, "community_title_and_flair"), "wordpress": (0, "natural_copy_and_taxonomy"),
    "spotify": (0, "native_metadata"), "amazon-music": (0, "native_metadata"),
    "apple-music": (0, "native_metadata"), "bandcamp": (0, "native_tags"),
    "soundcloud": (0, "native_tags"),
}
ROLES = {"brand", "niche", "topic", "format", "trend"}
GENERIC = {"#fyp", "#foryou", "#foryoupage", "#viral", "#trending", "#explorepage"}
TREND_METRICS = {"native_trend_rank", "native_trend_index", "relative_interest"}


def _object(path):
    raw, data = read_json(path)
    canonical(data)
    reject_secrets(data)
    if not isinstance(data, dict):
        raise InputError("Expected a JSON object.")
    return raw, data


def _fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise InputError("Unexpected or missing hashtag-document fields.")


def _platform(value):
    value = normal(label(value, "platform"))
    if value not in PROFILES:
        raise InputError("Unsupported platform; use an explicit documented profile.")
    return value


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise InputError("Expected an exact lowercase SHA-256.")
    return value


def _age(later, earlier):
    days = (datetime.fromisoformat(stamp(later)) - datetime.fromisoformat(stamp(earlier))).total_seconds() / 86400
    if days < 0:
        raise InputError("Evidence/review cannot be dated after its decision or use.")
    return days


def _tag(value):
    value = label(value, "literal hashtag")
    if not value.startswith("#"):
        raise InputError("A hashtag candidate must include its literal #; search phrases are separate.")
    tag = hashtag(value)
    if not any(unicodedata.category(c).startswith("L") for c in tag):
        raise InputError("Hashtags need a letter; numbers-only tags are not portable.")
    return tag


def _native_target(data):
    _fields(data, {"account_id", "object_id", "before_metadata_sha256"})
    for key in ("account_id", "object_id"):
        if data[key] != label(data[key], key):
            raise InputError("Native identifiers must be exact, without normalization or surrounding space.")
    _hash(data["before_metadata_sha256"])
    return data


def _native_fields(value):
    # Only human-readable copy: not video tags, visibility, schedules or media.
    if not isinstance(value, dict) or not value or set(value) - {"title", "description", "caption", "post"}:
        raise InputError("Native fields require named title/description/caption/post text.")
    if any(not isinstance(text, str) or len(text) > 100000 for text in value.values()):
        raise InputError("Native field text must be at most 100000 characters per field.")
    return value


def _native_before(path, subject, platform, target, checked_at):
    raw, before = _object(path)
    _fields(before, {"schema", "subject", "platform", "account_id", "object_id", "captured_at", "fields", "native_topic"})
    if before["schema"] != "keywordmoves-native-metadata/v1":
        raise InputError("Expected keywordmoves-native-metadata/v1.")
    if (before["subject"] != subject or before["platform"] != platform
            or any(before[k] != target[k] for k in ("account_id", "object_id"))
            or hashlib.sha256(raw).hexdigest() != target["before_metadata_sha256"]):
        raise InputError("Native object/account/scope/baseline hash differ from review.")
    _native_fields(before["fields"])
    if before["native_topic"] is not None:
        label(before["native_topic"], "native topic")
        if platform != "threads":
            raise InputError("native_topic is reserved for Threads.")
    _age(checked_at, before["captured_at"])
    return before


class HashtagPlannerPlugin:
    descriptor = PluginDescriptor(
        "hashtag-planner", "Discover literal tags in permitted captures, review scoped sets and audit final copy.",
        ("hashtags", "offline", "editorial-review", "caption-audit"),
        ("profiles", "discover", "plan", "audit", "plan-native", "audit-native"),
    )

    def run(self, request, context):
        del context
        if request.keywords:
            raise ConfigurationError("Use saved evidence documents, not unscoped seeds.")
        if request.operation == "profiles":
            if request.inputs or request.options:
                raise ConfigurationError("profiles takes no inputs/options.")
            return PluginResult("hashtag-planner", "profiles", metadata={
                "profiles": {k: {"editorial_ceiling": v[0], "surface": v[1]} for k, v in PROFILES.items()},
                "not_platform_limits_or_optimal_counts": True, "network_requests": 0})
        expected = {"discover": 1, "plan": 2, "audit": 2, "plan-native": 3, "audit-native": 3}
        if request.operation not in expected or len(request.inputs) != expected[request.operation] or request.options:
            raise ConfigurationError("discover: capture; plan: pool/review; audit: plan/copy; plan-native: pool/review/baseline; audit-native: plan/baseline/copy. No options.")
        if request.operation == "plan-native":
            return self._plan(request.inputs, native_metadata=True)
        if request.operation == "audit-native":
            return self._audit_native(request.inputs)
        return getattr(self, "_" + request.operation)(request.inputs)

    def _discover(self, inputs):
        raw, data = _object(inputs[0])
        _fields(data, {"schema", "subject", "platform", "captured_at", "source", "source_kind", "geography", "window", "records"})
        if data["schema"] != "keywordmoves-hashtag-capture/v1":
            raise InputError("Expected keywordmoves-hashtag-capture/v1.")
        subject, platform = label(data["subject"], "subject"), _platform(data["platform"])
        captured = stamp(data["captured_at"])
        source = label(data["source"], "source")
        geography, window = label(data["geography"], "geography"), label(data["window"], "window")
        kind = data["source_kind"]
        if not isinstance(kind, str) or kind not in {"public_sample", "owned_copy", "local_proposal"}:
            raise InputError("source_kind must be public_sample, owned_copy or local_proposal.")
        records = data["records"]
        if not isinstance(records, list) or len(records) > 5000:
            raise InputError("Supply 0-5000 permitted records; no silent truncation.")
        seen, counts, displays, refs = set(), Counter(), {}, {}
        for row in records:
            _fields(row, {"id", "text", "reference"})
            identity, reference = label(row["id"], "record id"), label(row["reference"], "reference")
            if normal(identity) in seen:
                raise InputError("Duplicate record ID would inflate sample counts.")
            seen.add(normal(identity))
            text = row["text"]
            if not isinstance(text, str) or len(text) > 100000:
                raise InputError("Record text must be a string of at most 100000 characters.")
            found = set()
            for _, start, end in hashtag_spans(text):
                value = _tag(text[start:end])
                displays.setdefault(value, unicodedata.normalize("NFC", text[start:end]))
                found.add(value)
            for tag in sorted(found):
                counts[tag] += 1
                refs.setdefault(tag, []).append({"id": identity, "reference": reference})
        candidates = tuple(KeywordCandidate(displays[tag], evidence=(KeywordEvidence(
            source, "captured_records_with_tag", count, "records", captured, geography,
            "Occurrence within the supplied capture only; not search demand, trend or per-tag reach."),),
            metadata={"kind": "hashtag", "target_subject": subject, "target_platform": platform,
                "source_kind": kind, "window": window, "references": refs[tag],
                "external_demand": "unvalidated", "implementation_authorised": False})
            for tag, count in sorted(counts.items()))
        return PluginResult("hashtag-planner", "discover", candidates, (
            "Literal source spelling is retained for accessible display; matching uses normalized identity.",
            "Captured usage does not prove relevance, safety, demand, acceptance or causal benefit.",
        ), {"schema": "keywordmoves-hashtag-pool/v1", "target_subject": subject,
            "target_platform": platform, "captured_at": captured, "source_kind": kind,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "record_count": len(records), "network_requests": 0})

    def _plan(self, inputs, native_metadata=False):
        raw, pool = _object(inputs[0])
        review_raw, review = _object(inputs[1])
        _fields(review, {"schema", "subject", "platform", "reviewed_at", "planned_for", "native_target" if native_metadata else "asset_sha256",
            "source_sha256", "native_check", "decisions"}, {"native_topic"})
        review_schema = "keywordmoves-hashtag-native-review/v1" if native_metadata else "keywordmoves-hashtag-review/v1"
        if review["schema"] != review_schema:
            raise InputError(f"Expected {review_schema}.")
        source_sha = hashlib.sha256(raw).hexdigest()
        if review["source_sha256"] != source_sha:
            raise InputError("Review source hash is stale or refers to other bytes.")
        subject, platform = label(review["subject"], "subject"), _platform(review["platform"])
        reviewed, planned = stamp(review["reviewed_at"]), stamp(review["planned_for"])
        review_age = _age(planned, reviewed)
        binding = {"asset_sha256": _hash(review["asset_sha256"])} if not native_metadata else {
            "native_target": _native_target(review["native_target"]), "target_kind": "existing_native_metadata",
            "media_provenance_checked": False}
        if native_metadata:
            before = _native_before(inputs[2], subject, platform, binding["native_target"], reviewed)
            binding["before_captured_at"] = stamp(before["captured_at"])
        topic = review.get("native_topic")
        if topic is not None:
            label(topic, "native topic")
            if platform != "threads":
                raise InputError("native_topic is reserved for Threads.")
        meta = pool.get("metadata")
        if not isinstance(meta, dict) or meta.get("target_subject") != subject or _platform(meta.get("target_platform")) != platform:
            raise InputError("Pool and review subject/platform must agree exactly.")
        native = review["native_check"]
        _fields(native, {"state", "checked_at", "reference", "notes"})
        if not isinstance(native["state"], str) or native["state"] not in {"checked", "unavailable"}:
            raise InputError("Native check state must be checked or unavailable.")
        native_age = _age(planned, native["checked_at"])
        _age(reviewed, native["checked_at"])
        label(native["reference"], "native reference")
        label(native["notes"], "native notes")
        rows, decisions = pool.get("keywords"), review["decisions"]
        if not isinstance(rows, list) or len(rows) > 5000 or not isinstance(decisions, list) or len(decisions) > 5000:
            raise InputError("Candidate and decision lists require at most 5000 rows.")
        candidates = {}
        for row in rows:
            if not isinstance(row, dict):
                raise InputError("Candidate must be an object.")
            tag = _tag(row.get("phrase"))
            if tag in candidates:
                raise InputError("Duplicate normalized tag: merge its evidence first.")
            metadata, evidence = row.get("metadata", {}), row.get("evidence", [])
            if not isinstance(metadata, dict) or not isinstance(evidence, list):
                raise InputError("Invalid candidate metadata/evidence.")
            if metadata.get("target_subject", subject) != subject or _platform(metadata.get("target_platform", platform)) != platform:
                raise InputError("Candidate scope conflicts with review.")
            try:
                candidate = KeywordCandidate(row["phrase"], evidence=tuple(KeywordEvidence(**e) for e in evidence), metadata=metadata)
            except (TypeError, ValueError) as exc:
                raise InputError("Invalid candidate evidence.") from exc
            for e in candidate.evidence:
                label(e.source, "evidence source")
                label(e.metric, "evidence metric")
                if isinstance(e.value, bool) or not isinstance(e.value, (str, int, float, type(None))):
                    raise InputError("Evidence values must be scalar observations, not booleans/objects.")
                if e.observed_at:
                    _age(reviewed, e.observed_at)
            candidates[tag] = candidate
        seen, eligible, rejected = set(), [], []
        for row in decisions:
            _fields(row, {"phrase", "fit", "role", "safety", "priority", "rationale"})
            tag = _tag(row["phrase"])
            if tag in seen or tag not in candidates:
                raise InputError("Duplicate or orphan hashtag decision.")
            seen.add(tag)
            if not all(isinstance(row[k], str) for k in ("fit", "role", "safety")) or row["fit"] not in {"direct", "adjacent", "reject"} or row["role"] not in ROLES or row["safety"] not in {"clear", "hold", "reject"}:
                raise InputError("Invalid fit/role/safety review.")
            priority = row["priority"]
            if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5000:
                raise InputError("priority must be an editorial integer 1-5000, not popularity.")
            rationale = label(row["rationale"], "review rationale")
            candidate = candidates[tag]
            dates = [e.observed_at for e in candidate.evidence if e.observed_at]
            freshest = min((_age(planned, d) for d in dates), default=None)
            trend = any(e.metric in TREND_METRICS and e.observed_at and _age(planned, e.observed_at) <= 7 and e.value is not None for e in candidate.evidence)
            local = candidate.metadata.get("source_kind", meta.get("source_kind")) == "local_proposal"
            reason = None
            if row["fit"] != "direct":
                reason = "not_directly_relevant"
            elif row["safety"] != "clear":
                reason = "safety_hold_or_rejection"
            elif tag in GENERIC:
                reason = "generic_reach_bait"
            elif review_age > 14:
                reason = "review_expires_before_planned_use"
            elif row["role"] == "trend" and (native["state"] != "checked" or native_age > 7 or not trend or local):
                reason = "trend_needs_recent_native_evidence"
            elif not local and (freshest is None or freshest > 30):
                reason = "discovery_evidence_missing_or_stale"
            if reason:
                rejected.append({"phrase": candidate.phrase, "reason": reason, "rationale": rationale})
            else:
                eligible.append((priority, tag, row, candidate))
        rejected.extend({"phrase": c.phrase, "reason": "unreviewed"} for tag, c in candidates.items() if tag not in seen)
        ceiling, surface = PROFILES[platform]
        counts, selected = Counter(), []
        for _, tag, row, candidate in sorted(eligible, key=lambda r: r[:2]):
            reason = "native_surface_instead_of_hashtags" if ceiling == 0 else "editorial_ceiling" if len(selected) >= ceiling else "role_cap" if counts[row["role"]] >= (1 if row["role"] in {"brand", "format", "trend"} else 2) else None
            if reason:
                rejected.append({"phrase": candidate.phrase, "reason": reason})
                continue
            counts[row["role"]] += 1
            selected.append(KeywordCandidate(candidate.phrase, evidence=candidate.evidence,
                metadata={**candidate.metadata, "role": row["role"], "rationale": row["rationale"],
                    "editorial_priority": row["priority"], "normalized_tag": tag}))
        needs_check = native["state"] != "checked" or native_age > 7 or review_age > 14
        if "native_target" in binding and _age(planned, binding["before_captured_at"]) > 7:
            needs_check = True
        state = "needs_native_recheck" if needs_check else "native_surface" if ceiling == 0 else "reviewed_proposal"
        is_native = "native_target" in binding
        return PluginResult("hashtag-planner", "plan-native" if is_native else "plan", tuple(selected), (
            "Counts are editorial ceilings, not platform limits or proven optima; zero tags is valid.",
            "Reviews/native-check claims are supplied by the caller; this tool cannot verify them.",
            "No public use is authorised and no reach improvement is demonstrated.",
        ), {"schema": "keywordmoves-hashtag-native-plan/v1" if is_native else "keywordmoves-hashtag-plan/v1", "target_subject": subject, "target_platform": platform,
            **binding, "source_sha256": source_sha, "review_sha256": hashlib.sha256(review_raw).hexdigest(),
            "reviewed_at": reviewed, "planned_for": planned, "native_check": native, "native_topic": topic,
            "state": state, "surface": surface, "editorial_ceiling": ceiling,
            "caption_hashtags": " ".join(c.phrase for c in selected), "rejected": rejected,
            "implementation_authorised": False, "network_requests": 0})

    def _audit_native(self, inputs):
        raw, plan = _object(inputs[0])
        copy_raw, copy = _object(inputs[2])
        _fields(copy, {"schema", "subject", "platform", "native_target", "plan_sha256", "checked_at", "fields", "native_topic"})
        if copy["schema"] != "keywordmoves-hashtag-native-final-copy/v1":
            raise InputError("Expected keywordmoves-hashtag-native-final-copy/v1.")
        meta = plan.get("metadata")
        if (not isinstance(meta, dict) or meta.get("schema") != "keywordmoves-hashtag-native-plan/v1"
                or plan.get("operation") != "plan-native" or plan.get("plugin") != "hashtag-planner"
                or meta.get("target_kind") != "existing_native_metadata"):
            raise InputError("Expected a saved native metadata plan, not an asset-bound plan.")
        platform = _platform(copy["platform"])
        target = _native_target(copy["native_target"])
        if (meta.get("target_subject") != copy["subject"] or meta.get("target_platform") != platform
                or meta.get("native_target") != target or hashlib.sha256(raw).hexdigest() != _hash(copy["plan_sha256"])):
            raise InputError("Final native scope/object/account/baseline/plan differ from plan.")
        before = _native_before(inputs[1], copy["subject"], platform, target, copy["checked_at"])
        fields = _native_fields(copy["fields"])
        if set(fields) != set(before["fields"]):
            raise InputError("Final fields must match the complete baseline field set.")
        topic = copy["native_topic"]
        if topic is not None:
            label(topic, "native topic")
            if platform != "threads":
                raise InputError("native_topic is reserved for Threads.")
        expected = [_tag(c["phrase"]) for c in plan.get("keywords", [])]
        actual = [_tag(text[start:end]) for text in fields.values() for _, start, end in hashtag_spans(text)]
        issues = self._copy_issues(meta, actual, expected, topic, copy["checked_at"], platform)
        if _age(copy["checked_at"], before["captured_at"]) > 7:
            issues.append("native_baseline_recheck_required")
        return PluginResult("hashtag-planner", "audit-native", notes=(
            "Checks supplied native metadata only; does not verify media bytes, ownership, native save or publication.",
            "Re-read the same account/object and baseline immediately before an authorised edit; independently audit its saved readback.",
        ), metadata={"schema": "keywordmoves-hashtag-native-audit/v1", "passed": not issues,
            "issues": issues, "actual_hashtags": actual, "target_kind": "existing_native_metadata",
            "target_subject": copy["subject"], "target_platform": platform, "native_target": target,
            "before_captured_at": stamp(before["captured_at"]), "before_fields_sha256": digest(before["fields"]),
            "fields_sha256": digest(fields), "field_sha256": {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in fields.items()},
            "final_copy_sha256": hashlib.sha256(copy_raw).hexdigest(), "checked_at": stamp(copy["checked_at"]),
            "reviewed_at": meta["reviewed_at"], "native_checked_at": stamp(meta["native_check"]["checked_at"]),
            "native_topic": topic, "plan_sha256": hashlib.sha256(raw).hexdigest(),
            "media_provenance_checked": False, "implementation_authorised": False, "network_requests": 0})

    @staticmethod
    def _copy_issues(meta, actual, expected, topic, checked_at, platform):
        issues = []
        if Counter(expected) != Counter(actual):
            issues.append("caption_tags_differ_from_plan")
        if len(actual) != len(set(actual)):
            issues.append("duplicate_caption_tags")
        if len(actual) > PROFILES[platform][0]:
            issues.append("editorial_ceiling_exceeded")
        if meta.get("state") == "needs_native_recheck" or _age(checked_at, meta["reviewed_at"]) > 14 or _age(checked_at, meta["native_check"]["checked_at"]) > 7:
            issues.append("native_recheck_required")
        if topic != meta.get("native_topic"):
            issues.append("native_topic_differs_from_plan")
        return issues

    def _audit(self, inputs):
        raw, plan = _object(inputs[0])
        copy_raw, copy = _object(inputs[1])
        _fields(copy, {"schema", "platform", "subject", "asset_sha256", "plan_sha256", "checked_at", "caption", "native_topic"})
        if copy["schema"] != "keywordmoves-hashtag-final-copy/v1":
            raise InputError("Expected keywordmoves-hashtag-final-copy/v1.")
        meta = plan.get("metadata")
        if not isinstance(meta, dict) or meta.get("schema") != "keywordmoves-hashtag-plan/v1" or plan.get("operation") != "plan" or plan.get("plugin") != "hashtag-planner":
            raise InputError("Expected a saved hashtag-planner plan.")
        platform = _platform(copy["platform"])
        if meta.get("target_subject") != copy["subject"] or meta.get("target_platform") != platform or meta.get("asset_sha256") != _hash(copy["asset_sha256"]) or hashlib.sha256(raw).hexdigest() != copy["plan_sha256"]:
            raise InputError("Final-copy subject/platform/asset/plan hash differ from plan.")
        if not isinstance(copy["caption"], str) or len(copy["caption"]) > 100000:
            raise InputError("Caption must be text of at most 100000 characters.")
        topic = copy["native_topic"]
        if topic is not None:
            label(topic, "native topic")
            if platform != "threads":
                raise InputError("native_topic is reserved for Threads.")
        expected = [_tag(c["phrase"]) for c in plan.get("keywords", [])]
        actual = [_tag(copy["caption"][start:end]) for _, start, end in hashtag_spans(copy["caption"])]
        issues = self._copy_issues(meta, actual, expected, topic, copy["checked_at"], platform)
        return PluginResult("hashtag-planner", "audit", notes=(
            "Checks supplied copy, not platform acceptance, topic persistence or public publication.",
        ), metadata={"passed": not issues, "issues": issues, "actual_hashtags": actual,
            "target_subject": copy["subject"], "target_platform": platform,
            "asset_sha256": copy["asset_sha256"],
            "caption_sha256": hashlib.sha256(copy["caption"].encode("utf-8")).hexdigest(),
            "final_copy_sha256": hashlib.sha256(copy_raw).hexdigest(),
            "checked_at": stamp(copy["checked_at"]), "reviewed_at": meta["reviewed_at"],
            "native_checked_at": stamp(meta["native_check"]["checked_at"]),
            "native_topic": topic, "plan_sha256": hashlib.sha256(raw).hexdigest(),
            "implementation_authorised": False, "network_requests": 0})
