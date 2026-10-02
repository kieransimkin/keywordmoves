"""Local semantic keyphrases with KeyBERT, without KeyLLM or hosted inference."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..errors import ConfigurationError, InputError, KeywordMovesError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
# Pin the default model; custom model IDs do not inherit this revision.
DEFAULT_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
WORD_RE = re.compile(r"[^\W_]+(?:['’‐‑-][^\W_]+)*", re.UNICODE)
SUFFIXES = {".txt", ".md", ".lrc", ".csv"}


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def _integer(options: Mapping[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not re.fullmatch(r"[+]?\d+", str(value)):
        raise ConfigurationError(f"KeyBERT {key} must be an integer between {low} and {high}.")
    result = int(value)
    if not low <= result <= high:
        raise ConfigurationError(f"KeyBERT {key} must be between {low} and {high}.")
    return result


def _strings(value: Any, key: str) -> frozenset[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = value
    else:
        raise ConfigurationError(f"KeyBERT {key} must be a comma-separated string or sequence.")
    if any(not isinstance(x, str) for x in items):
        raise ConfigurationError(f"KeyBERT {key} must contain only strings.")
    return frozenset(_normalise(x) for x in items if x.strip())


@dataclass(frozen=True)
class _Settings:
    model: str
    revision: str | None
    cache_dir: str | None
    device: str
    local_files_only: bool
    method: str
    diversity: float
    stop_words: str
    stopwords: frozenset[str]
    limit: int
    min_words: int
    max_words: int
    min_length: int
    min_occurrences: int
    min_df: int
    max_chars: int
    max_candidates: int
    max_occurrences: int
    batch_size: int
    nr_candidates: int

    @classmethod
    def parse(cls, options: Mapping[str, Any]) -> _Settings:
        allowed = {
            "text", "keybert_model", "revision", "cache_dir", "device", "local_files_only",
            "method", "diversity", "stop_words", "stopwords", "limit", "min_words", "max_words",
            "min_length", "min_occurrences", "min_df", "max_chars", "max_candidates",
            "max_occurrences", "batch_size", "nr_candidates",
        }
        if set(options) - allowed:
            raise ConfigurationError(
                "Unsupported KeyBERT option(s): " + ", ".join(sorted(set(options) - allowed))
                + ". Use --option keybert_model=NAME, not the LLM-specific --model/--llm."
            )
        model = options.get("keybert_model", DEFAULT_MODEL)
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("KeyBERT keybert_model must name a model or local directory.")
        model = model.strip()
        revision = options.get("revision", DEFAULT_REVISION if model == DEFAULT_MODEL else None)
        cache_dir = options.get("cache_dir")
        for name, value in (("revision", revision), ("cache_dir", cache_dir)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ConfigurationError(f"KeyBERT {name} must be a non-empty string.")
        device = options.get("device", "cpu")
        if not isinstance(device, str) or not re.fullmatch(r"auto|cpu|mps|cuda(?::\d+)?", device):
            raise ConfigurationError("KeyBERT device must be cpu, auto, mps, cuda or cuda:N.")
        local = options.get("local_files_only", False)
        if isinstance(local, str) and local.casefold() in {"true", "false"}:
            local = local.casefold() == "true"
        if not isinstance(local, bool):
            raise ConfigurationError("KeyBERT local_files_only must be true or false.")
        method = options.get("method", "cosine")
        if not isinstance(method, str) or method not in {"cosine", "mmr", "maxsum"}:
            raise ConfigurationError("KeyBERT method must be cosine, mmr or maxsum.")
        try:
            diversity = float(options.get("diversity", 0.5))
        except (ValueError, TypeError):
            raise ConfigurationError("KeyBERT diversity must be between 0 and 1.") from None
        if isinstance(options.get("diversity"), bool) or not 0 <= diversity <= 1:
            raise ConfigurationError("KeyBERT diversity must be between 0 and 1.")
        if "diversity" in options and method != "mmr":
            raise ConfigurationError("KeyBERT diversity requires method=mmr.")
        if "nr_candidates" in options and method != "maxsum":
            raise ConfigurationError("KeyBERT nr_candidates requires method=maxsum.")
        stop_words = options.get("stop_words", "english")
        if stop_words is None:
            stop_words = "none"
        if not isinstance(stop_words, str) or stop_words not in {"english", "none"}:
            raise ConfigurationError("KeyBERT stop_words must be english or none.")
        cfg = cls(
            model=model, revision=revision, cache_dir=cache_dir, device=device,
            local_files_only=local, method=method, diversity=diversity,
            stop_words=stop_words, stopwords=_strings(options.get("stopwords", ""), "stopwords"),
            limit=_integer(options, "limit", 20, 1, 1000),
            min_words=_integer(options, "min_words", 1, 1, 10),
            max_words=_integer(options, "max_words", 3, 1, 10),
            min_length=_integer(options, "min_length", 2, 1, 200),
            min_occurrences=_integer(options, "min_occurrences", 1, 1, 1000000),
            min_df=_integer(options, "min_df", 1, 1, 100000),
            max_chars=_integer(options, "max_chars", 1000000, 1, 10000000),
            max_candidates=_integer(options, "max_candidates", 2000, 1, 5000),
            max_occurrences=_integer(options, "max_occurrences", 10, 0, 1000),
            batch_size=_integer(options, "batch_size", 32, 1, 256),
            nr_candidates=_integer(options, "nr_candidates", 20, 1, 1000),
        )
        if cfg.min_words > cfg.max_words:
            raise ConfigurationError("KeyBERT min_words must not exceed max_words.")
        if method == "maxsum" and cfg.nr_candidates < cfg.limit:
            raise ConfigurationError("KeyBERT nr_candidates must be at least limit for maxsum.")
        return cfg


def _documents(request: PluginRequest, cfg: _Settings) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    if "text" in request.options:
        text = request.options["text"]
        if not isinstance(text, str) or not text.strip():
            raise InputError("KeyBERT text must be a non-empty reference text string.")
        docs.append(("inline:text", text))
    total = sum(len(text) for _, text in docs)
    budget_error = "KeyBERT inputs exceed max_chars; split the input or increase that option."
    if total > cfg.max_chars:
        raise InputError(budget_error)
    paths: dict[Path, None] = {}
    try:
        for value in request.inputs:
            path = Path(value)
            if path.is_dir():
                found = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
                if not found:
                    raise InputError(f"No supported KeyBERT text files in directory: {path}.")
            elif path.is_file() and path.suffix.lower() in SUFFIXES:
                found = [path]
            else:
                raise InputError(f"KeyBERT input is missing or has an unsupported type: {path}.")
            paths.update((p.resolve(), None) for p in found)
        for path in paths:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                text = handle.read(cfg.max_chars - total + 1)
            total += len(text)
            if total > cfg.max_chars:
                raise InputError(budget_error)
            docs.append((str(path), text))
    except (OSError, UnicodeError) as exc:
        raise InputError("Cannot read KeyBERT input as UTF-8 text; check permissions and encoding.") from exc
    if not docs or not any(text.strip() for _, text in docs):
        raise InputError("KeyBERT needs reference text via --input or --option text=TEXT.")
    return docs


def _spans(text: str, cfg: _Settings, stops: frozenset[str]) -> Iterator[tuple[str, int, int]]:
    words = list(WORD_RE.finditer(text))
    for start, left in enumerate(words):
        if _normalise(left.group()) in stops or not any(c.isalpha() for c in left.group()):
            continue
        for end in range(start, min(start + cfg.max_words, len(words))):
            right = words[end]
            if end > start:
                gap = text[words[end - 1].end():right.start()]
                if not gap or not gap.isspace() or "\n" in gap or "\r" in gap:
                    break
            phrase = _normalise(text[left.start():right.end()])
            if (
                end - start + 1 >= cfg.min_words and len(phrase) >= cfg.min_length
                and _normalise(right.group()) not in stops
                and any(c.isalpha() for c in right.group())
            ):
                yield phrase, left.start(), right.end()


@dataclass
class _Candidate:
    count: int = 0
    documents: Counter[str] = field(default_factory=Counter)
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    surfaces: set[str] = field(default_factory=set)
    scores: list[dict[str, Any]] = field(default_factory=list)


def _inventory(
    docs: list[tuple[str, str]], cfg: _Settings, stops: frozenset[str], allowed: frozenset[str],
) -> dict[str, _Candidate]:
    candidates: dict[str, _Candidate] = {}
    for source, text in docs:
        for phrase, start, end in _spans(text, cfg, stops):
            if allowed and phrase not in allowed:
                continue
            entry = candidates.setdefault(phrase, _Candidate())
            if len(candidates) > cfg.max_candidates:
                raise InputError(
                    "KeyBERT candidate vocabulary exceeds max_candidates. Narrow max_words, "
                    "use --keyword to restrict candidates, or process fewer documents."
                )
            entry.count += 1
            entry.documents[source] += 1
            if len(entry.occurrences) < cfg.max_occurrences:
                entry.occurrences.append({
                    "source": source, "start_char": start, "end_char": end,
                    "text": text[start:end],
                })
            if len(entry.surfaces) < 20:
                entry.surfaces.add(text[start:end])
    return {p: v for p, v in candidates.items()
            if v.count >= cfg.min_occurrences and len(v.documents) >= cfg.min_df}


def _chunks(text: str, encoder: Any) -> list[tuple[str, int]]:
    """Fit complete whitespace-delimited units to the real tokenizer's token budget."""
    limit = encoder.max_seq_length
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 4 or limit > 1000000:
        raise ConfigurationError("KeyBERT needs an encoder with a finite max_seq_length.")
    words = list(re.finditer(r"\S+", text))
    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(words):
        low, high = start + 1, min(len(words), start + limit)
        best = start
        weight = 0
        while low <= high:
            end = (low + high) // 2
            segment = text[words[start].start():words[end - 1].end()]
            tokens = encoder.tokenizer.encode(segment, add_special_tokens=True, truncation=False)
            if len(tokens) <= limit:
                best, weight, low = end, len(tokens), end + 1
            else:
                high = end - 1
        if best == start:
            raise InputError(
                "A KeyBERT input token exceeds the embedding model's context window. "
                "Split the long token or use a larger-context model; no text was truncated."
            )
        segment = text[words[start].start():words[best - 1].end()]
        chunks.append((segment, max(1, weight - encoder.tokenizer.num_special_tokens_to_add())))
        start = best
    return chunks


def _embeddings(encoder: Any, texts: list[str], cfg: _Settings) -> Any:
    import numpy as np

    values = np.asarray(encoder.encode(
        texts, batch_size=cfg.batch_size, show_progress_bar=False,
        convert_to_numpy=True, normalize_embeddings=True, prompt="",
    ))
    if (
        values.ndim != 2 or values.shape[0] != len(texts) or values.shape[1] == 0
        or not np.isfinite(values).all() or (np.linalg.norm(values, axis=1) == 0).any()
    ):
        raise KeywordMovesError("KeyBERT embedding model returned invalid or zero embeddings.")
    return values


def _version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class KeyBERTKeywordPlugin:
    descriptor = PluginDescriptor(
        name="keybert",
        summary="Rank literal reference-text keyphrases using local KeyBERT embeddings.",
        capabilities=("generate", "extract", "semantic-ranking", "local-inference"),
        operations=("extract",),
    )

    def __init__(self) -> None:
        self._cache: tuple[tuple[Any, ...], Any, Any] | None = None

    def _load(self, cfg: _Settings) -> tuple[Any, Any]:
        key = (cfg.model, cfg.revision, cfg.cache_dir, cfg.device, cfg.local_files_only)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1:]
        try:
            from keybert import KeyBERT
            from keybert.backend import SentenceTransformerBackend
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ConfigurationError(
                "KeyBERT needs optional dependencies: pip install 'keywordmoves[keybert]'."
            ) from None
        try:
            encoder = SentenceTransformer(
                cfg.model, revision=cfg.revision, cache_folder=cfg.cache_dir,
                device=None if cfg.device == "auto" else cfg.device,
                local_files_only=cfg.local_files_only, trust_remote_code=False,
                model_kwargs={"use_safetensors": True},
            )
            # An explicit backend avoids KeyBERT's string-model fallback behaviour.
            runtime = KeyBERT(model=SentenceTransformerBackend(encoder), llm=None)
        except (OSError, ValueError, RuntimeError, TypeError):
            raise ConfigurationError(
                "Cannot load the KeyBERT embedding model. Check model/revision, device, "
                "safetensors availability and cache. Offline mode requires a complete local model."
            ) from None
        self._cache = key, encoder, runtime
        return encoder, runtime

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        if request.operation != "extract":
            raise ConfigurationError("KeyBERT operation must be extract.")
        cfg = _Settings.parse(request.options)
        if any(not isinstance(x, str) or not x.strip() for x in request.keywords):
            raise ConfigurationError("KeyBERT --keyword candidates must be non-empty strings.")
        allowed = frozenset(_normalise(x) for x in request.keywords)
        docs = _documents(request, cfg)
        try:
            from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer
        except ImportError:
            raise ConfigurationError("Install optional dependencies: pip install 'keywordmoves[keybert]'.") from None
        stops = cfg.stopwords | (ENGLISH_STOP_WORDS if cfg.stop_words == "english" else frozenset())
        inventory = _inventory(docs, cfg, stops, allowed)
        notes = [
            "KeyBERT scores are cosine similarities, not search demand or model confidence.",
            "Candidates are literal contiguous spans; no POS/entity classification or LLM is used.",
        ]
        metadata: dict[str, Any] = {
            "model": cfg.model, "revision": cfg.revision, "method": cfg.method,
            "device": cfg.device, "local_files_only": cfg.local_files_only,
            "ngram_range": [cfg.min_words, cfg.max_words], "candidate_count": len(inventory),
            "score_aggregation": "max_selected_document_cosine",
            "document_embedding": "token_weighted_mean_of_normalized_chunk_embeddings",
            "sources": [{"source": source, "characters": len(text),
                         "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
                        for source, text in docs],
            "candidate_filter": "explicit" if allowed else "extracted",
        }
        if not inventory:
            notes.append("No candidate phrases survived the input, frequency and stopword filters.")
            return PluginResult(plugin="keybert", operation="extract", notes=tuple(notes), metadata=metadata)
        # Bound both combination count and inner pair comparisons before loading a model.
        if cfg.method == "maxsum":
            for source, _ in docs:
                count = sum(source in item.documents for item in inventory.values())
                top_n, pool = min(cfg.limit, count), min(cfg.nr_candidates, count)
                combinations = math.comb(pool, top_n)
                if combinations > 100000 or combinations * top_n * top_n > 10000000:
                    raise ConfigurationError(
                        "KeyBERT maxsum combination budget exceeded; lower limit/nr_candidates "
                        "or use method=mmr."
                    )
        encoder, runtime = self._load(cfg)
        words = sorted(inventory)

        def analyzer(text: str) -> list[str]:
            return [p for p, _, _ in _spans(text, cfg, stops) if p in inventory]

        vectorizer = CountVectorizer(analyzer=analyzer, vocabulary={p: i for i, p in enumerate(words)})
        try:
            import numpy as np

            # Candidate embeddings must never silently truncate either.
            for phrase in words:
                if len(encoder.tokenizer.encode(phrase, add_special_tokens=True, truncation=False)) > encoder.max_seq_length:
                    raise InputError("A KeyBERT candidate exceeds the model's context window; reduce max_words.")
            word_embeddings = _embeddings(encoder, words, cfg)
            for index, (source, text) in enumerate(docs):
                count = sum(source in item.documents for item in inventory.values())
                if not count:
                    metadata["sources"][index]["embedding_chunks"] = 0
                    continue
                chunks = _chunks(text, encoder)
                values = _embeddings(encoder, [part for part, _ in chunks], cfg)
                if values.shape[1] != word_embeddings.shape[1]:
                    raise KeywordMovesError("KeyBERT embedding dimensions are inconsistent.")
                doc_embedding = np.average(values, axis=0, weights=[weight for _, weight in chunks])
                norm = np.linalg.norm(doc_embedding)
                if not np.isfinite(norm) or norm == 0:
                    raise KeywordMovesError("KeyBERT document embedding is invalid or zero.")
                doc_embedding = (doc_embedding / norm).reshape(1, -1)
                metadata["sources"][index]["embedding_chunks"] = len(chunks)
                results = runtime.extract_keywords(
                    docs=text, vectorizer=vectorizer, top_n=min(cfg.limit, count),
                    use_mmr=cfg.method == "mmr", use_maxsum=cfg.method == "maxsum",
                    diversity=cfg.diversity, nr_candidates=min(cfg.nr_candidates, count),
                    doc_embeddings=doc_embedding, word_embeddings=word_embeddings,
                )
                if not isinstance(results, list) or not results:
                    raise KeywordMovesError("KeyBERT returned no rankings despite a non-empty vocabulary.")
                seen: set[str] = set()
                for rank, item in enumerate(results, 1):
                    if not isinstance(item, (list, tuple)) or len(item) != 2:
                        raise KeywordMovesError("KeyBERT returned malformed ranking data.")
                    phrase, raw_score = item
                    if not isinstance(phrase, str) or phrase not in inventory or source not in inventory[phrase].documents:
                        raise KeywordMovesError("KeyBERT returned a phrase absent from its source text.")
                    if isinstance(raw_score, bool):
                        raise KeywordMovesError("KeyBERT returned an invalid similarity score.")
                    score = float(raw_score)
                    if not math.isfinite(score) or not -1.000001 <= score <= 1.000001:
                        raise KeywordMovesError("KeyBERT returned an invalid similarity score.")
                    if phrase in seen:
                        raise KeywordMovesError("KeyBERT returned duplicate ranking data.")
                    seen.add(phrase)
                    inventory[phrase].scores.append({"source": source, "score": score, "rank": rank})
        except KeywordMovesError:
            raise
        except (ValueError, TypeError, RuntimeError, OSError, AttributeError, OverflowError) as exc:
            raise KeywordMovesError(
                "KeyBERT extraction failed; check model compatibility and input limits."
            ) from exc
        selected = [(p, v) for p, v in inventory.items() if v.scores]
        selected.sort(key=lambda item: (-max(s["score"] for s in item[1].scores), -item[1].count, item[0]))
        keywords = []
        for phrase, entry in selected[:cfg.limit]:
            score = max(s["score"] for s in entry.scores)
            keywords.append(KeywordCandidate(
                phrase=phrase, relationship="keybert-extracted", score=score,
                evidence=(
                    KeywordEvidence(source="keybert", metric="embedding_cosine_similarity", value=score,
                                    unit="cosine_similarity", notes="Maximum selected-document score; not demand evidence."),
                    KeywordEvidence(source="reference-text", metric="occurrences", value=entry.count, unit="count"),
                ),
                metadata={
                    "model": cfg.model, "method": cfg.method,
                    "document_frequency": len(entry.documents), "surface_forms": sorted(entry.surfaces),
                    "occurrences": entry.occurrences, "occurrences_total": entry.count,
                    "occurrences_truncated": entry.count > len(entry.occurrences),
                    "document_scores": entry.scores,
                },
            ))
        metadata["versions"] = {name: _version(name) for name in ("keybert", "sentence-transformers", "scikit-learn")}
        if any(source.get("embedding_chunks", 0) > 1 for source in metadata["sources"]):
            notes.append("Long reference text was embedded in tokenizer-sized chunks, not truncated.")
        if len(docs) > 1:
            notes.append("Selection/diversity is per document; the final limit caps their merged ranking.")
        return PluginResult(plugin="keybert", operation="extract", keywords=tuple(keywords),
                            notes=tuple(notes), metadata=metadata)
