# -*- coding: utf-8 -*-
"""可独立实例化的离线翻译作业：组合单串、文本表、项目与生命周期管线。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time

from . import glossary as _core_glossary
from . import matching as _core_matching
from . import pipeline as _core_pipeline
from . import text as _core_text
from . import xray as _core_xray
from .glossary import (
    GLOSSARY_HINT_STOPWORDS,
    _FREQ_MID_RE,
    _FREQ_NAME_LIMIT,
    _FREQ_NAME_MIN_MID,
    _FREQ_NAME_MIN_TOTAL,
    _FREQ_SCAN_FILE_CAP,
    _FREQ_WORD_RE,
    _SESSION_LIMITS,
    _SESSION_TERM_NOTE,
    session_category,
    session_term_shape,
)
from .job import JobPipeline
from .matching import GlossaryIndex
from .pipeline import (
    _RUNTIME_TOKEN_RE,
    detect_text_lang,
    has_cjk,
    looks_like_code_or_debug,
    looks_like_identifier_text,
    looks_like_prompt_echo,
    safe_chunk_chars,
)
from .project import ProjectPipeline
from .runtime import capture_stdout
from .single_text import SingleTextPipeline
from .table import TablePipeline
from .xray import looks_like_mojibake, read_encoding_chain

_ATOMIC_REPLACE_LOCK = threading.Lock()


class OfflineTranslationJob(SingleTextPipeline, TablePipeline, ProjectPipeline, JobPipeline):
    @property
    def _ctx_hint(self):
        return getattr(self._tls, "ctx", "")

    @_ctx_hint.setter
    def _ctx_hint(self, value):
        self._tls.ctx = value or ""

    @property
    def _style_hint(self):
        return getattr(self._tls, "style", "dialogue")

    @_style_hint.setter
    def _style_hint(self, value):
        self._tls.style = value or "dialogue"

    def __init__(self, parser, server, in_dir, mode, src_lang, tgt_lang,
                 reuse_dir=None, glossary=None, output_anyway=False,
                 force_glossary=True, ctx_size=8192, out_dir=None,
                 log=None, detail=None, progress=None, cancel_event=None,
                 reference_mode=False, copy_reference=True,
                 only_referenced=False, protect_proper=True,
                 preview_reference=False, hard_glossary_keys=None,
                 translate_literals=False, auto_session_terms=False,
                 session_cache_dir=None, tm_lite=True, parallel=1,
                 pending_files=0, freq_names=True, *, termbase=None,
                 data_root="", pipeline_version="", parallel_cap=16,
                 is_writable=None, tm_min_entries=40, tm_min_ratio=0.08):
        self.p = parser
        self.srv = server
        if termbase is None:
            raise ValueError("termbase is required")
        self.termbase = termbase
        self._configured_data_root = data_root
        self._configured_pipeline_version = pipeline_version
        self._writable_fn = is_writable or (lambda _path: True)
        self.tm_min_entries = int(tm_min_entries)
        self.tm_min_ratio = float(tm_min_ratio)
        self.in_dir = in_dir
        self.mode = mode
        self.src_lang = src_lang
        self.tgt = tgt_lang
        self.reuse_dir = reuse_dir
        self.glossary = dict(glossary or {})
        term_layers = self.termbase.load_layers()
        self.gloss_names = set(term_layers["names"])
        # 百科/任务/界面/新闻只做软提示：精确命中才注入，不强制替换。
        self.gloss_soft = set(term_layers.get("reference") or ())
        self.gloss_variants = dict(term_layers["variants"])
        self.gloss_case_sensitive = set(term_layers["case_sensitive"])
        self.gloss_quote_required = set(
            term_layers.get("quote_required") or ())
        # 技术精确表只做整条命中；不进入硬词/提示层。
        for _k, _v in self.termbase.technical().items():
            self.glossary.setdefault(_k, _v)
        self.output_anyway = output_anyway
        self.force_glossary = force_glossary
        self._tls = threading.local()
        self.dialogue_temp = 0.1
        self.batch_stats = {"batches": 0, "lines": 0, "failed": 0}
        self.ctx_size = int(ctx_size)
        # ★ 2.0.2：路数上限从写死的 4 放宽到 parallel_cap（与 LlamaServer 同步）。
        self.parallel = max(1, min(parallel_cap, int(parallel)))
        # 多路并发时所有 worker 共享计数/缓存/账本，写操作统一加锁。
        self._stat_lock = threading.Lock()
        self._ledger_lock = threading.RLock()
        self.safe_chars = safe_chunk_chars(self.ctx_size)
        self.long_chunks = 0
        # ID 引用层模式：文本正文在 configs/text 的条目里，XML/脚本只放 ID。
        # 这里只更新条目正文、原样复制引用层，不改写任何 <text>ID</text>。
        self.reference_mode = bool(reference_mode)
        self.copy_reference = bool(copy_reference)
        self.only_referenced = bool(only_referenced)
        self.protect_proper = bool(protect_proper)
        self.preview_reference = bool(preview_reference)
        self._table_entries = {}     # 绝对路径 -> {id: entity}
        self._id_files = {}          # id -> [绝对路径, ...]
        # ★ 2.0.2 终检：本次**成功改写过**的文本表（rel 集合）。真正的改写
        #   收口只有 _write_table_changes / _write_table_as_utf8 两个出口，
        #   在那里登记，终检只验这几张（原样复制的不用验 XML 结构）。
        self._audit_tables = set()
        self._ref_counts = {}        # id -> 引用次数
        self._ref_files = set()      # 含可解析引用的 XML/脚本
        self._unresolved = {}        # id -> 次数（索引里找不到）
        self._localization = {"language": "", "files": []}
        self._lang_hint = ""
        self.log = log or (lambda *args, **kwargs: None)
        self.detail = detail or (lambda *args, **kwargs: None)
        self.progress = progress or (lambda *args, **kwargs: None)
        self.cancel = cancel_event
        # 自动识别出的「整个 MOD 待翻译文件总数」，用于界面计数显示
        self.pending_files = int(pending_files or 0)
        self.req_count = 0
        self.trans_chars = 0
        # ★ 2.0.3 规则违规遥测：kind -> {hits, retried_ok, fixed,
        #   kept_first, kept_source}。收尾输出「提示词体检」报告，
        #   为提示词迭代提供数据（哪条规则被违反最多）。
        self._audit_stats = {}
        self.redundant = []
        self.failed_strings = 0
        self._parse_failures = []
        self.empty_files = 0
        self._past = set()
        self._temp_clock = 0
        self.out_dir = out_dir or os.path.join(in_dir, "translated_hy_mt2")
        self._excluded_paths = set()
        self.gloss = self._new_gloss_index()
        # None = 兼容旧 API/测试：所有术语都可用；set = 只在这些硬词上
        # 强制/提示（UI 传入 termbase 的 canon 层 + 用户自定义层）。
        self.gloss_hard = (set(hard_glossary_keys)
                           if hard_glossary_keys is not None else None)
        self.translate_literals = bool(translate_literals)
        self.auto_session_terms = bool(auto_session_terms)
        self.auto_freq_names = bool(freq_names)
        self.freq_names = {}
        self.freq_stats = {"candidates": 0, "cached": 0,
                           "translated": 0, "failed": 0}
        self.session_cache_dir = session_cache_dir or os.path.join(
            self._configured_data_root, "session_terms")
        self.session_keys = set()
        # 会话词条只做提示，不参与“整条精确替换”；否则模型生成的坏译名
        # 会绕过 protect_proper 直接写进术语表。
        self.session_only_keys = set()
        self.session_stats = {}
        self.session_requests = 0
        # TM-lite：高重复批次内精确复用；缓存只在本次任务内存里。
        self.tm_lite = bool(tm_lite)
        self.tm_stats = {"scope": "off", "total": 0, "unique": 0,
                         "ratio": 0.0, "hits": 0, "saved_chars": 0}
        self._tm_cache = {}
        self._tm_global_enabled = False
        self._tm_local_enabled = False
        # 完成账本：记录"每个输入文件的指纹 + 输出大小"，取代"输出文件存在就算
        # 译完"。旧做法在两种真实场景下会静默漏译：①游戏更新给 XML 加了新条目，
        # ②ltx/cfg 上次中断留下半截输出 —— 两者都会被"存在即跳过"永久保留。
        self._ledger_path = None
        self._ledger = {}
        self._invalid_generated_tables = set()
        self._ledger_present = False
        self._ledger_saved = 0.0

    def _log_progress(self, idx, total, rel):
        """每 50 个文件记一条 GUI 进度；逐文件明细不再进界面日志。

        实测日志：6122 个文件时逐文件 log 产生近 5000 行、局部 3000 行/秒，
        而界面每条日志整块重绘 400 行 —— 主线程被占满，窗口像卡死。
        明细保留在运行日志里（detail），界面只留可读的进度。
        """
        if idx == 1 or idx == total or idx % 50 == 0:
            self.log("进度 %d/%d（%.0f%%）：%s"
                     % (idx, total, 100.0 * idx / max(total, 1), rel))

    def _cancelled(self):
        return self.cancel is not None and self.cancel.is_set()

    def _parse(self, fn, *args, strict=False):
        """调用 vendor 解析器，并把它的 print 诊断转进详细日志。

        解析器会逐文件打印"用了哪个编码解码"—— 那正是编码猜错时唯一的
        线索，而 GUI 子系统下 stdout 为 None，不接过来就等于没有。

        同时记录单文件解析耗时：vendor 的 cfgxml 正则曾在一类 <texture>
        文件上灾难性回溯，一卡就是几十秒且持有 GIL（整个窗口无响应）。
        修好后仍保留这条"慢文件"证据，下次换别的语料再遇到就能直接定位。
        """
        t0 = time.time()
        with capture_stdout() as cap:
            result = fn(*args)
        elapsed = time.time() - t0
        for line in cap.lines():
            self.detail("  " + line)
            # vendor 解析器把 XML 解析失败吞掉后只 print 诊断并返回空表。
            # 源表按既有产品语义“跳过坏文件”，但必须留下可见告警；
            # 终检阶段（strict=True）则升级为异常计入 bad_parse。
            if "is not a parsable xml" in line:
                name = os.path.basename(str(args[0])) if args else "?"
                if strict:
                    raise ValueError("XML 无法解析：%s" % name)
                failures = getattr(self, "_parse_failures", None)
                if failures is None:
                    failures = []
                    self._parse_failures = failures
                failures.append(name)
        if elapsed >= 2.0:
            name = os.path.basename(str(args[0])) if args else "?"
            self.detail("  解析耗时 %.1f 秒：%s" % (elapsed, name))
            if elapsed >= 10.0:
                self.log("解析耗时异常（%.0f 秒）：%s；如再次出现请反馈该文件"
                         % (elapsed, name), "warn")
        return result

    def _translate_text(self, text, lang, glossary_hits=None, extra_sys=None,
                        context=None, style="dialogue", temperature=None):
        return self.srv.translate(
            text, lang, self.tgt, glossary_hits=glossary_hits,
            extra_sys=extra_sys, context=context, style=style,
            temperature=temperature)

    def _provider_is_running(self):
        checker = getattr(self.srv, "is_running", None)
        return bool(checker()) if checker else True

    def _translate_batch_text(self, lines, lang, glossary_hits=None,
                              temperature=None):
        return self.srv.translate_batch(
            lines, lang, self.tgt, glossary_hits=glossary_hits,
            temperature=temperature)

    def _termbase_lookup(self, text):
        return self.termbase.lookup(text)

    def _termbase_exact_translation(self, text):
        return self.termbase.exact_translation(text)

    def _partial_glossary_translation(self, text, rel=None):
        return self.termbase.partial_translation(text, rel)

    def _data_root(self):
        return self._configured_data_root

    def _is_writable(self, path):
        return self._writable_fn(path)

    def _pipeline_version(self):
        return self._configured_pipeline_version

    def _cut_text(self, text):
        return self.p.cutTextWithSeparator(text)

    def _warn_mojibake(self, rel, samples):
        """单字节解码结果疑似乱码时告警（不自动改判，交人工决定）。"""
        for text in samples:
            if looks_like_mojibake(text):
                self.log("⚠ %s 疑为编码误判（乱码特征）：请用「编码转换」页"
                         "统一转成 UTF-8 后再翻译，否则译文不可用" % rel, "warn")
                return

    def _read_existing(self, path):
        """递归读取目录内全部文本 XML 的已有译文（复用/断点）。"""
        reuse = {}
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if not fn.lower().endswith(".xml"):
                    continue
                full = os.path.join(root, fn)
                try:
                    ents = self.p.parse_xray_text_xml(
                        full, read_encoding_chain(full))
                except Exception as e:
                    self.detail("  复用文件跳过：%s（%s: %s）"
                                % (fn, type(e).__name__, e))
                    continue
                for ent in ents:
                    rec = self.p.getRecommendLangText(ent, self.tgt)[1]
                    if rec:
                        reuse[ent.id] = rec
        return reuse

    def _looks_like_target(self, text):
        """目标语言为中文时，汉字占**正文**比例高即认为已经是中文。

        不能把 `$level`、`%s`、`$$T1$$` 里的拉丁字母算进分母：实测
        `$level $kill_seen_z，如同一个潜行者...` 的汉字占比会被压到 0.3
        以下，已汉化条目被再次送翻，模型把变量名也译成 `$地点`。
        先剔除占位符、XML 标签，再按剩下的字母计算。
        """
        if self.tgt != "chs":
            return False
        clean = _RUNTIME_TOKEN_RE.sub("", text or "")
        clean = re.sub(r"<[^>]+>", "", clean)
        letters = [c for c in clean if c.isalpha()]
        if not letters:
            return False
        han = sum(1 for c in letters if "\u4e00" <= c <= "\u9fff")
        # 0.1 而不是 0.3：`测试Taurus Raging Bull左轮手枪` 这类“中文 + 品牌”
        # 已经是译文，0.3 会把品牌名那点拉丁字母算成“不是中文”，又翻一遍。
        return han / len(letters) > 0.1

    def _new_gloss_index(self):
        if not self.glossary:
            return None
        return GlossaryIndex(
            self.glossary, case_sensitive=self.gloss_case_sensitive,
            variants=self.gloss_variants)

    def _span_context_ok(self, text, start, end, key):
        return _core_matching.span_context_ok(
            text, start, end, key, self.gloss_quote_required)

    @staticmethod
    def _inside_quotes(text, start, end):
        return _core_matching.inside_quotes(text, start, end)

    def _glossary_hits(self, text):
        return _core_matching.glossary_hits(
            text, self.gloss, self.gloss_hard, self.gloss_names,
            self.gloss_soft, self.session_keys, self.gloss_quote_required)

    def _glossary_spans(self, text):
        return _core_matching.glossary_spans(
            text, self.gloss, self.gloss_hard, self.gloss_quote_required)

    @staticmethod
    def _tm_norm(text):
        return _core_text.tm_norm(text)

    def _tm_key(self, text, lang):
        return _core_text.tm_key(text, lang, self._tm_norm)

    def _tm_measure(self, values):
        return _core_text.tm_measure(values, self._tm_norm)

    def _tm_should_enable(self, stats):
        return _core_text.tm_should_enable(
            stats, self.tm_lite, self.tm_min_entries, self.tm_min_ratio)

    def _tm_enable_global(self, values):
        """任务级门控：重复率高才跨表复用；否则完全走原路径。"""
        if not self.tm_lite:
            return False
        stats = self._tm_measure(values)
        self.tm_stats.update(stats)
        if self._tm_should_enable(stats):
            self._tm_global_enabled = True
            self.tm_stats["scope"] = "global"
            self.log("TM-lite：本批重复率 %.1f%%（%d/%d），启用跨表精确复用"
                     % (stats["ratio"] * 100, stats["unique"],
                        stats["total"]), "dim")
            return True
        self.log("TM-lite：未启用（重复率 %.1f%%，门槛 %.0f%%；"
                 "不改变翻译路径）"
                 % (stats["ratio"] * 100, self.tm_min_ratio * 100), "dim")
        return False

    def _tm_enable_table(self, values, rel):
        """表级门控：任务级没启用时，只在高重复表内复用。"""
        self._tm_local_enabled = False
        if not self.tm_lite or self._tm_global_enabled:
            return
        stats = self._tm_measure(values)
        if not self._tm_should_enable(stats):
            return
        self._tm_local_enabled = True
        self._tm_cache = {}
        self.tm_stats.update(stats)
        self.tm_stats["scope"] = "table"
        self.log("TM-lite：%s 重复率 %.1f%%（%d/%d），表内精确复用"
                 % (rel, stats["ratio"] * 100, stats["unique"],
                    stats["total"]), "dim")

    def _tm_lookup(self, text, lang):
        if not (self._tm_global_enabled or self._tm_local_enabled):
            return None
        return self._tm_cache.get(self._tm_key(text, lang))

    def _tm_store(self, text, lang, out):
        if not (self._tm_global_enabled or self._tm_local_enabled):
            return
        src = self._tm_norm(text)
        if len(src) < 4 or not out or out == text:
            return
        if self.tgt == "chs" and not has_cjk(out):
            return
        self._tm_cache.setdefault(self._tm_key(text, lang), out)

    def _temp_for_style(self):
        return _core_pipeline.temp_for_style(self._style_hint, self.dialogue_temp)

    def _rule_hit(self, kind, outcome):
        """规则违规遥测：kind=规则名，outcome=retried_ok/fixed/
        kept_first/kept_source。线程安全（复用 _stat_lock）。"""
        with self._stat_lock:
            s = self._audit_stats.setdefault(kind, {})
            s["hits"] = s.get("hits", 0) + 1
            s[outcome] = s.get(outcome, 0) + 1

    def _audit_report(self):
        """任务收尾的「提示词体检」：按触发次数排序点名最高频违规规则。
        零违规只落明细日志（GUI 日志风暴防线，T29），有违规才上界面。"""
        if not self._audit_stats:
            self.detail("提示词体检：全部规则零违规，内置提示词与规则约束有效")
            return
        ranked = sorted(self._audit_stats.items(),
                        key=lambda kv: -kv[1].get("hits", 0))
        labels = (("retried_ok", "重试成功"), ("fixed", "自动修复"),
                  ("kept_first", "保留首译"), ("kept_source", "保留原文"))
        parts = []
        for kind, s in ranked:
            bits = ["%d 次" % s.get("hits", 0)]
            bits += ["%s %d" % (lab, s[oc]) for oc, lab in labels
                     if s.get(oc)]
            parts.append("%s（%s）" % (kind, " / ".join(bits)))
        self.log("提示词体检：" + "；".join(parts))
        self.log("最需要关注的规则：%s（触发最多，优先迭代）" % ranked[0][0],
                 "warn")

    @staticmethod
    def _inline_term_value(text, start, end, value):
        return _core_matching.inline_term_value(text, start, end, value)

    def _repair_residual_source_terms(self, src, out):
        return _core_matching.repair_residual_source_terms(
            src, out, self.gloss, self.glossary, self.gloss_hard,
            self.gloss_quote_required, self.detail)

    def _repair_residual_latin_names(self, src, out):
        return _core_matching.repair_residual_latin_names(
            src, out, self.gloss, self.glossary, self.gloss_names, self.detail)

    def _repair_known_semantic_terms(self, src, out, lang=""):
        return _core_pipeline.repair_known_semantic_terms(src, out, lang)

    def _session_tables(self):
        """当前 MOD 的文本表（兼容 configs/text 与 config/text 两种布局）。"""
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                if not fn.lower().endswith(".xml"):
                    continue
                rel = os.path.relpath(os.path.join(root, fn), self.in_dir)
                low = rel.lower().replace(os.sep, "/")
                if not (low.startswith("configs/text/")
                        or low.startswith("config/text/")):
                    continue
                if "sgtat_auto_texts" in low or "/___" in low:
                    continue
                yield rel, os.path.join(root, fn)

    def _session_candidates(self):
        """抽取名字型候选；返回 {lower: (source, category, lang, rel, id)}。"""
        core = {k.lower() for k in self.glossary}
        stop = set(GLOSSARY_HINT_STOPWORDS) | {
            "нет", "да", "все", "всё", "это", "так", "там", "тут", "очень",
            "можно", "нужно", "надо", "есть", "был", "была", "были",
        }
        cand = {}
        self._session_core_hits = set()
        for rel, full in self._session_tables():
            if self._cancelled():
                return cand
            try:
                ents = self._parse(self.p.parse_xray_text_xml, full,
                                   read_encoding_chain(full))
            except Exception:
                continue
            for ent in ents:
                cat = session_category(rel, ent.id)
                if not cat:
                    continue
                lang, src = self._pick_source(ent)
                if not src or has_cjk(src):
                    continue
                bare = session_term_shape(src, cat)
                if not bare:
                    continue
                low = bare.lower()
                if low in core:
                    # 核心词已在通用库；只把它登记为“本次 MOD 见过”，
                    # 让对话里也能作为软提示，不重新翻译、不硬替换。
                    self._session_core_hits.add(bare)
                    continue
                if (low in stop
                        or looks_like_code_or_debug(rel, bare)
                        or looks_like_identifier_text(bare)):
                    continue
                if low not in cand:
                    cand[low] = (bare, cat,
                                 detect_text_lang(bare) or lang or "rus",
                                 rel, ent.id)
        order = {"人物与军衔": 0, "阵营与组织": 1, "地点与区域": 2,
                 "变异生物": 3, "物品·武器·装备·神器": 4,
                 "成就·称号·属性标签": 5}
        rows = sorted(cand.values(),
                      key=lambda x: (order.get(x[1], 9), len(x[0]), x[0]))
        kept = {}
        used = {k: 0 for k in _SESSION_LIMITS}
        for src, cat, lang, rel, eid in rows:
            if used.get(cat, 0) >= _SESSION_LIMITS.get(cat, 0):
                continue
            used[cat] += 1
            kept[src.lower()] = (src, cat, lang, rel, eid)
        return kept

    @staticmethod
    def _clean_term_line(line):
        return _core_glossary.clean_term_line(line)

    def _translate_session_batch(self, terms, lang):
        """批量翻译名词；行数不符时返回空，交回单条重试。"""
        self.session_requests += 1
        try:
            out = self._translate_text(
                "\n".join(terms), lang,
                extra_sys=_SESSION_TERM_NOTE)
        except Exception:
            return {}
        lines = [self._clean_term_line(x) for x in (out or "").splitlines()
                 if x.strip()]
        if len(lines) != len(terms):
            return {}
        result = {}
        for src, dst in zip(terms, lines):
            if (dst and dst != src and has_cjk(dst)
                    and not looks_like_prompt_echo(dst) and len(dst) <= 60):
                result[src] = dst
        return result

    def _build_session_glossary(self):
        """扫描当前 MOD，补齐名字型术语；缓存只在本机，不随包。"""
        if not self.srv or not self._provider_is_running():
            return
        cand = self._session_candidates()
        if not cand:
            self.session_keys = set(getattr(self, "_session_core_hits", set()))
            self.session_stats = {"candidates": 0, "cached": 0,
                                  "translated": 0, "failed": 0}
            return
        raw = "\n".join("%s|%s|%s" % (v[3], v[4], v[0])
                        for v in sorted(cand.values()))
        fp = hashlib.sha1(raw.encode("utf-8", "replace"),
                          usedforsecurity=False).hexdigest()
        cache_path = os.path.join(self.session_cache_dir, fp + ".json")
        cached = {}
        try:
            data = json.load(open(cache_path, encoding="utf-8"))
            if isinstance(data.get("terms"), dict):
                cached = {str(k): str(v) for k, v in data["terms"].items()}
        except Exception:
            cached = {}
        cache_low = {k.lower(): v for k, v in cached.items()}
        translated = {}
        missing = [(v[0], v[2]) for low, v in cand.items()
                   if low not in cache_low]
        by_lang = {}
        for src, lang in missing:
            by_lang.setdefault(lang, []).append(src)
        for lang, items in by_lang.items():
            for i in range(0, len(items), 20):
                batch = items[i:i + 20]
                got = self._translate_session_batch(batch, lang)
                translated.update(got)
                for src in batch:
                    if src in got:
                        continue
                    self.session_requests += 1
                    try:
                        out = self._translate_text(
                            src, lang,
                            extra_sys=_SESSION_TERM_NOTE)
                    except Exception:
                        continue
                    dst = self._clean_term_line(out)
                    if (dst and dst != src and has_cjk(dst)
                            and not looks_like_prompt_echo(dst)
                            and len(dst) <= 60):
                        translated[src] = dst
        merged = dict(cached)
        merged.update(translated)
        self.session_keys = set(merged) | set(
            getattr(self, "_session_core_hits", set()))
        self.session_only_keys = set(merged)
        for src, dst in merged.items():
            self.glossary[src] = dst
        self.gloss = self._new_gloss_index()
        try:
            os.makedirs(self.session_cache_dir, exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"_format": "ai-translate-session-terms/1",
                           "fingerprint": fp, "terms": merged},
                          f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, cache_path)
        except OSError:
            pass
        self.session_stats = {
            "candidates": len(cand),
            "cached": len(cache_low),
            "translated": len(translated),
            "failed": max(0, len(missing) - len(translated)),
        }
        self.log("会话词表：候选 %d 条，缓存 %d，新译 %d，失败 %d（本机缓存，不随包）"
                 % (self.session_stats["candidates"],
                    self.session_stats["cached"],
                    self.session_stats["translated"],
                    self.session_stats["failed"]), "dim")

    def _build_freq_names(self):
        """★ 2.0.3 高频专名定译：全文扫描正文高频词，复用会话词表的
        批量定译与本机缓存管线，结果并入**软提示层**（术语提示只在语义
        相符时采用），收敛自造译名的跨文件漂移。"""
        if not self.srv or not self._provider_is_running():
            return
        total, mid, lang_of = {}, {}, {}
        scanned = 0
        for rel, full in self._collect_text_targets():
            if self._cancelled():
                return
            if scanned >= _FREQ_SCAN_FILE_CAP:
                break
            scanned += 1
            try:
                ents = self._parse(self.p.parse_xray_text_xml, full,
                                   read_encoding_chain(full))
            except Exception:
                continue
            for ent in ents:
                lang, src = self._pick_source(ent)
                if not src or has_cjk(src):
                    continue
                for w in _FREQ_WORD_RE.findall(src):
                    lang_of.setdefault(w, lang)
                    total[w] = total.get(w, 0) + 1
                for w in _FREQ_MID_RE.findall(src):
                    mid[w] = mid.get(w, 0) + 1
        stop = GLOSSARY_HINT_STOPWORDS
        cand = []
        for w, n in total.items():
            if n < _FREQ_NAME_MIN_TOTAL or mid.get(w, 0) < _FREQ_NAME_MIN_MID:
                continue
            if len(w) <= 3 or w.lower() in stop or w in self.glossary:
                continue
            cand.append((n, w))
        cand.sort(reverse=True)
        cand = [w for _n, w in cand[:_FREQ_NAME_LIMIT]]
        self.freq_stats = {"candidates": len(cand), "cached": 0,
                           "translated": 0, "failed": 0}
        if not cand:
            self.freq_names = {}
            return
        raw = "\n".join("%s|%s" % (lang_of.get(w, self.src_lang), w)
                        for w in cand)
        fp = hashlib.sha1(
            ("freq/1/" + raw).encode("utf-8", "replace"),
            usedforsecurity=False).hexdigest()
        cache_path = os.path.join(self.session_cache_dir,
                                  "freq_" + fp + ".json")
        cached = {}
        try:
            data = json.load(open(cache_path, encoding="utf-8"))
            if isinstance(data.get("terms"), dict):
                cached = {str(k): str(v) for k, v in data["terms"].items()}
        except Exception:
            cached = {}
        cache_low = {k.lower(): v for k, v in cached.items()}
        self.freq_stats["cached"] = len(cache_low)
        translated = {}
        missing = [(w, lang_of.get(w, self.src_lang)) for w in cand
                   if w.lower() not in cache_low]
        by_lang = {}
        for w, lang in missing:
            by_lang.setdefault(lang, []).append(w)
        for lang, items in by_lang.items():
            for i in range(0, len(items), 20):
                batch = items[i:i + 20]
                got = self._translate_session_batch(batch, lang)
                translated.update(got)
                for w in batch:
                    if w in got:
                        continue
                    self.session_requests += 1
                    try:
                        out = self._translate_text(
                            w, lang,
                            extra_sys=_SESSION_TERM_NOTE)
                    except Exception:
                        continue
                    dst = self._clean_term_line(out)
                    if (dst and dst != w and has_cjk(dst)
                            and not looks_like_prompt_echo(dst)
                            and len(dst) <= 60):
                        translated[w] = dst
        merged = dict(cached)
        merged.update(translated)
        self.freq_stats["translated"] = len(translated)
        self.freq_stats["failed"] = max(0, len(missing) - len(translated))
        self.freq_names = merged
        self.glossary.update(merged)
        self.gloss_soft |= set(merged)
        self.gloss = self._new_gloss_index()
        try:
            os.makedirs(self.session_cache_dir, exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"_format": "ai-translate-freq-names/1",
                           "fingerprint": fp, "terms": merged},
                          f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, cache_path)
        except OSError:
            pass
        self.log("高频专名定译：候选 %d，缓存 %d，新译 %d（软提示层）"
                 % (self.freq_stats["candidates"],
                    self.freq_stats["cached"],
                    self.freq_stats["translated"]), "dim")

    def _write_atomic(self, out_path, writer):
        """先写同目录唯一临时文件再替换，避免并发作业互相覆盖。"""
        parent = os.path.dirname(out_path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".ait_", suffix=".tmp", dir=parent)
        os.close(fd)
        try:
            writer(tmp)
            # Windows 并发替换同一目标时可能互相拒绝访问；临时文件仍并行写。
            with _ATOMIC_REPLACE_LOCK:
                os.replace(tmp, out_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @staticmethod
    def _looks_like_ref_value(value):
        return _core_xray.looks_like_ref_value(value)

    @staticmethod
    def _entry_style(rel, src):
        return _core_pipeline.entry_style(rel, src)

    def _scene_context(self, metas, idx):
        return _core_pipeline.scene_context(metas, idx)

    def _parse_numbered_batch(self, out, n):
        return _core_pipeline.parse_numbered_batch(out, n)

    @staticmethod
    def _patch_table_text(text, eid, lang, new_text):
        return _core_xray.patch_table_text(text, eid, lang, new_text)

    @staticmethod


    @staticmethod
    def _merge_generated_extract(target, source):
        return _core_xray.merge_generated_extract(target, source)
