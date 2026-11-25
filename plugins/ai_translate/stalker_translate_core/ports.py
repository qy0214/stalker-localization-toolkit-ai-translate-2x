# -*- coding: utf-8 -*-
"""核心边界：应用层依赖端口，不依赖具体 UI、MCP 或 Provider SDK。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .domain import TranslationRequest, TranslationResult, TokenUsage


@runtime_checkable
class ProviderPort(Protocol):
    name: str

    def translate(self, request: TranslationRequest) -> TranslationResult:
        ...


@runtime_checkable
class TextProviderPort(Protocol):
    def translate_text(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        glossary_hits=None,
        extra_sys: str | None = None,
        context: str | None = None,
        style: str = "dialogue",
        temperature: float | None = None,
    ) -> str:
        ...

    def translate_batch_text(
        self,
        texts: list[str],
        source_lang: str,
        target_lang: str,
        glossary_hits=None,
        temperature: float | None = None,
    ) -> str:
        ...


@runtime_checkable
class ParserPort(Protocol):
    def cut_text_with_separator(self, text: str) -> list[dict[str, object]]:
        ...


@runtime_checkable
class GlossaryPort(Protocol):
    def lookup(self, text: str) -> str | None:
        ...

    def hits(self, text: str, limit: int = 32) -> tuple[tuple[str, str], ...]:
        ...


@runtime_checkable
class TextRepositoryPort(Protocol):
    def read_text(self, path: str) -> str:
        ...

    def write_text_atomic(self, path: str, text: str) -> None:
        ...


@runtime_checkable
class ProjectRepositoryPort(Protocol):
    def scan(self, root: str, mode: str = "auto") -> tuple[str, ...]:
        ...

    def read(self, path: str) -> str:
        ...

    def write_atomic(self, path: str, text: str) -> None:
        ...


@runtime_checkable
class AuditSinkPort(Protocol):
    def record(self, event: str, payload: dict[str, object]) -> None:
        ...


@runtime_checkable
class CancellationTokenPort(Protocol):
    def is_cancelled(self) -> bool:
        ...


@runtime_checkable
class UsageSinkPort(Protocol):
    def add(self, usage: TokenUsage) -> None:
        ...
