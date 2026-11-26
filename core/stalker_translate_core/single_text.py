# -*- coding: utf-8 -*-
"""共享单串翻译链：占位符、重试、质量闸门与确定性修复。"""
from __future__ import annotations

import re

from .pipeline import (
    OutputTruncated,
    _ANY_PLACEHOLDER_RE,
    _CYR_WORD_RE,
    _PLACEHOLDER_RE,
    _PLACEHOLDER_RETRY_NOTE,
    _PROFANITY_RETRY_NOTE,
    _RESIDUAL_CYRILLIC_NOTE,
    clean_injected_bullets,
    clean_stuck_article,
    clean_translation_output,
    detect_text_lang,
    escape_newlines,
    find_glue_residue,
    has_cjk,
    has_source_profanity,
    has_zh_profanity,
    looks_like_context_bleed,
    looks_like_prompt_echo,
    mask_runtime_tokens,
    missing_big_numbers,
    missing_model_tokens,
    normalize_cn_punctuation,
    patch_missing_numbers,
    reconcile_literal_newlines,
    repair_model_tokens,
    restore_linebreaks,
    restore_runtime_tokens,
    runtime_tokens_match,
    semantic_role_issues,
    style_cleanup_cn,
    unescape_newlines,
)
from .text import split_long_text, split_protected


class SingleTextPipeline:
    def _translate_chunk(self, content, lang, hints, depth=0, note=""):
        """翻译单个句段：超出该档安全长度时按句切分逐段翻译并拼回，
        保证不超长、不截断（长文质量保护）。

        depth 用于处理"模型输出被 max_tokens 截断"：收到 OutputTruncated
        就把该段一分为二重译，最多递归 3 层（宁可多切几刀，也不能把半句
        译文写进汉化包）。"""
        if len(content) <= self.safe_chars:
            try:
                return self._translate_text(
                    content, lang, glossary_hits=hints,
                    extra_sys=note or None,
                    context=getattr(self, "_ctx_hint", "") or None,
                    style=getattr(self, "_style_hint", "dialogue"),
                    temperature=self._temp_for_style())
            except OutputTruncated:
                if depth >= 3 or len(content) < 8:
                    self.log("  输出被截断且已无法再切分（%d 字符），"
                             "建议改用更大上下文档位" % len(content), "warn")
                    raise
                self.log("  输出被截断，按句再切一次重译（%d 字符）"
                         % len(content), "warn")
                return "".join(
                    self._translate_chunk(seg, lang, hints, depth + 1, note)
                    for seg in split_long_text(
                        content, max(4, len(content) // 2)))
        parts = split_protected(content, self.safe_chars, _ANY_PLACEHOLDER_RE)
        if len(parts) > 1:
            with self._stat_lock:
                self.long_chunks += max(0, len(parts) - 1)
            self.log("  长文本 %d 字符 → 自动切为 %d 段翻译（%s 上下文窗）"
                     % (len(content), len(parts), self.ctx_size))
        return "".join(
            self._translate_chunk(seg, lang, hints, depth + 1, note)
            for seg in parts)

    def _translate_segments(self, text, lang, note=""):
        """整条翻译；占位符在 `_translate_one` 里已 mask，不能在这里按
        换行/`$$ACTION$$` 再切碎。

        旧实现调用 `cutTextWithSeparator`：它把 `\\n` 当分隔符，多行描述被
        拆成一段段单独翻译，实测 8 条藏匿点描述发了 56 次请求，行与行之间
        失去上下文。这里只保留“整条精确命中术语”的快捷路径，其余交给
        `_translate_chunk`（超长时仍按句切分，保留占位符）。
        """
        if not text.strip():
            return text
        stripped = text.strip()
        if (not self.force_glossary and stripped in self.glossary
                and stripped not in self.session_only_keys):
            self._tls.translation_origin = "glossary_exact"
            with self._stat_lock:
                self.req_count += 1
            return text.replace(stripped, self.glossary[stripped])
        with self._stat_lock:
            self.req_count += 1
            self.trans_chars += len(text)
        hints = None if self.force_glossary else self._glossary_hits(text)
        return self._translate_chunk(text, lang, hints, note=note)

    def _translate_one(self, text, lang, context="", style="dialogue"):
        """翻译一个文本串；运行期占位符先 mask，译后校验并回填。

        校验失败（模型改了/丢了 `$name`、`%s`、`\n` 等）时**保留原文**，
        绝不把坏译文写进文本表。这是硬约束，不依赖提示词。
        """
        # ★ 2.0.8 源头减险：极短条目（<12 字符，如 "Я не готов."）不注入
        # 场景上下文。这类条目几乎没有正文压住 prompt 前面的「前一句：…」，
        # 1.8B 小模型最容易把上下文整段当正文翻出（实测 bar_arena_man_job_172
        # 被上一条的长译文覆盖）。阈值只取最危险的极短串，正常短对话仍保留
        # 上下文帮助消歧；其余污染由下面的 bleed 检测+去上下文重试兜住。
        self._ctx_hint = "" if len((text or "").strip()) < 12 \
            else (context or "")
        self._style_hint = style or "dialogue"
        self._tls.translation_origin = "model"
        self._tls.rule_results = []
        if lang in ("auto", "text"):
            lang = detect_text_lang(text) or self._lang_hint or "rus"
        elif lang in ("rus", "eng"):
            det = detect_text_lang(text)
            if det and det != lang:
                lang = det
        if lang == self.tgt:
            return text
        hit = self._tm_lookup(text, lang)
        if hit is not None:
            self._tls.translation_origin = "tm_lite"
            with self._stat_lock:
                self.tm_stats["hits"] += 1
                self.tm_stats["saved_chars"] += len(text)
            self.detail("  TM-lite 精确复用：%s" % str(text)[:60])
            return hit
        normalized = unescape_newlines(text)
        masked, mapping = mask_runtime_tokens(normalized)
        if not mapping:
            out = self._translate_plain(normalized, lang)
        else:
            out = self._translate_plain(masked, lang)
            restored = restore_runtime_tokens(out, mapping)
            if restored is None:
                # 颜色控制符（%c[...]）在带术语提示的请求里偶发被模型
                # 吞掉；先按“逐字保留控制符”重试一次，仍失败才保留原文。
                try:
                    retry_raw = self._translate_plain(
                        masked, lang, note=_PLACEHOLDER_RETRY_NOTE)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                except Exception:
                    retry = None
                if retry is None:
                    # ★ 2.0.17 第三级兜底：切短块逐块译（占位符用
                    # split_protected 保护不被切断），与完整性闸门同一
                    # 模式。实测动态新闻/物品描述里含 $clr_N、%c[...]、
                    # %s 的条目模型最容易破坏占位符，旧版在这里整条
                    # 保留英文——切片能救回大多数。切片也失败才保留原文。
                    sliced = None
                    try:
                        raw = self._force_sliced(masked, lang,
                                                 note=_PLACEHOLDER_RETRY_NOTE)
                        sliced = restore_runtime_tokens(raw, mapping) \
                            if raw is not None else None
                    except Exception:
                        sliced = None
                    if sliced is not None:
                        self.detail("  运行占位符被破坏，切片翻译成功：%s"
                                    % str(text)[:50])
                        self._rule_hit("占位符保留", "sliced_ok")
                        out = sliced
                    else:
                        self.detail("  运行占位符未原样保留，保留原文：%s"
                                    % str(text)[:60])
                        self._rule_hit("占位符保留", "kept_source")
                        self.failed_strings += 1
                        return text
                else:
                    self.detail("  控制符未保留，已按逐字保留要求重试：%s"
                                % str(text)[:50])
                    self._rule_hit("占位符保留", "retried_ok")
                    out = retry
            else:
                out = restored
        if looks_like_prompt_echo(out, normalized):
            self.detail("  模型回显提示词，保留原文：%s" % str(text)[:60])
            self._rule_hit("提示词回显", "kept_source")
            self.failed_strings += 1
            return text
        # ★ 2.0.8 上下文串入闸门：模型把「前一句/后一句」上下文当正文翻出，
        # 或极短源文吐出超长段落（上一条译文）。这些标签/段落绝不属于本条
        # 译文。去掉上下文重译一次（清 _ctx_hint，否则重试仍带同一上下文）；
        # 仍异常则保留原文，绝不把相邻条目的译文写进本条。
        if looks_like_context_bleed(normalized, out):
            self.detail("  上下文串入译文，去掉上下文重译：%s"
                        % str(text)[:60])
            saved_ctx = self._ctx_hint
            self._ctx_hint = ""
            try:
                if mapping:
                    retry_raw = self._translate_plain(masked, lang)
                    retry_ctx = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry_ctx = self._translate_plain(normalized, lang)
            except Exception:
                retry_ctx = None
            if retry_ctx and not looks_like_prompt_echo(retry_ctx, normalized) \
                    and not looks_like_context_bleed(normalized, retry_ctx):
                self.detail("  去上下文重译成功：%s" % str(text)[:50])
                self._rule_hit("上下文串入", "retried_ok")
                out = retry_ctx
            else:
                # ★ 2.0.25（aera 全量审计 D12 签名②）：去上下文重译仍串入
                # 时不再直接保留原文——先试切片强译（实测 camelbak 描述
                # 341 字符因此整条漏译）。切片结果同样过串入检测。
                sliced_ctx = None
                try:
                    raw = self._force_sliced(masked, lang)
                    sliced_ctx = restore_runtime_tokens(raw, mapping) \
                        if (raw is not None and mapping) else raw
                except Exception:
                    sliced_ctx = None
                if (sliced_ctx and not looks_like_prompt_echo(sliced_ctx, normalized)
                        and has_cjk(sliced_ctx)
                        and not looks_like_context_bleed(normalized,
                                                         sliced_ctx)):
                    self.detail("  去上下文重译仍串入，切片强译成功：%s"
                                % str(text)[:50])
                    self._rule_hit("上下文串入", "sliced_ok")
                    out = sliced_ctx
                else:
                    self.detail("  去上下文重译仍异常，保留原文：%s"
                                % str(text)[:60])
                    self._rule_hit("上下文串入", "kept_source")
                    self.failed_strings += 1
                    return text
        # 残留源词确定性替换：模型把专名原样保留时，直接按词库换掉；
        # 不调用模型，也不改动句子其它部分。替换后再看是否还残留俄文。
        out = self._repair_residual_source_terms(normalized, out)
        out = self._repair_residual_latin_names(normalized, out)
        out = self._repair_known_semantic_terms(normalized, out, lang)
        out = self._enforce_preferred_terms(normalized, out)
        if self._style_hint == "dialogue" and has_cjk(out):
            out = style_cleanup_cn(out)
        # 多行保真：模型偶尔把整段合并成一行；带明确数量再试一次。
        source_nl = normalized.count("\n")
        if source_nl and out.count("\n") != source_nl:
            note = ("原文有 %d 个换行，必须输出 %d 个换行，逐行对应；"
                    "不得合并段落，也不得多加空行。" % (source_nl, source_nl))
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=note)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=note)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry, normalized) \
                    and retry.count("\n") == source_nl:
                self.detail("  换行数不符，已按原文段数重试：%s"
                            % str(text)[:50])
                self._rule_hit("换行保真", "retried_ok")
                out = retry
            else:
                fixed = restore_linebreaks(out, source_nl)
                if fixed != out:
                    self.detail("  换行数不符（%d→%d），按句末标点补回"
                                % (source_nl, out.count("\n")))
                    self._rule_hit("换行保真", "fixed")
                    out = fixed
                else:
                    self.detail("  换行数与原文不一致（%d→%d），保留首次译文"
                                % (source_nl, out.count("\n")))
                    self._rule_hit("换行保真", "kept_first")
        # ★ 2.0.3 长度比闸门：原文较长而译文长度比异常，多半是漏译
        # （模型只翻了前半句）或过度扩写。带针对性要求重试一次；
        # 仍异常则保留首次译文（漏译的译文仍比俄文原文强，不回退原文）。
        # 短文本（<40 字符或可译词 <4）噪声大，不检；短标签不检。
        #
        # ★ 2.0.7 下限按语言对：俄→中天然压缩（西里尔 2 字节/字符、中文
        # 信息密度高），实测bio表 0.24~0.35 全是正确译文；旧下限 0.30
        # 把 77 条正确译文判成漏译、各白跑一次重试。英→中类似。
        # 同语系/中→外维持 0.30。上限 2.0 不变（扩写仍要抓）。
        _to_zh = self.tgt in ("zh", "chs", "zh-CN", "zh-Hans", "简体中文",
                              "中文", "chs")
        _low_ratio = 0.15 if (_to_zh and lang in ("rus", "eng", "ukr", "auto",
                                                  "text")) else 0.30
        if (self._style_hint != "label" and len(normalized.strip()) >= 40
                and len(re.findall(r"[а-яёА-ЯЁ]{2,}|[A-Za-z]{2,}",
                                   normalized)) >= 4):
            src_len = max(1, len(normalized.strip()))
            ratio = len(out.strip()) / src_len
            # 漏译的真正特征：译文短到几乎没内容（<12 字符），无论语言对
            # 都要抓；否则用语言对下限。
            if ratio < _low_ratio and len(out.strip()) < 12 \
                    or not (_low_ratio <= ratio <= 2.0):
                note = ("译文长度与原文严重不符，疑似漏译或过度扩写；"
                        "逐句对照原文完整翻译，不增译、不漏译、不概括。")
                try:
                    if mapping:
                        retry_raw = self._translate_plain(
                            masked, lang, note=note)
                        retry = restore_runtime_tokens(retry_raw, mapping)
                    else:
                        retry = self._translate_plain(
                            normalized, lang, note=note)
                except Exception:
                    retry = None
                ok_ratio = False
                if retry and not looks_like_prompt_echo(retry, normalized) \
                        and _low_ratio <= len(retry.strip()) / src_len <= 2.0:
                    ok_ratio = True
                if ok_ratio:
                    self.detail("  译文长度比异常（%.2f），已按完整对照重试：%s"
                                % (ratio, str(text)[:50]))
                    self._rule_hit("长度比（漏译/扩写）", "retried_ok")
                    out = retry
                else:
                    self.detail("  译文长度比异常（%.2f），重试未恢复，"
                                "保留首次译文：%s"
                                % (ratio, str(text)[:50]))
                    self._rule_hit("长度比（漏译/扩写）", "kept_first")
        # ★ 2.0.9 数字保真闸门：源文的大额数字（金额/年份/坐标/密码）必须在
        # 译文中原样出现（含千分位或中文万/千计数）。实测源文 30000 卢布被
        # 译成 15,000，报酬腰斩。带“逐字保留数字”要求重试一次；仍缺则保留
        # 原文——错误的金额比未翻译的俄文更有害，原文至少不会误导玩家。
        if _to_zh:
            _missed = missing_big_numbers(normalized, out)
            if _missed:
                self.detail("  译文大额数字缺失/被改（%s），按逐字保留重试：%s"
                            % (",".join(_missed[:4]), str(text)[:50]))
                note = ("原文中的数字（尤其是金额、年份、坐标、密码）必须"
                        "逐字保留，不得换算、四舍五入或改写；"
                        "例如 30000 就写 30000，不要写成其它数字。")
                try:
                    if mapping:
                        retry_raw = self._translate_plain(
                            masked, lang, note=note)
                        retry_num = restore_runtime_tokens(retry_raw, mapping)
                    else:
                        retry_num = self._translate_plain(normalized, lang,
                                                          note=note)
                except Exception:
                    retry_num = None
                still = missing_big_numbers(normalized, retry_num) \
                    if retry_num else _missed
                if retry_num and not looks_like_prompt_echo(retry_num, normalized) \
                        and not still:
                    self.detail("  数字保真重试成功：%s" % str(text)[:50])
                    self._rule_hit("数字保真", "retried_ok")
                    out = retry_num
                else:
                    # ★ 2.0.12：不再整段回退原文。重试仍缺数字时，保留已
                    # 译好的中文正文，仅把缺失数字按原值补进去——一整段
                    # 看不懂的外文比一个数字缺失严重得多。仅当译文根本
                    # 没有中文（完全没翻）才回退原文。
                    patched = patch_missing_numbers(
                        normalized, out,
                        skip=missing_model_tokens(normalized, out))
                    if patched and patched != out:
                        self.detail("  数字保真重试仍缺（%s），已补数字保留译文：%s"
                                    % (",".join(still[:4]), str(text)[:50]))
                        self._rule_hit("数字保真", "fixed")
                        out = patched
                    else:
                        self.detail("  数字保真重试仍缺失（%s），保留原文：%s"
                                    % (",".join(still[:4]), str(text)[:50]))
                        self._rule_hit("数字保真", "kept_source")
                        self.failed_strings += 1
                        return text
        # 粗口保真闸门：原文有脏话/辱骂/威胁，译文却完全软化时，带
        # “保留同等力度”的补充要求重试一次；仍软化则保留第一次结果。
        if has_source_profanity(normalized) and not has_zh_profanity(out):
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=_PROFANITY_RETRY_NOTE)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=_PROFANITY_RETRY_NOTE)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry, normalized) \
                    and has_zh_profanity(retry):
                self.detail("  粗口被软化，已按原文力度重试：%s"
                            % str(text)[:50])
                self._rule_hit("粗口力度", "retried_ok")
                out = retry
            else:
                self._rule_hit("粗口力度", "kept_first")
        # 完整性闸门：源文有可译内容，模型却原样回显/输出英文时重试一次。
        # 不处理纯型号（FX / ППШ-41）和无空格缩写，避免无意义请求。
        if (not has_cjk(out) and not has_cjk(text)
                and (re.search(r"[a-zа-яё]{2,}", text)
                     or " " in text.strip())):
            note = ("必须译成简体中文，不要保留英文或俄文原文；"
                    "人名音译，物品/任务按上下文意译。")
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=note)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=note)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry, normalized) \
                    and has_cjk(retry):
                self.detail("  译文无中文，已按完整性要求重试：%s"
                            % str(text)[:50])
                self._rule_hit("完整性（无中文）", "retried_ok")
                out = retry
            else:
                # ★ 2.0.13 第三级兜底：整段回显时强制切成短块逐块译。
                # 旧版到这里直接 kept_first，长英文句（如 dm_anomalies_13
                # 341 字符）就整段不翻。只有切片也失败才保留原文。
                sliced = None
                try:
                    if mapping:
                        raw = self._force_sliced(masked, lang, note=note)
                        sliced = restore_runtime_tokens(raw, mapping) \
                            if raw is not None else None
                    else:
                        sliced = self._force_sliced(normalized, lang,
                                                    note=note)
                except Exception:
                    sliced = None
                if sliced and not looks_like_prompt_echo(sliced, normalized) \
                        and has_cjk(sliced):
                    self.detail("  译文无中文，切片强制翻译成功：%s"
                                % str(text)[:50])
                    self._rule_hit("完整性（无中文）", "sliced_ok")
                    out = sliced
                elif len(normalized.strip()) <= 48:
                    # ★ 2.0.24（IWP 全量审计 D5）：短名回显第 4 级兜底。
                    # 成就名/绰号/军衔名（Rag and Bone、Lt-Colonel Shulga）
                    # 对「翻译这句话」类指令持续回显（实测 187 条）——改发
                    # 「定名」指令（要求给出中文译名，而非翻译整句）再试
                    # 一次；仍回显才保留原文。通用规则，不依赖具体 MOD。
                    _name_note = ("输入是游戏内的名称（成就、人物、物品、"
                                  "地点、组织）。为该名称给出简洁的中文译名"
                                  "（音译或意译均可），不要解释，不要原样"
                                  "保留英文或俄文。")
                    nm = None
                    try:
                        if mapping:
                            raw = self._translate_plain(masked, lang,
                                                         note=_name_note)
                            nm = restore_runtime_tokens(raw, mapping)
                        else:
                            nm = self._translate_plain(normalized, lang,
                                                       note=_name_note)
                    except Exception:
                        nm = None
                    if (nm and not looks_like_prompt_echo(nm, normalized)
                            and has_cjk(nm)
                            and nm.strip() != normalized.strip()):
                        self.detail("  短名回显，已按定名指令给出译名：%s"
                                    % str(text)[:50])
                        self._rule_hit("完整性（无中文）", "named_ok")
                        out = nm
                    else:
                        self._rule_hit("完整性（无中文）", "kept_first")
                else:
                    self._rule_hit("完整性（无中文）", "kept_first")
        # 残留俄文闸门：中文译文里还夹着西里尔词，通常是引号内的格变专名
        # 被模型当成不可译名原样保留。带“引号内也要译”要求重试一次；
        # 仍残留则保留首次译文，不强行音译未知专名。
        residual = _CYR_WORD_RE.findall(out)
        if has_cjk(out) and residual:
            note = _RESIDUAL_CYRILLIC_NOTE % "、".join(
                list(dict.fromkeys(residual))[:3])
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=note)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=note)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry, normalized) \
                    and has_cjk(retry) and not _CYR_WORD_RE.search(retry):
                self.detail("  译文残留俄文，已按引号内也译重试：%s"
                            % str(text)[:50])
                self._rule_hit("残留源文", "retried_ok")
                out = retry
            else:
                self._rule_hit("残留源文", "kept_first")
        # ★ 2.0.13 确定性后修复（零模型请求）：圆点注入 + 冠词粘连。
        # 这些是模型的格式噪声，用源文比对即可确定性清除，不必重译整段。
        cleaned, bullet_fixed = clean_injected_bullets(normalized, out)
        if bullet_fixed:
            self.detail("  清除模型注入圆点：%s" % str(text)[:50])
            self._rule_hit("圆点注入", "fixed")
            out = cleaned
        cleaned2, article_fixed = clean_stuck_article(out)
        if article_fixed:
            self.detail("  剥离粘连冠词：%s" % str(text)[:50])
            self._rule_hit("冠词粘连", "fixed")
            out = cleaned2
        # ★ 2.0.14 确定性后修复（零模型请求）：标点规范化 + 型号/年份保真。
        if has_cjk(out):
            out, punct_fixed = normalize_cn_punctuation(out)
            if punct_fixed:
                self._rule_hit("标点规范", "fixed")
        # 型号被模型换成别的（FNX-45→P220）。★ 2.0.24（IWP 全量审计
        # D2/D3）：旧实现只补「（原文型号 …）」注记——错误内容原样留在
        # 成品里还污染玩家阅读。现在先带「型号逐字保留」要求定向重试
        # 一次，仍失败才句末追加裸型号（无「原文」字样）。
        miss_model = missing_model_tokens(normalized, out)
        if miss_model and has_cjk(out):
            _model_note = ("武器/物品型号（如 AK-100、FNX-45）必须原样"
                           "保留，不得替换为其他型号、省略或换算。")
            retried = None
            try:
                if mapping:
                    raw = self._translate_plain(masked, lang,
                                                note=_model_note)
                    retried = restore_runtime_tokens(raw, mapping)
                else:
                    retried = self._translate_plain(normalized, lang,
                                                    note=_model_note)
            except Exception:
                retried = None
            if (retried and not looks_like_prompt_echo(retried, normalized)
                    and has_cjk(retried)
                    and not missing_model_tokens(normalized, retried)):
                retried, _ = normalize_cn_punctuation(retried)
                self.detail("  型号保真重试成功：%s" % str(text)[:50])
                self._rule_hit("型号保真", "retried_ok")
                out = retried
            else:
                repaired_model = repair_model_tokens(normalized, out)
                still_missing = missing_model_tokens(normalized, repaired_model)
                if still_missing:
                    repaired_model = repaired_model.rstrip() + "（%s）" % \
                        "、".join(still_missing[:3])
                self._rule_hit("型号保真", "fixed")
                self.detail("  型号被替换且重试仍失败，已按源型号修复：%s"
                            % "、".join(miss_model[:3]))
                out = repaired_model
        # ★ 2.0.24（IWP 全量审计 D10）：半音译粘连闸门。模型把专名音译
        # 一半、原文留一半（「克里门enko队长」「Tkachenko将军」
        # 「$clr_2We正看到」），词库回填要求整词边界、残留西里尔闸门不认
        # 拉丁碎片，这类残词此前完全无人拦。带「整词音译」指令重试一次；
        # 仍粘连则保留现译文只记规则命中——碎片句仍比整段原文可读，
        # 且已在终检点名清单里供人工复核。
        if _to_zh and has_cjk(out):
            glue = find_glue_residue(out)
            if glue:
                _glue_note = ("译文里出现了音译一半、原文留一半的粘连词"
                              "（如「克里门enko」）。人名、绰号、术语要么"
                              "整体音译成中文，要么整词保留原文并加空格，"
                              "禁止字母直接贴着汉字。")
                retried = None
                try:
                    if mapping:
                        raw = self._translate_plain(masked, lang,
                                                    note=_glue_note)
                        retried = restore_runtime_tokens(raw, mapping)
                    else:
                        retried = self._translate_plain(normalized, lang,
                                                        note=_glue_note)
                except Exception:
                    retried = None
                if (retried and not looks_like_prompt_echo(retried, normalized)
                        and has_cjk(retried)
                        and not find_glue_residue(retried)):
                    self.detail("  粘连残词（%s），已按整词音译重试：%s"
                                % ("、".join(glue), str(text)[:50]))
                    self._rule_hit("粘连残词", "retried_ok")
                    out = retried
                else:
                    self._rule_hit("粘连残词", "kept_first")
                    self.detail("  粘连残词重试仍失败：%s | %s"
                                % ("、".join(glue), str(text)[:50]))
        # ★ 2.0.18 出口级换行对齐：换行闸门在流程前段，其后数字/完整性/
        # 残留闸门的重试可能再次破坏换行（实测 marsh 背景段 4→2 行）。
        # 在写回前做最后一次对齐，保证任何闸门都破坏不了换行结构。
        if source_nl and out.count("\n") != source_nl:
            realigned = restore_linebreaks(out, source_nl)
            if realigned != out:
                self.detail("  出口换行对齐（%d→%d）：%s"
                            % (out.count("\n"), source_nl, str(text)[:50]))
                self._rule_hit("出口换行对齐", "fixed")
                out = realigned
        final = escape_newlines(self._enforce_preferred_terms(normalized, out))
        # C. 字面 \n 保真：源有字面换行而译文吞成空格/多加时，按源数量对齐。
        final, nl_fixed = reconcile_literal_newlines(text, final)
        if nl_fixed:
            self._rule_hit("字面换行保真", "fixed")
            self.detail("  字面换行数不符，已按原文对齐：%s"
                        % str(text)[:50])
        if looks_like_context_bleed(normalized, unescape_newlines(final)):
            self.failed_strings += 1
            self._rule_hit("重试结果串段", "blocked")
            self.detail("  重试结果仍混入其它段落，保留原文：%s"
                        % str(text)[:50])
            final = text
        elif not runtime_tokens_match(text, final):
            self.failed_strings += 1
            self._rule_hit("运行期标记计数不一致", "blocked")
            self.detail("  运行期标记校验失败，保留原文：%s" % str(text)[:50])
            final = text
        else:
            role_issues = semantic_role_issues(text, final, lang)
            if role_issues:
                self.failed_strings += 1
                self._rule_hit("语义方向冲突", "blocked")
                self.detail("  语义方向校验失败（%s），保留原文：%s"
                            % ("、".join(role_issues), str(text)[:50]))
                final = text
        self._tm_store(normalized, lang, final)
        if self.on_translation is not None and final != text:
            try:
                self.on_translation(text, final)
            except Exception:
                pass
        return final

    def _force_sliced(self, text, lang, note=""):
        """★ 2.0.13 未翻译兜底：把整段强制切成短块逐块翻译。

        完整性闸门重试仍无中文时说明模型把整段英文原样回显——整段越长越
        容易回显。这里无视 safe_chars 强行按句切成 ≤100 字符的小块，每块
        单独请求（短句翻译成功率高得多），拼回。占位符用 split_protected
        保护不被切断。任一块仍是纯英文则整条判失败（返回 None），交回原文。
        """
        if not text or not text.strip():
            return None
        parts = split_protected(text, 100, _PLACEHOLDER_RE)
        if len(parts) <= 1 and len(text) <= 100:
            # 本来就是短句还回显，切片也帮不上，直接失败
            return None
        pieces = []
        for seg in parts:
            try:
                got = self._translate_segments(seg, lang, note=note)
            except Exception:
                return None
            if not got or not has_cjk(got):
                return None      # 有一块没译出，整条不算成功
            pieces.append(got)
        return "".join(pieces) if pieces else None

    def _translate_plain(self, text, lang, note=""):
        """翻译一个文本串；强制术语模式下整体翻译后回填指定译名。"""
        if lang == self.tgt:
            return text
        if not self.glossary:
            return self._translate_segments(text, lang, note=note)
        if self.force_glossary and self.gloss is not None:
            return self._translate_force(text, lang, note=note)
        return self._translate_segments(text, lang, note=note)

    def _translate_sliced(self, text, lang, note=""):
        """旧的强制术语做法：把术语之间的片段**各自**送翻，术语直接拼接。

        保留是为了兜底（占位符法校验不过时回退到这里）。实测这条路径在
        一句含多个术语时句子质量差：片段之间没有上下文，会产出
        「去那里西德洛维奇，拿去吧战利品，然后——神器并且进入区域。」这种
        拼接感很强的译文，引号与标点也会被打散。
        """
        out = []
        last = 0
        for start, end, key in self._glossary_spans(text):
            if start > last:
                out.append(self._translate_segments(
                    text[last:start], lang, note=note))
            out.append(self._inline_term_value(
                text, start, end, self.glossary[key]))
            last = end
        if last < len(text):
            out.append(self._translate_segments(
                text[last:], lang, note=note))
        return "".join(out) if out else text

    def _translate_force(self, text, lang, note=""):
        """强制术语：先把术语换成占位符，**整串一次翻译**，再把占位符回填成
        词库指定译名。

        为什么不用旧做法：旧做法按术语切片，术语之间的每段各自翻译 —— 一句里
        出现 3 个术语就是 4 次请求，片段之间没有上下文，译文会有拼接感、标点与
        引号被打散（用户实测「非常差」）。改成占位符后整串只翻一次，语序、
        标点、语气完整；占位符由系统提示词第 1 条要求逐字保留（两档模型实测
        都能守住），最后回填，术语仍然是 100% 确定。
        校验不过（占位符丢失/控制符被改）时自动回退旧做法，不会产出坏译文。
        """
        hits = self._glossary_spans(text)
        if not hits:
            # 无强制术语时仍要透传 note：数字保真/上下文/占位符/换行等
            # 闸门的重试指令全靠它，漏传会让重试永远无效、整条保留原文。
            return self._translate_segments(text, lang, note=note)
        # ★ 2.0.25（aera 全量审计 D12 签名①）：超长多术语条目不走 $$T$$
        # 占位符法。实测百科长文（800+ 字符、多处色码/换行）里 1.8B 连续
        # 破坏 $$T1$$，两次重试失败 → 切片兜底的片段占位符也失败 → 整条
        # 保留原文（15 条百科条目全漏）。长文改走普通整译，术语交译后
        # 词库回填（_repair_residual_source_terms/_latin_names 实测有效）。
        if len(text) >= 600:
            self.detail("  长文多术语，普通整译并注入术语提示：%s"
                        % str(text)[:50])
            return self._translate_chunk(
                text, lang, self._glossary_hits(text), note=note)

        mapping = {}
        parts = []
        last = 0
        for i, (start, end, key) in enumerate(hits, 1):
            ph = "$$T%d$$" % i
            mapping[ph] = self._inline_term_value(
                text, start, end, self.glossary[key])
            parts.append(text[last:start])
            parts.append(ph)
            last = end
        parts.append(text[last:])
        masked = "".join(parts)

        hard = [pc["content"] for pc in self._cut_text(text)
                if not pc["needTrans"] and pc["content"].strip()]

        try:
            out = self._translate_masked(masked, lang, note=note)
        except Exception as e:
            self.detail("  占位符整译失败（%s: %s），回退到切片方式"
                        % (type(e).__name__, e))
            return self._translate_sliced(text, lang, note=note)

        bad = [ph for ph in mapping if out.count(ph) != 1]
        if bad:
            self.detail("  占位符未原样保留（%s），回退到切片方式"
                        % "、".join(bad[:3]))
            return self._translate_sliced(text, lang, note=note)
        lost = [h for h in hard if h not in out]
        if lost:
            self.detail("  控制符被改动（%s），回退到切片方式"
                        % "、".join(repr(h) for h in lost[:3]))
            return self._translate_sliced(text, lang, note=note)

        for ph, val in mapping.items():
            out = out.replace(ph, val)
        return out

    def _translate_masked(self, masked, lang, note=""):
        """把已占位符化的整串交给模型翻一次（超长时按句切分，不丢占位符）。"""
        with self._stat_lock:
            self.req_count += 1
            self.trans_chars += len(masked)
        if len(masked) <= self.safe_chars:
            return self._translate_text(
                masked, lang, extra_sys=note or None,
                context=getattr(self, "_ctx_hint", "") or None,
                style=getattr(self, "_style_hint", "dialogue"),
                temperature=self._temp_for_style())
        parts = split_protected(masked, self.safe_chars, _PLACEHOLDER_RE)
        with self._stat_lock:
            self.long_chunks += max(0, len(parts) - 1)
        self.log("  长文本 %d 字符 → 自动切为 %d 段翻译（%s 上下文窗）"
                 % (len(masked), len(parts), self.ctx_size))
        return "".join(self._translate_masked(seg, lang, note)
                       for seg in parts)

    def _translate_dialogue_batch(self, batch):
        """批量翻译 2～4 条连续短对话；失败返回 {}，交由逐条路径。"""
        self._tls.translation_origin = "model_batch"
        self._tls.rule_results = []
        # ★ 2.0.24（IWP 全量审计 D1）：批量路径与单条路径同等保护——
        # 送翻前 mask 运行期占位符（%c[default]、$clr、%s…），译后逐行
        # 还原并校验；任何不一致整批丢弃交回单条路径（那里有完整闸门）。
        # 旧实现直接送原文，实测 16 条 %c[default]→%c[默认]、$clr_2REMOVE
        # 被拆译、色码元组内插换行——引擎按未知色名/坏变量渲染，运行期破坏。
        lines = []
        mappings = []
        for x in batch:
            masked, mapping = mask_runtime_tokens(x["src"])
            lines.append(masked)
            mappings.append(mapping)
        pairs = []
        seen = set()
        for x in batch:
            for k, v in self._glossary_hits(x["src"]):
                if k not in seen:
                    seen.add(k)
                    pairs.append((k, v))
        with self._stat_lock:
            self.batch_stats["batches"] += 1
            self.req_count += 1
            self.trans_chars += sum(len(x["src"]) for x in batch)
        try:
            out = self._translate_batch_text(
                lines, batch[0]["lang"],
                glossary_hits=pairs or None,
                temperature=self.dialogue_temp)
        except Exception:
            self.batch_stats["failed"] += 1
            return {}
        parsed = self._parse_numbered_batch(out, len(batch))
        if not parsed:
            self.batch_stats["failed"] += 1
            return {}
        # ★ 2.0.24（IWP 全量审计 D11）：批处理错位签名。模型把上一行的
        # 译文复制/顺延到下一行（实测 agr_technic_dialogs_737 拿到 735
        # 源文的另一种译法、本条一字未译）——行数一致、长度比 0.23 也
        # 高于闸门下限，唯一可靠信号是「不同源文、完全相同译文」。
        # 命中任何一对即整批丢弃，交单条路径逐条重翻。
        # 短应答豁免：「是的。/好的。」类问候语不同源文同译是正常收敛，
        # 只有 ≥30 字的整句译文重复才可能是错位（实测错位样本 ~90 字）。
        for _i in range(len(parsed)):
            for _j in range(_i + 1, len(parsed)):
                if (len(parsed[_i].strip()) >= 30
                        and parsed[_i].strip() == parsed[_j].strip()
                        and batch[_i]["src"].strip()
                        != batch[_j]["src"].strip()):
                    self.batch_stats["failed"] += 1
                    return {}
        result = {}
        for x, mapping, text in zip(batch, mappings, parsed):
            text = clean_translation_output(text)
            if mapping:
                text = restore_runtime_tokens(text, mapping)
                if text is None:
                    # 掩码丢失/重复/被改动：整批不可信，回退单条路径
                    self.batch_stats["failed"] += 1
                    return {}
            if not runtime_tokens_match(x["src"], text):
                self.batch_stats["failed"] += 1
                return {}
            if text.count("\\n") != x["src"].count("\\n"):
                self.batch_stats["failed"] += 1
                return {}
            if (self.tgt == "chs"
                    and missing_big_numbers(x["src"], text)):
                self.batch_stats["failed"] += 1
                return {}
            if self.tgt == "chs" and find_glue_residue(text):
                # 粘连残词交单条路径重试（批量里无法定向重试）
                self.batch_stats["failed"] += 1
                return {}
            text = self._repair_residual_source_terms(x["src"], text)
            text = self._repair_residual_latin_names(x["src"], text)
            text = self._enforce_preferred_terms(x["src"], text)
            xlang = x["lang"]
            if xlang in ("auto", "text", "rus", "eng"):
                det = detect_text_lang(x["src"])
                if det:
                    xlang = det
            text = self._repair_known_semantic_terms(
                x["src"], text, xlang)
            if self._entry_style(x["rel"], x["src"]) == "dialogue":
                text = style_cleanup_cn(text)
            if (not text or not has_cjk(text)
                    or text.strip() == x["src"].strip()
                    or looks_like_prompt_echo(text, x["src"])
                    or (has_source_profanity(x["src"])
                        and not has_zh_profanity(text))):
                self.batch_stats["failed"] += 1
                return {}
            if semantic_role_issues(x["src"], text, xlang):
                self.batch_stats["failed"] += 1
                return {}
            lead = x["src"][:len(x["src"]) - len(x["src"].lstrip())]
            trail = x["src"][len(x["src"].rstrip()):]
            result[x["id"]] = (x["lang"], lead + text.strip() + trail)
        self.batch_stats["lines"] += len(batch)
        return result
