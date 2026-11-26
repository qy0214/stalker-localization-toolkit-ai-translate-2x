# -*- coding: utf-8 -*-
"""可选俄语词形归一；依赖缺失时只做保守归一。"""
from __future__ import annotations

import re


class MorphologyNormalizer:
    def __init__(self, enabled: bool = True):
        self._analyzer = None
        if enabled:
            try:
                from pymorphy3 import MorphAnalyzer
                self._analyzer = MorphologyAnalyzerAdapter(MorphAnalyzer())
            except Exception:
                self._analyzer = None

    @property
    def available(self) -> bool:
        return self._analyzer is not None

    def normalize(self, term: str, lang: str = "rus") -> str:
        value = str(term or "").strip().casefold().replace("ё", "е")
        if not value or lang not in ("rus", "ukr") or self._analyzer is None:
            return value
        try:
            parsed = self._analyzer.parse(value)
            if parsed and float(getattr(parsed, "score", 0.0)) >= 0.5:
                return str(parsed.normal_form).casefold().replace("ё", "е")
        except Exception:
            pass
        return value


class MorphologyAnalyzerAdapter:
    def __init__(self, analyzer):
        self.analyzer = analyzer

    def parse(self, value: str):
        return self.analyzer.parse(value)[0]


def conservative_stem(term: str) -> str:
    value = str(term or "").strip().casefold().replace("ё", "е")
    return re.sub(r"(?:ами|ями|ах|ях|ом|ем|ой|ий|ый|ая|яя|ов|ев|ам|ям|ах|ях)$",
                  "", value)
