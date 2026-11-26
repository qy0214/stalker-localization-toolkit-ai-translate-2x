# -*- coding: utf-8 -*-
"""共享文本表编排：筛选、批量、单条翻译与编码回写。"""
from __future__ import annotations

import re

from .pipeline import (
    _RUNTIME_TOKEN_RE,
    _has_translatable_text,
    _id_player_visible,
    _is_model_code_form,
    clean_translation_output,
    detect_text_lang,
    is_player_facing_label,
    looks_like_code_or_debug,
    looks_like_generated_noise,
    looks_like_hard_protected_file,
    looks_like_identifier_text,
    looks_like_proper_text,
    looks_like_technical_text,
)
from .text import pattern_translate
from .xray import read_encoding_chain


class TablePipeline:
    def _translate_table(self, full, out_path, rel):
        st = {"translated": 0, "skipped": 0, "failed": 0,
              "changed": False, "copied": False}
        try:
            ents = self._parse(self.p.parse_xray_text_xml, full,
                               read_encoding_chain(full))
        except Exception as e:
            st["failed"] += 1
            self.detail("  文本表解析失败：%s（%s）" % (rel, e))
            return st
        if not ents:
            self._discard_unchanged_output(rel, out_path)
            return st
        # 多路并发时表级 TM 的 _tm_local_enabled/_tm_cache 是实例状态，
        # 会被其他 worker 覆盖；只保留任务级全局 TM（在并发前已算好）。
        if self.tm_lite and not self._tm_global_enabled and self.parallel == 1:
            tm_values = []
            for ent in ents:
                _lang, src = self._pick_source(ent)
                if not src or not src.strip():
                    continue
                if self._looks_like_target(src) or not _has_translatable_text(src):
                    continue
                if (looks_like_code_or_debug(rel, src)
                        or looks_like_generated_noise(rel, src)
                        or looks_like_technical_text(src)
                        or (looks_like_identifier_text(src)
                            and not is_player_facing_label(rel, src))):
                    continue
                if self.protect_proper and looks_like_proper_text(rel, src):
                    continue
                tm_values.append(src)
            self._tm_enable_table(tm_values, rel)
        changes = {}
        metas = []
        for ent in ents:
            lang0, src0 = self._pick_source(ent)
            metas.append({
                "id": ent.id, "ent": ent, "lang": lang0, "src": src0,
                "rel": rel, "style": self._entry_style(rel, src0 or ""),
            })
        st["total"] = len(metas)
        batched_ids = set()
        # ★ 2.0.13：批量不再因 force_glossary 整体关闭。原实现里只要开着
        # 强制术语，dialog 表就全走逐条——实测请求数翻倍是速度主因之一。
        # 术语安全由下面 _ok() 保证：含 hard 术语/占位符/词库整条命中的条目
        # 一律踢出批次走逐条，批量只处理「无硬术语的连续短对话」。
        if "dialog" in rel.lower():
            def _ok(meta):
                src0 = meta["src"]
                if not src0 or not src0.strip():
                    return False
                if len(src0) < 4 or len(src0) > 220 or chr(10) in src0:
                    return False
                if _RUNTIME_TOKEN_RE.search(src0):
                    return False
                # 含 hard 术语的句子不进批量：批量只用提示，容易丢术语；
                # 交给逐条路径（占位符/校验更完整）。
                if (self.gloss_hard is not None
                       and any(k in self.gloss_hard
                               for k, _v in self._glossary_hits(src0))):
                    return False
                if self._looks_like_target(src0) or not _has_translatable_text(src0):
                    return False
                if src0.strip() in self.glossary:
                    return False
                if (looks_like_code_or_debug(rel, src0)
                        or looks_like_generated_noise(rel, src0)
                        or looks_like_technical_text(src0)
                        or (looks_like_identifier_text(src0)
                            and not is_player_facing_label(rel, src0)
                            and not _id_player_visible(meta["id"]))):
                    return False
                if self.only_referenced and meta["id"] not in self._ref_counts:
                    return False
                # ★ 2.0.7：术语库整条命中的条目必须进批次（写入点直接取译名）
                if src0 not in self.glossary \
                        and self._termbase_lookup(src0) is not None:
                    return True
                if self.protect_proper and looks_like_proper_text(rel, src0):
                    return False
                return True
            i = 0
            while i < len(metas):
                if not _ok(metas[i]):
                    i += 1
                    continue
                batch = [metas[i]]
                total = len(metas[i]["src"])
                j = i + 1
                while (j < len(metas) and len(batch) < 4
                       and _ok(metas[j])
                       and metas[j]["lang"] == metas[i]["lang"]
                       and total + len(metas[j]["src"]) <= 700):
                    batch.append(metas[j])
                    total += len(metas[j]["src"])
                    j += 1
                if len(batch) >= 2:
                    got = self._translate_dialogue_batch(batch)
                    if got:
                        changes.update(got)
                        batched_ids.update(got)
                        st["translated"] += len(got)
                        i = j
                        continue
                i += 1
        for meta_idx, meta in enumerate(metas):
            ent = meta["ent"]
            if ent.id in batched_ids:
                continue
            if self._cancelled():
                break
            lang, src = meta["lang"], meta["src"]
            if not src or not src.strip():
                continue
            action, value, reason = self._filter_decision(rel, ent.id, src, lang)
            self._record_filter_decision(rel, ent.id, action, reason)
            if action == "keep":
                continue
            if action == "skip":
                st["skipped"] += 1
                continue
            if action == "exact":
                if value != src:
                    changes[ent.id] = (lang, value)
                    st["translated"] += 1
                continue
            use_lang = self.src_lang if self.src_lang != "auto" else lang
            # 字段名不可靠：不少 MOD 把英语放进 <rus>，或把俄语放进 <text>。
            # 即使显式选了 rus/eng，正文明显是另一种语言时也按正文判断；
            # ukr 不覆盖（俄/乌都是西里尔，粗判分不出来）。
            if use_lang != "ukr":
                det = detect_text_lang(src)
                if det and det != use_lang:
                    use_lang = det
            ctx = ""
            if meta["style"] == "dialogue" and "dialog" in rel.lower():
                ctx = self._scene_context(metas, meta_idx)
            try:
                out = self._translate_one(
                    src, use_lang, context=ctx, style=meta["style"])
            except Exception as e:
                st["failed"] += 1
                self.failed_strings += 1
                self.detail("  条目翻译失败，保留原文：%s（%s: %s）"
                            % (ent.id, type(e).__name__, e))
                continue
            if not out or not out.strip():
                self.failed_strings += 1
                continue
            out = clean_translation_output(out)
            lead = src[:len(src) - len(src.lstrip())]
            trail = src[len(src.rstrip()):]
            new = lead + out.strip() + trail
            if new == src:
                self.failed_strings += 1
                continue
            changes[ent.id] = (lang, new)
            st["translated"] += 1
        if not changes:
            self._discard_unchanged_output(rel, out_path)
            return st
        try:
            with open(full, "rb") as handle:
                handle.read().decode("utf-8-sig")
            utf8_table = True
        except (OSError, UnicodeDecodeError):
            utf8_table = False
        if utf8_table:
            wrote = self._write_table_changes(full, out_path, changes, rel)
        else:
            # 老 MOD 常见 cp1251 表；中文无法用 cp1251 写回，转成 UTF-8
            # 并重写声明。会丢注释/多语言标签，但不会漏译。
            wrote = self._write_table_as_utf8(full, out_path, ents,
                                             changes, rel)
        if wrote:
            issues = self._verify_table_roundtrip(full, out_path, rel)
            if issues:
                wrote = False
                for issue in issues:
                    self.log("  回写校验失败：%s" % issue, "err")
        if wrote:
            st["changed"] = True
            self._audit_tables.add(rel)   # ★ 2.0.2：登记进终检清单
            source_by_id = {meta["ent"].id: meta["src"] for meta in metas}
            for eid, (_lang, new_text) in changes.items():
                reason = self._filter_reason.get((rel, eid), "")
                origin = "model_batch" if eid in batched_ids else (
                    "deterministic" if reason.endswith("exact") or reason == "proper_partial"
                    else "model")
                self._record_provenance(rel, eid, source_by_id.get(eid, ""),
                                        new_text, origin)
            self._record_done(rel, full, out_path)
        else:
            st["failed"] += 1
            self._ledger.pop(rel.replace("\\", "/"), None)
            self.detail("  文本表写入失败，未记录完成：%s" % rel)
        return st


    def _write_table_as_utf8(self, full, out_path, ents, changes, rel):
        """非 UTF-8 文本表：保持 <rus>/<eng>/<tur> 结构转 UTF-8。

        不能走 generateOutputXml：它只写 <text>，会把原文的 <rus>/<eng>
        标签全部丢掉（实测这个 MOD 的 stable_dialogs 从 52 KB 缩到 24 KB，
        引擎按 rus 读时就可能读不到中文）。这里逐条改 <rus> 内容，
        只把 XML 声明的编码换成 utf-8。
        """
        try:
            with open(full, "rb") as handle:
                raw = handle.read()
        except OSError:
            return False
        text = None
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", "replace")
        applied = missed = 0
        for eid, (lang, new_text) in changes.items():
            text, ok = self._patch_table_text(text, eid, lang, new_text)
            if ok:
                applied += 1
            else:
                missed += 1
        if applied == 0:
            self.log("文本表条目定位失败，未写出译文：%s" % rel, "warn")
            return False
        if missed:
            self.detail("  %s：%d 条已改写，%d 条定位失败（保留原文）"
                        % (rel, applied, missed))
        text = re.sub(
            r'(<\?xml[^>]*?\bencoding\s*=\s*["\'])[^"\']+(["\'])',
            r"\1utf-8\2", text, count=1, flags=re.I)
        if not text.lstrip().startswith("<?xml"):
            text = '<?xml version="1.0" encoding="utf-8"?>\n' + text
        data = (b"\xef\xbb\xbf" if had_bom else b"") + text.encode("utf-8")
        def write_bytes(path):
            with open(path, "wb") as stream:
                stream.write(data)

        try:
            self._write_atomic(out_path, write_bytes)
        except OSError as e:
            self.log("文本表写入失败，未写出译文：%s（%s）" % (rel, e), "err")
            return False
        self.log("文本表转 UTF-8 并保留语言标签：%s" % rel, "warn")
        return True


    def _write_table_changes(self, full, out_path, changes, rel):
        try:
            with open(full, "rb") as handle:
                raw = handle.read()
        except OSError:
            return False
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            self.log("非 UTF-8 文本表不能原地改写：%s" % rel, "warn")
            return False
        applied = missed = 0
        for eid, (lang, new_text) in changes.items():
            text, ok = self._patch_table_text(text, eid, lang, new_text)
            if ok:
                applied += 1
            else:
                missed += 1
        if applied == 0:
            self.log("文本表条目定位失败，未写出译文：%s" % rel, "warn")
            return False
        if missed:
            self.detail("  %s：%d 条已改写，%d 条定位失败（保留原文）"
                        % (rel, applied, missed))
        # ★ 2.0.11：UTF-8 表原地改写时同步把 XML 声明的 encoding 改成 utf-8。
        # 旧逻辑只写字节不改声明：源码声明 windows-1251、内容却是 UTF-8 中文，
        # 引擎若按声明解码就会乱码（实测 IWP 输出 5/5 张表声明不符）。
        text = re.sub(
            r'(<\?xml[^>]*?\bencoding\s*=\s*["\'])[^"\']+(["\'])',
            r"\1utf-8\2", text, count=1, flags=re.I)
        if not text.lstrip().startswith("<?xml"):
            text = '<?xml version="1.0" encoding="utf-8"?>\n' + text
        data = (b"\xef\xbb\xbf" if had_bom else b"") + text.encode("utf-8")
        def write_bytes(path):
            with open(path, "wb") as stream:
                stream.write(data)

        try:
            self._write_atomic(out_path, write_bytes)
        except OSError as e:
            self.log("文本表写入失败，未写出译文：%s（%s）" % (rel, e), "err")
            return False
        return True

