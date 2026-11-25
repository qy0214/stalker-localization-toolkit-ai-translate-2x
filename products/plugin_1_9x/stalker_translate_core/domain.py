# -*- coding: utf-8 -*-
"""共享翻译领域的不可变值对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str = ""
    severity: Severity = "error"
    source_id: str = ""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_lang: str = "auto"
    target_lang: str = "chs"
    context: str = ""
    style: str = ""
    glossary_hits: tuple[tuple[str, str], ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TranslationResult:
    text: str
    source_text: str = ""
    provider: str = ""
    model: str = ""
    request_id: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    issues: tuple[QualityIssue, ...] = ()
    retained_source: bool = False
    cache_hit: bool = False


@dataclass(frozen=True)
class Budget:
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_requests: int | None = None
    max_cost: float | None = None
    deadline_sec: float | None = None


@dataclass(frozen=True)
class JobSpec:
    input_dir: str
    output_dir: str
    mode: str = "auto"
    source_lang: str = "auto"
    target_lang: str = "chs"
    provider_profile: str = ""
    model: str = ""
    concurrency: int = 1
    resume: bool = True
    budget: Budget = field(default_factory=Budget)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    state: JobState
    progress: int = 0
    total: int = 0
    message: str = ""
    output_dir: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    issues: tuple[QualityIssue, ...] = ()
