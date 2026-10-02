"""Local English keywords with NLTK POS tags, grammar chunks and named entities.

The optional library and data are loaded on use, never downloaded implicitly.
Candidates refer to literal spans in reference text, not generated suggestions.
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

FEATURE_WEIGHTS = {
    "entities": 4.0, "proper-nouns": 4.0, "noun-chunks": 3.0,
    "keyphrases": 2.0, "nouns": 1.0, "adjectives": 0.5, "verbs": 0.5,
}
DEFAULT_FEATURES = frozenset(FEATURE_WEIGHTS) - {"adjectives", "verbs"}
SUFFIXES = {".txt", ".md", ".lrc", ".csv"}
NP_GRAMMAR = r"NP: {<DT|PRP\$>?<JJ.*>*<NN.*>+}"
RESOURCE_PATHS = {
    "punkt_tab": ("tokenizers/punkt_tab/english/",),
    "averaged_perceptron_tagger_eng": ("taggers/averaged_perceptron_tagger_eng/",),
    "stopwords": ("corpora/stopwords", "corpora/stopwords.zip/stopwords/"),
    "maxent_ne_chunker_tab": ("chunkers/maxent_ne_chunker_tab/english_ace_multiclass/",),
    "words": ("corpora/words", "corpora/words.zip/words/"),
    "wordnet": ("corpora/wordnet", "corpora/wordnet.zip/wordnet/"),
}
ADDRESS_RE = re.compile(r"(?:\b(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _integer(options: Mapping[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not re.fullmatch(r"[+]?\d+", str(value)):
        raise ConfigurationError(f"NLTK {key} must be an integer between {low} and {high}.")
    result = int(value)
    if not low <= result <= high:
        raise ConfigurationError(f"NLTK {key} must be between {low} and {high}.")
    return result


def _boolean(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    raise ConfigurationError(f"NLTK {key} must be true or false.")


def _words(value: Any, key: str) -> frozenset[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        raise ConfigurationError(f"NLTK {key} must be a comma-separated string or sequence.")
    if any(not isinstance(item, str) for item in values):
        raise ConfigurationError(f"NLTK {key} must contain only strings.")
    return frozenset(_normalise(item) for item in values if item.strip())


@dataclass(frozen=True)
class _Settings:
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
            "text", "language", "features", "entity_labels", "stopwords", "lemmatize",
            "limit", "min_occurrences", "min_length", "max_words", "max_chars",
            "max_occurrences", "batch_size",
        }
        if set(options) - allowed:
            raise ConfigurationError(
                "Unsupported NLTK option(s): " + ", ".join(sorted(set(options) - allowed))
                + ". This is an English NLTK extractor; --model/--llm are LLM options."
            )
        if options.get("language", "english") != "english":
            raise ConfigurationError("NLTK currently supports language=english only.")
        features = _words(options.get("features", DEFAULT_FEATURES), "features")
        if not features or features - FEATURE_WEIGHTS.keys():
            raise ConfigurationError("NLTK features must select from: " + ", ".join(FEATURE_WEIGHTS))
        labels = None
        if "entity_labels" in options:
            labels = _words(options["entity_labels"], "entity_labels")
            if not labels or ("*" in labels and labels != {"*"}):
                raise ConfigurationError("NLTK entity_labels must be labels or '*' alone.")
        return cls(
            features=features, entity_labels=labels,
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
            raise InputError("NLTK text must be a non-empty reference text string.")
        documents.append(("inline:text", text))
    paths: dict[Path, None] = {}
    try:
        for value in request.inputs:
            path = Path(value)
            if path.is_dir():
                found = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
                if not found:
                    raise InputError(f"No supported NLTK text files in directory: {path}.")
            elif path.is_file():
                if path.suffix.lower() not in SUFFIXES:
                    raise InputError(f"Unsupported NLTK input type: {path.suffix or '(none)'}.")
                found = [path]
            else:
                raise InputError(f"NLTK input does not exist: {path}.")
            for item in found:
                paths[item.resolve()] = None
        for path in paths:
            # Bound the read before parsing; do not truncate and return partial results.
            with path.open(encoding="utf-8-sig", newline="") as handle:
                documents.append((str(path), handle.read(cfg.max_chars + 1)))
    except (OSError, UnicodeError) as exc:
        raise InputError("Cannot read NLTK input as UTF-8 text; check encoding and permissions.") from exc
    if not documents or not any(text.strip() for _, text in documents):
        raise InputError("NLTK needs non-empty reference text via --input or --option text=TEXT.")
    for source, text in documents:
        if len(text) > cfg.max_chars:
            raise InputError(
                f"NLTK input {source!r} exceeds max_chars={cfg.max_chars}. "
                "Split the document or explicitly increase --option max_chars; no text was processed."
            )
    return documents


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
            relationship="nltk-extracted",
            score=round(item.raw_score / maximum, 6),
            evidence=(KeywordEvidence(
                source="nltk", metric="detected_occurrences", value=len(locations), unit="count",
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


def _resources(cfg: _Settings) -> tuple[str, ...]:
    names = ["punkt_tab", "averaged_perceptron_tagger_eng", "stopwords"]
    if "entities" in cfg.features:
        names.extend(("maxent_ne_chunker_tab", "words"))
    if cfg.lemmatize:
        names.append("wordnet")
    return tuple(names)


def _require_data(nltk: Any, resources: tuple[str, ...]) -> None:
    missing = []
    for name in resources:
        for path in RESOURCE_PATHS[name]:
            try:
                nltk.data.find(path)
                break
            except LookupError:
                pass
        else:
            missing.append(name)
    if missing:
        raise ConfigurationError(
            "Missing NLTK data. Install explicitly: python -m nltk.downloader "
            + " ".join(missing)
            + ". For a custom data directory, set NLTK_DATA before starting KeywordMoves. "
            "No resources were downloaded automatically."
        )


@dataclass
class _Runtime:
    version: str
    sentences: Any
    words: Any
    tagger: Any
    parser: Any
    ner: Any
    lemmatizer: Any
    stopwords: frozenset[str]


@dataclass(frozen=True)
class _Token:
    text: str
    tag: str
    start: int
    end: int
    blocked: bool = False

    @property
    def proper(self) -> bool:
        return self.tag in {"NNP", "NNPS"}


def _content(token: _Token, stops: frozenset[str]) -> bool:
    return (
        not token.blocked and token.tag != "CD"
        and any(c.isalpha() for c in token.text)
        and (token.proper or token.text.casefold() not in stops)
    )


def _tree_spans(tree: Any, tagged: list[tuple[str, str]]) -> Iterator[tuple[int, int, str]]:
    if tree.leaves() != tagged:
        raise InputError("NLTK chunk output changed token order; source offsets would be invalid.")
    offset = 0
    for child in tree:
        if isinstance(child, tuple):
            offset += 1
        else:
            size = len(child.leaves())
            if size:
                yield offset, offset + size, child.label()
            offset += size


def _spans(
    tokens: list[_Token], tagged: list[tuple[str, str]], entities: list[tuple[int, int, str]],
    runtime: _Runtime, cfg: _Settings,
) -> Iterator[tuple[int, int, str, str | None]]:
    if "entities" in cfg.features:
        for start, end, label in entities:
            if cfg.entity_labels is None or cfg.entity_labels == {"*"} or label in cfg.entity_labels:
                yield start, end, "entities", label
    if "proper-nouns" in cfg.features:
        entity_starts = {start for start, _, _ in entities}
        i = 0
        while i < len(tokens):
            if not tokens[i].proper:
                i += 1
                continue
            start = i
            yield i, i + 1, "proper-nouns", None
            i += 1
            while i < len(tokens):
                if tokens[i].proper and i not in entity_starts:
                    yield i, i + 1, "proper-nouns", None
                    i += 1
                elif (tokens[i].text in {"-", "‐", "‑"} and i + 1 < len(tokens)
                      and tokens[i + 1].proper and i + 1 not in entity_starts):
                    yield i + 1, i + 2, "proper-nouns", None
                    i += 2
                else:
                    break
            if i - start > 1:
                yield start, i, "proper-nouns", None
    if "noun-chunks" in cfg.features:
        for start, end, _ in _tree_spans(runtime.parser.parse(tagged), tagged):
            while start < end and tokens[start].tag in {"DT", "PRP$"}:
                start += 1
            if start < end:
                yield start, end, "noun-chunks", None
    stops = runtime.stopwords | cfg.stopwords
    for i, token in enumerate(tokens):
        feature = (
            "nouns" if token.tag in {"NN", "NNS"} else
            "adjectives" if token.tag.startswith("JJ") else
            "verbs" if token.tag.startswith("VB") else None
        )
        if feature in cfg.features and _content(token, stops):
            yield i, i + 1, feature, None
        if "keyphrases" not in cfg.features or not token.tag.startswith("NN"):
            continue
        start = i
        while start > 0 and i - start + 1 < cfg.max_words:
            previous = tokens[start - 1]
            if not previous.tag.startswith(("JJ", "NN")) or not _content(previous, stops):
                break
            start -= 1
            yield start, i + 1, "keyphrases", None


def _collect(
    text: str, source: str, tokens: list[_Token], tagged: list[tuple[str, str]],
    runtime: _Runtime, cfg: _Settings, candidates: dict[str, _Candidate],
) -> None:
    entities = list(_tree_spans(runtime.ner.parse(tagged), tagged)) if runtime.ner else []
    named = {i for start, end, _ in entities for i in range(start, end)}
    stops = runtime.stopwords | cfg.stopwords
    lemmas: dict[int, str] = {}
    for start, end, feature, label in _spans(tokens, tagged, entities, runtime, cfg):
        span = tokens[start:end]
        left, right = span[0].start, span[-1].end
        surface = _normalise(text[left:right])
        lexical = [t for t in span if any(c.isalnum() for c in t.text)]
        if (not lexical or len(lexical) > cfg.max_words or len(surface) < cfg.min_length
            or any(t.blocked for t in span) or not any(_content(t, stops) for t in lexical)):
            continue
        protected = feature == "entities" or any(
            tokens[i].proper or i in named for i in range(start, end)
        )
        if protected:
            phrase = surface
        else:
            parts = []
            for i in range(start, end):
                token = tokens[i]
                if i not in lemmas:
                    pos = {"N": "n", "J": "a", "V": "v"}.get(token.tag[:1])
                    lemmas[i] = (
                        runtime.lemmatizer.lemmatize(token.text.lower(), pos=pos)
                        if cfg.lemmatize and pos else token.text
                    )
                parts.append(lemmas[i])
                if i + 1 < end:
                    parts.append(text[token.end:tokens[i + 1].start])
            phrase = _normalise("".join(parts)).casefold()
        key = phrase.casefold()
        if key in cfg.stopwords or surface.casefold() in cfg.stopwords:
            continue
        if not protected and key in stops:
            continue
        item = candidates.setdefault(key, _Candidate(phrase=phrase, word_count=len(lexical)))
        if protected and feature in {"entities", "proper-nouns"}:
            item.phrase = surface
        item.features.add(feature)
        if label:
            item.entity_labels.add(label)
        item.surfaces.add(surface)
        item.locations.setdefault((source, left, right), set()).add(feature)


class NLTKKeywordPlugin:
    descriptor = PluginDescriptor(
        name="nltk",
        summary="Extract proper nouns, grammar-based noun chunks, entities and keyphrases with NLTK.",
        capabilities=("generate", "extract", "local-inference", "proper-nouns", "noun-chunks"),
        operations=("extract",),
    )

    def __init__(self) -> None:
        self._runtimes: dict[tuple[bool, bool], _Runtime] = {}

    def _load(self, cfg: _Settings) -> _Runtime:
        try:
            import nltk
            from nltk.chunk import RegexpParser
            from nltk.chunk.named_entity import Maxent_NE_Chunker
            from nltk.corpus import stopwords
            from nltk.stem import WordNetLemmatizer
            from nltk.tag import PerceptronTagger
            from nltk.tokenize import PunktTokenizer, TreebankWordTokenizer
        except ImportError as exc:
            raise ConfigurationError(
                "The NLTK plugin needs optional dependencies: pip install 'keywordmoves[nltk]'."
            ) from exc
        key = ("entities" in cfg.features, cfg.lemmatize)
        if key not in self._runtimes:
            _require_data(nltk, _resources(cfg))
            try:
                self._runtimes[key] = _Runtime(
                    version=nltk.__version__, sentences=PunktTokenizer("english"),
                    words=TreebankWordTokenizer(), tagger=PerceptronTagger(lang="eng"),
                    parser=RegexpParser(NP_GRAMMAR),
                    ner=Maxent_NE_Chunker(fmt="multiclass") if key[0] else None,
                    lemmatizer=WordNetLemmatizer() if cfg.lemmatize else None,
                    stopwords=frozenset(stopwords.words("english")),
                )
            except (LookupError, OSError, ValueError) as exc:
                raise ConfigurationError(
                    "Cannot load NLTK data. Reinstall the selected resources with: "
                    "python -m nltk.downloader " + " ".join(_resources(cfg))
                ) from exc
        return self._runtimes[key]

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        if request.operation != "extract":
            raise ConfigurationError("NLTK operation must be extract.")
        if request.keywords:
            raise ConfigurationError(
                "NLTK extracts from reference text; use --input or --option text, not --keyword."
            )
        cfg = _Settings.parse(request.options)
        documents = _documents(request, cfg)
        runtime = self._load(cfg)
        candidates: dict[str, _Candidate] = {}
        sources = []
        try:
            for source, text in documents:
                count = self._process(text, source, runtime, cfg, candidates)
                sources.append({
                    "source": source, "characters": len(text), "tokens": count,
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                })
        except LookupError as exc:
            raise ConfigurationError(
                "NLTK data is missing or incomplete. Install explicitly: python -m nltk.downloader "
                + " ".join(_resources(cfg))
            ) from exc
        except (OSError, ValueError) as exc:
            raise InputError("NLTK could not process the supplied reference text.") from exc
        keywords, eligible = _results(candidates, cfg)
        notes = [
            "NLTK extraction is local; no LLM, hosted API or automatic data download is used.",
            "Noun chunks use a Penn Treebank POS grammar, not dependency parsing.",
            "Scores are relative heuristic salience, not confidence, search volume or demand evidence.",
            "POS and entity annotations are predictions; review names, lyrics and specialist terms.",
        ]
        if not keywords:
            notes.append("No candidates matched the selected features and filters.")
        return PluginResult(
            plugin="nltk", operation="extract", keywords=keywords, notes=tuple(notes),
            metadata={
                "nltk_version": runtime.version, "language": "english",
                "resources": list(_resources(cfg)), "noun_chunk_grammar": NP_GRAMMAR,
                "tagger": "averaged_perceptron_tagger_eng", "sources": sources,
                "features": sorted(cfg.features), "lemmatize": cfg.lemmatize,
                "candidate_count": len(candidates), "eligible_count": eligible,
                "returned_count": len(keywords), "limit": cfg.limit,
                "score_method": "max_feature_weight * (1 + log2(detected_occurrences)) + 0.1 * min(words, 5); divided by maximum eligible score",
                "feature_weights": dict(FEATURE_WEIGHTS),
            },
        )

    @staticmethod
    def _process(
        text: str, source: str, runtime: _Runtime, cfg: _Settings,
        candidates: dict[str, _Candidate],
    ) -> int:
        addresses = [(m.start(), m.end()) for m in ADDRESS_RE.finditer(text)]
        total = 0
        batch: list[tuple[list[str], list[tuple[int, int]]]] = []

        def flush() -> None:
            nonlocal total
            tagged_sentences = runtime.tagger.tag_sents(words for words, _ in batch)
            for (words, offsets), tagged in zip(batch, tagged_sentences, strict=True):
                if [word for word, _ in tagged] != words:
                    raise InputError("NLTK tagger changed tokens; source offsets would be invalid.")
                tokens = [
                    _Token(text[start:end], tag, start, end,
                           any(start < b and end > a for a, b in addresses))
                    for (start, end), (_, tag) in zip(offsets, tagged, strict=True)
                ]
                _collect(text, source, tokens, tagged, runtime, cfg, candidates)
                total += len(tokens)
            batch.clear()

        # Treat lyric line breaks as hard boundaries. Keep original character offsets,
        # including CRLF, instead of joining lines or removing stopwords before chunking.
        for line in re.finditer(r"[^\r\n]+", text):
            for sent_start, sent_end in runtime.sentences.span_tokenize(line.group()):
                fragment = line.group()[sent_start:sent_end]
                base = line.start() + sent_start
                words = runtime.words.tokenize(fragment)
                offsets = [(base + a, base + b) for a, b in runtime.words.span_tokenize(fragment)]
                if len(words) != len(offsets):
                    raise InputError("NLTK tokenizer returned inconsistent source offsets.")
                if not words:
                    continue
                batch.append((words, offsets))
                if len(batch) >= cfg.batch_size:
                    flush()
        if batch:
            flush()
        return total
