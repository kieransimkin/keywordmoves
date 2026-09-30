from __future__ import annotations

from typing import Protocol

from .models import (
    ExecutionContext,
    LLMRequest,
    LLMResult,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)


class KeywordPlugin(Protocol):
    descriptor: PluginDescriptor

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult: ...


class LLMPlugin(Protocol):
    descriptor: PluginDescriptor

    def generate(self, request: LLMRequest) -> LLMResult: ...
