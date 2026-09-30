from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PluginDescriptor:
    name: str
    summary: str
    capabilities: tuple[str, ...]
    operations: tuple[str, ...]
    version: str = "1"


@dataclass(frozen=True)
class KeywordEvidence:
    source: str
    metric: str
    value: float | int | str | None = None
    unit: str | None = None
    observed_at: str | None = None
    geography: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class KeywordCandidate:
    phrase: str
    relationship: str = "candidate"
    score: float | None = None
    evidence: tuple[KeywordEvidence, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginRequest:
    operation: str
    keywords: tuple[str, ...] = ()
    inputs: tuple[Path, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginResult:
    plugin: str
    operation: str
    keywords: tuple[KeywordCandidate, ...] = ()
    notes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMRequest:
    task: str
    prompt: str
    max_new_tokens: int = 128
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    plugin: str
    model: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionContext:
    llms: Any
    cache_dir: Path | None = None

