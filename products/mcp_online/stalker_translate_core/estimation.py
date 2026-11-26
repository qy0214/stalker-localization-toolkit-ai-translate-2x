# -*- coding: utf-8 -*-
"""按真实可译规模与实测吞吐估算翻译时长。"""
from __future__ import annotations

import math

from .models import TIER_PROFILES

_VARIANT_FACTORS = {"cuda": 1.0, "cuda13": 1.0, "vulkan": 0.22, "cpu": 0.06}
_ENTRY_OVERHEAD_CHARS = 24
_EXTRA_CHUNK_OVERHEAD_CHARS = 100
_STARTUP_SECONDS = {"cuda": 8.0, "cuda13": 8.0, "vulkan": 10.0, "cpu": 20.0}


def parallel_speedup(parallel):
    routes = max(1, int(parallel or 1))
    return float(routes ** 0.75)


def estimate_translation(info, tier, parallel=1, variant="cuda",
                         measured_speed=None):
    profile = TIER_PROFILES.get(tier) or TIER_PROFILES["mt2"]
    routes = max(1, min(16, int(parallel or 1)))
    files = max(0, int(info.get("translatable_files")
                       or info.get("files_with_text") or 0))
    entries = max(0, int(info.get("translatable_entries")
                         or info.get("n_strings") or 0))
    chars = max(0, int(info.get("translatable_chars") or 0))
    extra_chunks = max(0, int((info.get("extra_chunks") or {}).get(tier, 0)))
    work_units = (chars + entries * _ENTRY_OVERHEAD_CHARS
                  + extra_chunks * _EXTRA_CHUNK_OVERHEAD_CHARS)
    baseline = float(TIER_PROFILES["mt2"]["speed_chars_per_sec"])
    base_speed = float(measured_speed or profile.get("speed_chars_per_sec")
                       or baseline)
    factor = _VARIANT_FACTORS.get(variant, 1.0)
    speed = max(1.0, base_speed * factor)
    scaling = parallel_speedup(routes)
    startup = _STARTUP_SECONDS.get(variant, 10.0)
    seconds = startup + work_units / max(1.0, speed * scaling)
    serial_seconds = work_units / max(1.0, speed)
    return {
        "tier": tier,
        "files": files,
        "entries": entries,
        "chars": chars,
        "extra_chunks": extra_chunks,
        "parallel": routes,
        "variant": variant,
        "chars_per_sec": speed,
        "parallel_speedup": scaling,
        "speedup_vs_mt2": base_speed / baseline,
        "seconds": seconds,
        "low_seconds": seconds * 0.75,
        "high_seconds": seconds * 1.4,
        "per_route_files": int(math.ceil(files / routes)),
        "per_route_entries": int(math.ceil(entries / routes)),
        "per_route_chars": int(math.ceil(chars / routes)),
        "per_route_seconds": serial_seconds / routes,
        "startup_seconds": startup,
        "estimated": bool(measured_speed is None),
    }


def estimate_all_tiers(info, parallel=1, variant="cuda"):
    return {tier: estimate_translation(info, tier, parallel, variant)
            for tier in TIER_PROFILES if tier != "custom"}
