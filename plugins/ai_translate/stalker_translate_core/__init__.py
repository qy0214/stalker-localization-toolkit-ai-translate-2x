# -*- coding: utf-8 -*-
"""共享翻译核心。"""
from .assets import AssetBundle, pipeline_fingerprint, resolve_asset_root, sha256_file
from .domain import (
    Budget,
    JobSpec,
    JobState,
    JobStatus,
    QualityIssue,
    TokenUsage,
    TranslationRequest,
    TranslationResult,
)
from .glossary import (
    GlossaryIndex,
    UserGlossaryStore,
    flatten_glossary,
    glossary_hint_block,
    layer_counts,
    load_glossary_json,
    load_termbase_document,
    session_category,
    session_term_shape,
)
from .mcp import PROTOCOL_VERSION, SERVER_INSTRUCTIONS, TOOL_DEFINITIONS, tool_result
from .offline_job import OfflineTranslationJob
from .termbase import Termbase
from .pipeline import (
    OutputTruncated,
    _has_translatable_text,
    budget_max_tokens,
    detect_text_lang,
    runtime_tokens_match,
    semantic_role_issues,
)
from .ports import (
    AuditSinkPort,
    CancellationTokenPort,
    GlossaryPort,
    ParserPort,
    ProviderPort,
    ProjectRepositoryPort,
    TextProviderPort,
    TextRepositoryPort,
    UsageSinkPort,
)
from .single_text import SingleTextPipeline
from .table import TablePipeline

__all__ = [
    "AssetBundle",
    "AuditSinkPort",
    "Budget",
    "CancellationTokenPort",
    "GlossaryIndex",
    "GlossaryPort",
    "JobSpec",
    "JobState",
    "JobStatus",
    "OfflineTranslationJob",
    "OutputTruncated",
    "ParserPort",
    "ProjectRepositoryPort",
    "PROTOCOL_VERSION",
    "ProviderPort",
    "QualityIssue",
    "SERVER_INSTRUCTIONS",
    "SingleTextPipeline",
    "TablePipeline",
    "Termbase",
    "TextProviderPort",
    "TextRepositoryPort",
    "TOOL_DEFINITIONS",
    "TokenUsage",
    "TranslationRequest",
    "TranslationResult",
    "UsageSinkPort",
    "UserGlossaryStore",
    "_has_translatable_text",
    "budget_max_tokens",
    "detect_text_lang",
    "flatten_glossary",
    "glossary_hint_block",
    "layer_counts",
    "load_glossary_json",
    "load_termbase_document",
    "pipeline_fingerprint",
    "resolve_asset_root",
    "runtime_tokens_match",
    "semantic_role_issues",
    "session_category",
    "session_term_shape",
    "sha256_file",
    "tool_result",
]

__version__ = "1.0.0"
