# -*- coding: utf-8 -*-
"""产品装配：配置、Provider、共享术语、服务与任务管理器。"""
from __future__ import annotations

from pathlib import Path

from stalker_translate_core import AssetBundle, GlossaryIndex, resolve_asset_root

from .config import Config, load_config
from .jobs import JobManager
from .provider import OpenAICompatibleProvider, ProviderSettings
from .service import TranslationService


def create_app(config_path: str | Path | None = None):
    cfg = load_config(config_path)
    asset_root = resolve_asset_root(
        Path(__file__).resolve().parent.parent / "__init__.py", "assets")
    provider = OpenAICompatibleProvider(ProviderSettings(
        base_url=cfg.base_url, model=cfg.model, api_key=cfg.api_key,
        timeout_sec=cfg.timeout_sec, max_retries=cfg.max_retries,
    ))
    glossary = GlossaryIndex.from_json(asset_root / "stalker_termbase.json")
    service = TranslationService(cfg, provider, glossary,
                                 AssetBundle.load(asset_root).fingerprint())
    jobs = JobManager(cfg, service)
    return cfg, provider, service, jobs


def check_env(cfg: Config, provider: OpenAICompatibleProvider,
              asset_root: Path, deep: bool = False,
              service: TranslationService | None = None) -> dict:
    endpoint = provider.health()
    bundle = AssetBundle.load(asset_root)
    result = {
        "ready": bool(cfg.model and endpoint.get("ok")),
        "server": {"name": "stalker-translate-online", "version": "0.2.0"},
        "core": "1.0.1",
        "provider": provider.name,
        "pipeline_mode": "shared_core_full_chain",
        "model": provider.model,
        "endpoint": endpoint,
        "config": cfg.public_dict(),
        "asset_sha256": dict(bundle.hashes),
        "pipeline": service.pipeline if service else "",
    }
    if deep and endpoint.get("ok") and service:
        try:
            probe = service.translate("Hello, stalker.", "eng", "chs",
                                      use_cache=False)
            result["probe_translation"] = probe.text
            result["probe_ok"] = not probe.retained_source
        except Exception as exc:
            result["probe_ok"] = False
            result["probe_error"] = "%s: %s" % (type(exc).__name__, exc)
    return result
