"""Explainable local phrase filtering; never a cross-provider demand score."""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import replace

from ..errors import ConfigurationError, InputError
from ..models import KeywordCandidate, KeywordEvidence, PluginDescriptor, PluginResult
from ..monitoring.validation import canonical, finite, label, normal, read_json, reject_secrets

STOPWORDS = {"a", "an", "the", "about", "for", "of", "to", "in", "and", "with"}


def terms(value):
    parts, word = [], ""
    for char in normal(value):
        if unicodedata.category(char)[0] in "LNM" or char in "\u200c\u200d'’":
            word += char
        elif word:
            parts.append(word)
            word = ""
    if word:
        parts.append(word)
    return tuple(parts)


def contains(words, phrase):
    needle = terms(phrase)
    return bool(needle) and any(words[i:i + len(needle)] == needle for i in range(len(words)))


class CandidateFocusPlugin:
    descriptor = PluginDescriptor("candidate-focus", "Filter and order saved candidates by explicit phrase fit, preserving every source metric.",
                                  ("composition", "review", "matching-terms"), ("related", "matching-terms"))

    def run(self, request, context):
        del context
        if request.operation not in self.descriptor.operations or len(request.keywords) != 1 or not 1 <= len(request.inputs) <= 20:
            raise ConfigurationError("candidate-focus requires one seed and 1-20 saved KeywordMoves JSON results.")
        options = dict(request.options)
        if set(options) - {"include_any", "exclude_any", "min_overlap", "limit", "subject"}:
            raise ConfigurationError("Unknown candidate-focus option.")
        seed = label(request.keywords[0], "seed")
        seed_terms = set(terms(seed)) - STOPWORDS
        if not seed_terms:
            raise ConfigurationError("Seed needs at least one meaningful term.")
        minimum = options.get("min_overlap", 1)
        limit = options.get("limit", 20)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or not 0 <= minimum <= len(seed_terms):
            raise ConfigurationError("min_overlap must be an integer from 0 to the meaningful seed-term count.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ConfigurationError("limit must be an integer from 1 to 1000.")
        lists = {}
        for key in ("include_any", "exclude_any"):
            value = options.get(key, "")
            if not isinstance(value, str):
                raise ConfigurationError("include_any/exclude_any must be pipe-separated literal phrases.")
            lists[key] = [label(p, key) for p in value.split("|") if p.strip()]
            if len(lists[key]) > 50:
                raise ConfigurationError("At most 50 explicit phrases per filter.")
        kept, rejected, inputs = [], [], []
        row_count = 0
        for path in request.inputs:
            raw, data = read_json(path)
            canonical(data)
            reject_secrets(data)
            if not isinstance(data, dict) or not isinstance(data.get("keywords"), list) or not isinstance(data.get("plugin"), str):
                raise InputError("Expected a saved KeywordMoves PluginResult JSON.")
            inputs.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "plugin": data["plugin"]})
            for row in data["keywords"]:
                row_count += 1
                if row_count > 50000 or not isinstance(row, dict):
                    raise InputError("Expected at most 50000 candidate objects; no truncation.")
                phrase = label(row.get("phrase"), "phrase")
                metadata = row.get("metadata", {})
                if not isinstance(metadata, dict) or not isinstance(row.get("evidence", []), list):
                    raise InputError("Candidate metadata/evidence have invalid types.")
                words = terms(phrase)
                overlap = sorted(set(words) & seed_terms)
                reason = None
                if "subject" in options and metadata.get("subject") != options["subject"]:
                    reason = "different_subject"
                elif any(contains(words, p) for p in lists["exclude_any"]):
                    reason = "excluded_phrase"
                elif lists["include_any"] and not any(contains(words, p) for p in lists["include_any"]):
                    reason = "required_context_absent"
                elif request.operation == "matching-terms" and not seed_terms <= set(words):
                    reason = "missing_seed_terms"
                elif len(overlap) < minimum:
                    reason = "insufficient_seed_overlap"
                if reason:
                    rejected.append({"phrase": phrase, "input_index": len(inputs), "row": row_count, "reason": reason})
                    continue
                try:
                    evidence = tuple(KeywordEvidence(**e) for e in row.get("evidence", []))
                    score = row.get("score")
                    if score is not None:
                        finite(score, "score")
                    candidate = KeywordCandidate(phrase, row.get("relationship", "candidate"), score, evidence, metadata)
                except (TypeError, ValueError) as exc:
                    raise InputError("Invalid candidate/evidence fields.") from exc
                rank = (normal(phrase) != normal(seed), -len(overlap), row_count)
                kept.append((rank, replace(candidate, metadata={**metadata, "focus_review": {
                    "seed": seed, "matched_seed_terms": overlap, "source_plugin": data["plugin"],
                    "input_index": len(inputs), "original_row": row_count,
                    "requires_human_intent_review": True}})))
        kept.sort(key=lambda item: item[0])
        selected = kept[:limit]
        settings = {"operation": request.operation, "seed": seed, **options}
        return PluginResult(self.descriptor.name, request.operation, tuple(item[1] for item in selected),
            ("Literal term fit is an explainable queue order, not search demand or a guarantee of song relevance.",
             "Original scores, evidence, sources and dates are preserved unchanged; no averages or invented volumes.",
             "Unicode exact terms, no stemming/translation; exclusions require explicit caller choices.",),
            {"inputs": inputs, "input_rows": row_count, "matched_rows": len(kept), "returned_rows": len(selected),
             "limit_omitted_rows": len(kept) - len(selected), "rejected": rejected, "settings": settings,
             "settings_sha256": hashlib.sha256(canonical(settings).encode()).hexdigest(), "network_requests": 0})
