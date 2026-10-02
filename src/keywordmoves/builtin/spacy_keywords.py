"""Local, traceable keyword candidates from spaCy linguistic annotations.

spaCy and its pipeline are deliberately loaded only when this plugin runs.
No generation, external evidence lookup, or automatic model download occurs.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)

DEFAULT_MODEL = "en_core_web_sm"
FEATURE_WEIGHTS = {
    "entities": 4.0,
    "proper-nouns": 4.0,
    "noun-chunks": 3.0,
    "keyphrases": 2.0,
    "nouns": 1.0,
    "adjectives": 0.5,
    "verbs": 0.5,
}
DEFAULT_FEATURES = frozenset(FEATURE_WEIGHTS) - {"adjectives", "verbs"}
NUMERIC_ENTITIES = frozenset({"DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL"})
SUFFIXES = {".txt", ".md", ".lrc", ".csv"}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _integer(options: Mapping[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not re.fullmatch(r"[+]?\d+", str(value)):
        raise ConfigurationError(f"spaCy {key} must be an integer between {low} and {high}.")
    result = int(value)
    if not low <= result <= high:
        raise ConfigurationError(f"spaCy {key} must be between {low} and {high}.")
    return result


def _boolean(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    raise ConfigurationError(f"spaCy {key} must be true or false.")


def _words(value: Any, key: str) -> frozenset[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        raise ConfigurationError(f"spaCy {key} must be a comma-separated string or sequence.")
    if any(not isinstance(item, str) for item in values):
        raise ConfigurationError(f"spaCy {key} must contain only strings.")
    return frozenset(_normalise(item) for item in values if item.strip())


@dataclass(frozen=True)
class _Settings:
    model: str
    features: frozenset[str]
    entity_labels: frozenset[str] | None
    stopwords: frozenset[str]
    lemmatize: bool
    limit: int
    min_occurrences: int
    min_length: int
    max_words: int
    max_chars: int
    max_occurrences: int
    batch_size: int

    @classmethod
    def parse(cls, options: Mapping[str, Any]) -> _Settings:
        allowed = {
            "text", "spacy_model", "features", "entity_labels", "stopwords", "lemmatize",
            "limit", "min_occurrences", "min_length", "max_words", "max_chars",
            "max_occurrences", "batch_size",
        }
        if set(options) - allowed:
            raise ConfigurationError(
                "Unsupported spaCy option(s): " + ", ".join(sorted(set(options) - allowed))
                + ". Use --option spacy_model=NAME for its pipeline; --model/--llm are LLM options."
            )
        model = options.get("spacy_model", DEFAULT_MODEL)
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("spaCy spacy_model must name an installed pipeline or local path.")
        features = _words(options.get("features", DEFAULT_FEATURES), "features")
        if not features or features - FEATURE_WEIGHTS.keys():
            raise ConfigurationError("spaCy features must select from: " + ", ".join(FEATURE_WEIGHTS))
        labels = None
        if "entity_labels" in options:
            labels = _words(options["entity_labels"], "entity_labels")
            if not labels or ("*" in labels and labels != {"*"}):
                raise ConfigurationError("spaCy entity_labels must be labels or '*' alone.")
        return cls(
            model=model.strip(), features=features, entity_labels=labels,
            stopwords=frozenset(x.casefold() for x in _words(options.get("stopwords", ""), "stopwords")),
            lemmatize=_boolean(options.get("lemmatize", True), "lemmatize"),
            limit=_integer(options, "limit", 50, 1, 10000),
            min_occurrences=_integer(options, "min_occurrences", 1, 1, 1000000),
            min_length=_integer(options, "min_length", 2, 1, 200),
            max_words=_integer(options, "max_words", 8, 1, 50),
            max_chars=_integer(options, "max_chars", 1000000, 1, 10000000),
            max_occurrences=_integer(options, "max_occurrences", 10, 0, 1000),
            batch_size=_integer(options, "batch_size", 16, 1, 256),
        )


def _documents(request: PluginRequest, cfg: _Settings) -> list[tuple[str, str]]:
    documents: list[tuple[str, str]] = []
    if "text" in request.options:
        text = request.options["text"]
        if not isinstance(text, str) or not text.strip():
            raise InputError("spaCy text must be a non-empty reference text string.")
        documents.append(("inline:text", text))
    paths: dict[Path, None] = {}
    try:
        for value in request.inputs:
            path = Path(value)
            if path.is_dir():
                found = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
                if not found:
                    raise InputError(f"No supported spaCy text files in directory: {path}.")
            elif path.is_file():
                if path.suffix.lower() not in SUFFIXES:
                    raise InputError(f"Unsupported spaCy input type: {path.suffix or '(none)'}.")
                found = [path]
            else:
                raise InputError(f"spaCy input does not exist: {path}.")
            for item in found:
                paths[item.resolve()] = None
        for path in paths:
            # Bound the read before parsing; do not truncate and return partial results.
            with path.open(encoding="utf-8-sig") as handle:
                documents.append((str(path), handle.read(cfg.max_chars + 1)))
    except (OSError, UnicodeError) as exc:
        raise InputError("Cannot read spaCy input as UTF-8 text; check encoding and permissions.") from exc
    if not documents or not any(text.strip() for _, text in documents):
        raise InputError("spaCy needs non-empty reference text via --input or --option text=TEXT.")
    for source, text in documents:
        if len(text) > cfg.max_chars:
            raise InputError(
                f"spaCy input {source!r} exceeds max_chars={cfg.max_chars}. "
                "Split the document or explicitly increase --option max_chars; no text was processed."
            )
    return documents


def _content(token: Any, stopwords: frozenset[str]) -> bool:
    return (
        not token.is_space and not token.is_punct and not token.like_num
        and not token.like_url and not token.like_email
        and any(char.isalpha() for char in token.text)
        and (not token.is_stop or token.pos_ == "PROPN")
        and _normalise(token.text).casefold() not in stopwords
        and _normalise(token.lemma_).casefold() not in stopwords
    )


def _adjacent(doc: Any, left: int, right: int) -> bool:
    gap = doc.text[doc[left].idx + len(doc[left]):doc[right].idx]
    return doc[right].is_sent_start is not True and "\n" not in gap and "\r" not in gap


def _spans(doc: Any, cfg: _Settings) -> Iterator[tuple[Any, str, str | None]]:
    features = cfg.features
    if features - {"entities"} and not doc.has_annotation("POS", require_complete=True):
        raise ConfigurationError("Selected spaCy features require complete POS annotations.")
    if "entities" in features:
        if not doc.has_annotation("ENT_IOB", require_complete=True):
            raise ConfigurationError("spaCy entities require an entity recognizer/ruler with ENT_IOB annotations.")
        for entity in doc.ents:
            if cfg.entity_labels == {"*"} or (
                entity.label_ in cfg.entity_labels if cfg.entity_labels is not None
                else entity.label_ not in NUMERIC_ENTITIES
            ):
                yield entity, "entities", entity.label_
    if "proper-nouns" in features:
        i = 0
        while i < len(doc):
            if doc[i].pos_ != "PROPN":
                i += 1
                continue
            start = i
            yield doc[i:i + 1], "proper-nouns", None
            i += 1
            while i < len(doc):
                if (doc[i].pos_ == "PROPN" and doc[i].ent_iob_ != "B"
                    and _adjacent(doc, i - 1, i)):
                    yield doc[i:i + 1], "proper-nouns", None
                    i += 1
                elif (
                    doc[i].text in {"-", "‐", "‑"} and i + 1 < len(doc)
                    and doc[i + 1].pos_ == "PROPN"
                    and _adjacent(doc, i - 1, i) and _adjacent(doc, i, i + 1)
                ):
                    yield doc[i + 1:i + 2], "proper-nouns", None
                    i += 2
                else:
                    break
            if i - start > 1:
                yield doc[start:i], "proper-nouns", None
    if "noun-chunks" in features:
        if not doc.has_annotation("DEP", require_complete=True):
            raise ConfigurationError("spaCy noun-chunks require a dependency parser with DEP annotations.")
        try:
            for chunk in doc.noun_chunks:
                if chunk.root.pos_ not in {"NOUN", "PROPN"}:
                    continue
                start, end = chunk.start, chunk.end
                while start < end and (
                    doc[start].pos_ in {"DET", "PRON"} or doc[start].is_space or doc[start].is_punct
                ):
                    start += 1
                while start < end and (doc[end - 1].is_space or doc[end - 1].is_punct):
                    end -= 1
                if start < end:
                    yield doc[start:end], "noun-chunks", None
        except NotImplementedError as exc:
            raise ConfigurationError(
                "This spaCy language has no noun-chunk iterator. Select another pipeline "
                "or explicitly omit noun-chunks from --option features."
            ) from exc
    token_features = {"NOUN": "nouns", "ADJ": "adjectives", "VERB": "verbs"}
    for token in doc:
        feature = token_features.get(token.pos_)
        if feature in features and _content(token, cfg.stopwords):
            yield doc[token.i:token.i + 1], feature, None
        if "keyphrases" not in features or token.pos_ not in {"NOUN", "PROPN"}:
            continue
        # Contiguous adjective/noun compounds; no stopword removal that invents adjacency.
        start = token.i
        while start > 0 and token.i - start + 1 < cfg.max_words:
            previous = doc[start - 1]
            if (
                previous.pos_ not in {"ADJ", "NOUN", "PROPN"}
                or not _content(previous, cfg.stopwords)
                or not _adjacent(doc, start - 1, start)
            ):
                break
            start -= 1
            yield doc[start:token.i + 1], "keyphrases", None


@dataclass
class _Candidate:
    phrase: str
    word_count: int
    features: set[str] = field(default_factory=set)
    entity_labels: set[str] = field(default_factory=set)
    surfaces: set[str] = field(default_factory=set)
    locations: dict[tuple[str, int, int], set[str]] = field(default_factory=dict)

    @property
    def raw_score(self) -> float:
        return max(FEATURE_WEIGHTS[x] for x in self.features) * (
            1 + math.log2(len(self.locations))
        ) + 0.1 * min(self.word_count, 5)


def _collect(doc: Any, source: str, cfg: _Settings, candidates: dict[str, _Candidate]) -> None:
    # Do not reintroduce dates/numbers via the POS or noun-chunk extraction paths.
    blocked = {
        token.i for ent in doc.ents
        if ent.label_ in NUMERIC_ENTITIES
        and cfg.entity_labels != {"*"}
        and (cfg.entity_labels is None or ent.label_ not in cfg.entity_labels)
        for token in ent
    }
    for span, feature, label in _spans(doc, cfg):
        surface = _normalise(span.text)
        tokens = [token for token in span if not token.is_space and not token.is_punct]
        if (
            not tokens or "\n" in span.text or "\r" in span.text
            or len(tokens) > cfg.max_words or len(surface) < cfg.min_length
            or any(token.i in blocked for token in tokens)
            or any(token.like_url or token.like_email for token in tokens)
        ):
            continue
        protected = feature == "entities" or any(
            token.pos_ == "PROPN" or token.ent_iob_ in {"B", "I"} for token in tokens
        )
        if not (feature == "entities" and any(c.isalpha() for c in surface)) and not any(
            _content(token, cfg.stopwords) for token in tokens
        ):
            continue
        # Preserve complete names and original punctuation. Lemmatise common phrases only.
        phrase = surface if protected else _normalise("".join(
            (token.lemma_ if cfg.lemmatize and token.lemma_ and token.pos_ in {"NOUN", "ADJ", "VERB"}
             else token.text) + token.whitespace_
            for token in span
        )).casefold()
        key = phrase.casefold()
        if key in cfg.stopwords or surface.casefold() in cfg.stopwords:
            continue
        item = candidates.setdefault(key, _Candidate(phrase=phrase, word_count=len(tokens)))
        if protected and feature in {"entities", "proper-nouns"}:
            item.phrase = surface
        item.features.add(feature)
        if label:
            item.entity_labels.add(label)
        item.surfaces.add(surface)
        item.locations.setdefault((source, span.start_char, span.end_char), set()).add(feature)


def _results(candidates: dict[str, _Candidate], cfg: _Settings) -> tuple[tuple[KeywordCandidate, ...], int]:
    eligible = [item for item in candidates.values() if len(item.locations) >= cfg.min_occurrences]
    eligible.sort(key=lambda item: (-item.raw_score, -item.word_count, item.phrase.casefold()))
    maximum = eligible[0].raw_score if eligible else 1.0
    results = []
    for item in eligible[:cfg.limit]:
        locations = sorted(item.locations.items())
        occurrences = [
            {"source": source, "start_char": start, "end_char": end, "features": sorted(features)}
            for (source, start, end), features in locations[:cfg.max_occurrences]
        ]
        results.append(KeywordCandidate(
            phrase=item.phrase,
            relationship="spacy-extracted",
            score=round(item.raw_score / maximum, 6),
            evidence=(KeywordEvidence(
                source="spacy", metric="detected_occurrences", value=len(locations), unit="count",
                notes="Distinct detected spans in supplied text, not search-volume or demand evidence.",
            ),),
            metadata={
                "features": sorted(item.features), "entity_labels": sorted(item.entity_labels),
                "surface_forms": sorted(item.surfaces), "word_count": item.word_count,
                "document_frequency": len({loc[0] for loc in item.locations}),
                "raw_salience": round(item.raw_score, 6), "occurrences": occurrences,
                "occurrences_omitted": max(0, len(locations) - cfg.max_occurrences),
            },
        ))
    return tuple(results), len(eligible)


class SpacyKeywordPlugin:
    descriptor = PluginDescriptor(
        name="spacy",
        summary="Extract proper nouns, noun chunks, entities and keyphrases locally with spaCy.",
        capabilities=("generate", "extract", "local-inference", "proper-nouns", "noun-chunks"),
        operations=("extract",),
    )

    def __init__(self) -> None:
        self._pipelines: dict[str, Any] = {}

    def _load(self, model: str) -> tuple[Any, str]:
        try:
            import spacy
        except ImportError as exc:
            raise ConfigurationError(
                "The spaCy plugin needs optional dependencies: pip install 'keywordmoves[spacy]'."
            ) from exc
        if model not in self._pipelines:
            try:
                self._pipelines[model] = spacy.load(model)
            except (OSError, ValueError, ImportError) as exc:
                raise ConfigurationError(
                    f"Cannot load spaCy pipeline {model!r}. Install a compatible pipeline explicitly "
                    f"(default: python -m spacy download {DEFAULT_MODEL}) or set "
                    "--option spacy_model to a trusted installed pipeline/local path. "
                    "No pipeline was downloaded automatically."
                ) from exc
        return self._pipelines[model], spacy.__version__

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context  # No LLM/provider selection, even when a registry is available.
        if request.operation != "extract":
            raise ConfigurationError("spaCy operation must be extract.")
        if request.keywords:
            raise ConfigurationError("spaCy extracts from reference text; use --input or --option text, not --keyword.")
        cfg = _Settings.parse(request.options)
        documents = _documents(request, cfg)
        nlp, version = self._load(cfg.model)
        nlp.max_length = cfg.max_chars
        candidates: dict[str, _Candidate] = {}
        sources = []
        notes = [
            "spaCy extraction uses no LLM plugin or hosted API client in KeywordMoves.",
            "Scores are relative heuristic salience, not confidence, search volume or demand evidence.",
            "Linguistic annotations are model predictions; review names, lyrics and domain-specific terms.",
        ]
        try:
            for (source, text), doc in zip(documents, nlp.pipe(
                (text for _, text in documents), batch_size=cfg.batch_size,
            ), strict=True):
                if doc.text != text:
                    raise InputError("spaCy pipeline changed the reference text; source offsets would be invalid.")
                sources.append({
                    "source": source, "characters": len(text), "tokens": len(doc),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                })
                if not text.strip():
                    notes.append(f"Skipped empty reference text: {source}.")
                    continue
                if cfg.lemmatize and any(
                    token.pos_ in {"NOUN", "ADJ", "VERB"} and not token.lemma_ for token in doc
                ):
                    note = "Some lemmas were unavailable; their original token forms were retained."
                    if note not in notes:
                        notes.append(note)
                _collect(doc, source, cfg, candidates)
        except (OSError, ValueError) as exc:
            raise InputError("spaCy could not process the reference text with the selected pipeline.") from exc
        keywords, eligible = _results(candidates, cfg)
        if not keywords:
            notes.append("No candidates matched the selected features and filters.")
        return PluginResult(
            plugin=self.descriptor.name, operation=request.operation, keywords=keywords,
            notes=tuple(notes), metadata={
                "spacy_model": cfg.model, "spacy_version": version,
                "model_version": nlp.meta.get("version"), "language": nlp.lang,
                "pipeline": list(nlp.pipe_names), "sources": sources,
                "features": sorted(cfg.features), "lemmatize": cfg.lemmatize,
                "candidate_count": len(candidates), "eligible_count": eligible,
                "returned_count": len(keywords), "limit": cfg.limit,
                "score_method": "max_feature_weight * (1 + log2(detected_occurrences)) + 0.1 * min(words, 5); divided by maximum eligible score",
                "feature_weights": dict(FEATURE_WEIGHTS),
            },
        )
