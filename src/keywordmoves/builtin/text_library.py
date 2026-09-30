from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    KeywordEvidence,
    LLMRequest,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")
SUPPORTED_SUFFIXES = {".txt", ".md", ".lrc", ".csv"}
STOPWORDS = {
    "about", "after", "again", "against", "also", "and", "are", "because", "been",
    "before", "being", "between", "but", "can", "could", "did", "does", "doing", "for",
    "from", "had", "has", "have", "here", "hers", "him", "his", "how", "into", "its",
    "just", "like", "more", "most", "not", "now", "only", "our", "out", "over", "she",
    "should", "some", "such", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "through", "too", "under", "very", "was", "were",
    "what", "when", "where", "which", "while", "who", "will", "with", "would", "you",
    "your", "yeah", "oh", "ooh", "la",
}


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n,;:.!-–—\"'" )).casefold()


def _paths(inputs: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            found.extend(
                candidate
                for candidate in sorted(path.rglob("*"))
                if candidate.is_file() and candidate.suffix.casefold() in SUPPORTED_SUFFIXES
            )
        elif path.is_file():
            if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
                raise InputError(f"Unsupported text-library file type: {path.suffix or '(none)'}." )
            found.append(path)
        else:
            raise InputError(f"Text-library input does not exist: {path}.")
    if not found:
        raise InputError("No supported text files were found.")
    return list(dict.fromkeys(path.resolve() for path in found))


def _read(paths: Iterable[Path]) -> tuple[str, tuple[str, ...]]:
    documents: list[str] = []
    sources: list[str] = []
    for path in paths:
        try:
            documents.append(path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise InputError(f"Text input is not UTF-8: {path}.") from exc
        sources.append(str(path))
    return "\n\n".join(documents), tuple(sources)


def _local_candidates(text: str, limit: int) -> list[KeywordCandidate]:
    words = [_normalise(match.group(0)) for match in WORD_RE.finditer(text)]
    words = [word for word in words if word and word not in STOPWORDS]
    counts: Counter[str] = Counter(words)
    for left, right in zip(words, words[1:]):
        if left != right:
            counts[f"{left} {right}"] += 1
    if not counts:
        return []
    top_count = max(counts.values())
    ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [
        KeywordCandidate(
            phrase=phrase,
            relationship="text-extracted",
            score=round(count / top_count, 4),
            evidence=(
                KeywordEvidence(
                    source="text-library",
                    metric="occurrences",
                    value=count,
                    unit="count",
                ),
            ),
        )
        for phrase, count in ranked[:limit]
    ]


def _parse_llm_list(text: str) -> list[str]:
    phrases: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(keywords?|related(?: concepts?| words?)?)\s*:\s*", "", line, flags=re.I)
        for item in re.split(r"\s*[;,|]\s*|\s+\d+[.)]\s+", line):
            phrase = _normalise(re.sub(r"^[*\-•\d.)\s]+", "", item))
            if phrase and 2 <= len(phrase) <= 80:
                phrases.append(phrase)
    return list(dict.fromkeys(phrases))


class TextLibraryPlugin:
    descriptor = PluginDescriptor(
        name="text-library",
        summary="Extract keywords from local text and expand concepts through a selected LLM plugin.",
        capabilities=("generate", "discover", "analyse"),
        operations=("extract", "extract-local"),
    )

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        if request.operation not in self.descriptor.operations:
            raise ConfigurationError(
                f"text-library operation must be one of: {', '.join(self.descriptor.operations)}."
            )
        paths = _paths(request.inputs)
        text, sources = _read(paths)
        limit = int(request.options.get("limit", 20))
        if limit < 1 or limit > 200:
            raise ConfigurationError("Text-library limit must be between 1 and 200.")
        local_limit = limit if request.operation == "extract-local" else max(1, limit // 3)
        candidates = _local_candidates(text, local_limit)
        notes = [f"Read {len(sources)} local text file(s)."]

        if request.operation == "extract-local":
            notes.append("No LLM was used; results are frequency-ranked local candidates.")
            return PluginResult(
                plugin=self.descriptor.name,
                operation=request.operation,
                keywords=tuple(candidates),
                notes=tuple(notes),
                metadata={"sources": sources},
            )

        llm_name = request.options.get("llm")
        if not llm_name:
            raise ConfigurationError(
                "The 'extract' operation requires an explicit LLM plugin selection, for example "
                "--llm huggingface-transformers. Use 'extract-local' for no LLM."
            )
        llm = context.llms.get(str(llm_name))
        shortlist = ", ".join(item.phrase for item in candidates[:12]) or "none"
        max_chars = int(request.options.get("max_text_chars", 2400))
        llm_options = {
            key.removeprefix("llm_"): value
            for key, value in request.options.items()
            if key.startswith("llm_")
        }
        extracted_result = llm.generate(
            LLMRequest(
                task="keyword-extraction",
                prompt=(
                    "Extract up to eight useful search keyword phrases from the text below. Keep "
                    "them truthful to the text. Return only a comma-separated list, with no "
                    "explanation.\n\n"
                    f"Local candidates: {shortlist}\n\nText:\n{text[:max_chars]}"
                ),
                max_new_tokens=int(request.options.get("llm_max_new_tokens", 128)),
                options=llm_options,
            )
        )
        extracted = _parse_llm_list(extracted_result.text)
        concept_seeds = ", ".join(dict.fromkeys([*extracted, *[item.phrase for item in candidates]]))
        related_result = llm.generate(
            LLMRequest(
                task="related-concept-expansion",
                prompt=(
                    "Suggest up to eight closely related search phrases or concepts for the seed "
                    "phrases below. Keep them relevant; do not invent popularity or demand. Return "
                    "only a comma-separated list, with no explanation.\n\n"
                    f"Seed phrases: {concept_seeds[:max_chars]}"
                ),
                max_new_tokens=int(request.options.get("llm_max_new_tokens", 128)),
                options=llm_options,
            )
        )
        related = _parse_llm_list(related_result.text)
        known = {item.phrase for item in candidates}
        for phrase, relationship in [
            *((item, "llm-extracted") for item in extracted),
            *((item, "llm-related") for item in related),
        ]:
            if phrase not in known:
                candidates.append(
                    KeywordCandidate(
                        phrase=phrase,
                        relationship=relationship,
                        evidence=(
                            KeywordEvidence(
                                source=f"llm:{extracted_result.plugin}",
                                metric="semantic_suggestion",
                                value=extracted_result.model,
                                notes="Model output is a proposal, not demand evidence.",
                            ),
                        ),
                    )
                )
                known.add(phrase)
        notes.append(
            f"LLM expansion used {extracted_result.plugin} with model {extracted_result.model}."
        )
        return PluginResult(
            plugin=self.descriptor.name,
            operation=request.operation,
            keywords=tuple(candidates[:limit]),
            notes=tuple(notes),
            metadata={
                "sources": sources,
                "llm_plugin": extracted_result.plugin,
                "llm_model": extracted_result.model,
                "llm_metadata": dict(extracted_result.metadata),
            },
        )
