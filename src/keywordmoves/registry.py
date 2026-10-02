from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Callable, Generic, TypeVar

from .errors import PluginNotFoundError

T = TypeVar("T")


class _Registry(Generic[T]):
    def __init__(self, group: str, builtins: dict[str, Callable[[], T]] | None = None) -> None:
        self.group = group
        self._factories = dict(builtins or {})
        self._instances: dict[str, T] = {}
        self._loaded_entry_points = False

    def register(self, name: str, factory: Callable[[], T]) -> None:
        self._factories[name] = factory
        self._instances.pop(name, None)

    def _load_entry_points(self) -> None:
        if self._loaded_entry_points:
            return
        for item in entry_points(group=self.group):
            self._factories.setdefault(item.name, item.load())
        self._loaded_entry_points = True

    def names(self) -> tuple[str, ...]:
        self._load_entry_points()
        return tuple(sorted(self._factories))

    def get(self, name: str) -> T:
        self._load_entry_points()
        if name not in self._factories:
            available = ", ".join(self.names()) or "none"
            raise PluginNotFoundError(
                f"Plugin {name!r} was not found in {self.group}. Available: {available}."
            )
        if name not in self._instances:
            self._instances[name] = self._factories[name]()
        return self._instances[name]

    def describe(self) -> tuple[Any, ...]:
        return tuple(self.get(name).descriptor for name in self.names())


def _keyword_builtins() -> dict[str, Callable[[], Any]]:
    from .builtin.google_trends import GoogleTrendsPlugin
    from .builtin.nltk_keywords import NLTKKeywordPlugin
    from .builtin.observed_evidence import ObservedEvidencePlugin
    from .builtin.spacy_keywords import SpacyKeywordPlugin
    from .builtin.text_library import TextLibraryPlugin

    return {
        "google-trends": GoogleTrendsPlugin,
        "nltk": NLTKKeywordPlugin,
        "observed-evidence": ObservedEvidencePlugin,
        "spacy": SpacyKeywordPlugin,
        "text-library": TextLibraryPlugin,
    }


def _llm_builtins() -> dict[str, Callable[[], Any]]:
    from .builtin.huggingface_llm import HuggingFaceTransformersLLM
    from .builtin.openai_llm import OpenAILLM

    return {
        "huggingface-transformers": HuggingFaceTransformersLLM,
        "openai": OpenAILLM,
    }


class PluginRegistry(_Registry[Any]):
    def __init__(self) -> None:
        super().__init__("keywordmoves.plugins", _keyword_builtins())


class LLMRegistry(_Registry[Any]):
    def __init__(self) -> None:
        super().__init__("keywordmoves.llms", _llm_builtins())
