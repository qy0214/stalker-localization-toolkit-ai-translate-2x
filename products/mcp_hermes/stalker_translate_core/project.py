# -*- coding: utf-8 -*-
"""共享项目编排：引用层、字面层、文件遍历、生成 ID 与回写。"""
from __future__ import annotations

import csv
import os
import re
import shutil
import time

from .assets import sha256_file
from .pipeline import (
    _RUNTIME_TOKEN_RE,
    _has_translatable_text,
    _id_player_visible,
    detect_text_lang,
    is_player_facing_label,
    looks_like_code_or_debug,
    looks_like_generated_noise,
    looks_like_hard_protected_file,
    looks_like_identifier_text,
    looks_like_proper_text,
    looks_like_technical_text,
    unescape_newlines,
)
from .script import (
    _SCRIPT_QUOTED_RE,
    _SCRIPT_TRANSLATE_RE,
    _XML_REF_ATTR_RE,
    _XML_REF_TAG_RE,
    extra_script_display_literals,
    script_literal_allowed_in_file,
    script_translate_literal_args,
)
from .text import pattern_translate
from .xray import (
    _ENC_UTF8,
    classify_xray_file,
    files_equal,
    looks_like_mojibake,
    read_encoding_chain,
    read_text_any,
)


class ProjectPipeline:
    def _fallback_done_dir(self):
        """输入目录不可写（Program Files/只读盘/游戏目录受保护）时，
        把译文落到本机可变数据目录下，按输入路径派生唯一名。"""
        safe = re.sub(r'[<>:"/\\|?*]+', "_",
                      os.path.abspath(self.in_dir)).strip("_")
        return os.path.join(self._data_root(), "translated_output", safe[-120:])


    def _resolve_done_dir(self, preferred):
        """确定实际输出目录：首选可写就用；不可写自动回退并明确告知。"""
        try:
            if self._is_writable(preferred):
                return preferred
        except Exception:
            pass
        fb = self._fallback_done_dir()
        os.makedirs(fb, exist_ok=True)
        self.log("输出目录不可写（%s），译文改存到：%s"
                 % (preferred, fb), "warn")
        return fb


    def _read_localization(self):
        info = {"language": "", "files": []}
        path = os.path.join(self.in_dir, "configs", "localization.ltx")
        if not os.path.isfile(path):
            return info
        for line in read_text_any(path).splitlines():
            line = line.strip()
            if not line or line.startswith(";") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            if key == "language":
                info["language"] = value.strip().lower()
            elif key == "files":
                info["files"] = [x.strip() for x in value.split(",")
                                 if x.strip()]
        return info


    def _is_excluded(self, path):
        ap = os.path.normcase(os.path.abspath(path))
        return any(ap == ex or ap.startswith(ex + os.sep)
                   for ex in self._excluded_paths)


    def _iter_text_tables(self):
        root = os.path.join(self.in_dir, "configs", "text")
        out = []
        if not os.path.isdir(root):
            return out
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs
                       if not self._is_excluded(os.path.join(cur, d))]
            for fn in files:
                full = os.path.join(cur, fn)
                if fn.lower().endswith(".xml") and not self._is_excluded(full):
                    out.append(full)
        out.sort()
        return out


    def _build_text_index(self):
        self._table_entries = {}
        self._id_files = {}
        self._localization = self._read_localization()
        self._lang_hint = (self._localization.get("language") or "").lower()
        if not self._lang_hint:
            # 没写 localization.ltx 时，从 configs/text/<lang>/ 推断
            for full in self._iter_text_tables():
                parts = full.replace("\\", "/").lower().split("/configs/text/")
                if len(parts) == 2 and "/" in parts[1]:
                    self._lang_hint = parts[1].split("/")[0]
                    break
        for full in self._iter_text_tables():
            try:
                ents = self._parse(self.p.parse_xray_text_xml, full,
                                   read_encoding_chain(full))
            except Exception as e:
                self.detail("  文本表解析失败，跳过：%s（%s）"
                            % (os.path.basename(full), e))
                continue
            table = {}
            for ent in ents:
                if ent.id:
                    table[ent.id] = ent
            self._table_entries[full] = table
            for eid in table:
                self._id_files.setdefault(eid, []).append(full)
        self.log("文本表：%d 个文件 / %d 个 ID（语言 %s，注册表 %d 张）"
                 % (len(self._table_entries), len(self._id_files),
                    self._localization.get("language") or "未声明",
                    len(self._localization.get("files") or [])))


    def _scan_references(self):
        refs = {}
        ref_files = set()
        unresolved = {}
        text_root = os.path.abspath(
            os.path.join(self.in_dir, "configs", "text"))
        configs_root = os.path.join(self.in_dir, "configs")

        def add_ref(value, full, count_unresolved=True):
            v = (value or "").strip()
            if not v:
                return False
            if v in self._id_files:
                refs[v] = refs.get(v, 0) + 1
                ref_files.add(full)
                return True
            if count_unresolved and self._looks_like_ref_value(v):
                unresolved[v] = unresolved.get(v, 0) + 1
            return False

        for cur, dirs, files in os.walk(configs_root):
            ap = os.path.abspath(cur)
            if ap == text_root or ap.startswith(text_root + os.sep):
                continue
            if self._is_excluded(cur):
                dirs[:] = []
                continue
            for fn in files:
                if not fn.lower().endswith(".xml"):
                    continue
                full = os.path.join(cur, fn)
                text = read_text_any(full)
                for v in _XML_REF_TAG_RE.findall(text):
                    add_ref(v, full)
                for v in _XML_REF_ATTR_RE.findall(text):
                    add_ref(v, full)

        scripts_root = os.path.join(self.in_dir, "scripts")
        if os.path.isdir(scripts_root):
            for cur, dirs, files in os.walk(scripts_root):
                if self._is_excluded(cur):
                    dirs[:] = []
                    continue
                for fn in files:
                    if not fn.lower().endswith(".script"):
                        continue
                    full = os.path.join(cur, fn)
                    text = read_text_any(full)
                    ids = list(_SCRIPT_TRANSLATE_RE.findall(text))
                    for v in _SCRIPT_QUOTED_RE.findall(text):
                        if v in self._id_files and v not in ids:
                            ids.append(v)
                    for v in ids:
                        add_ref(v, full, count_unresolved=False)

        self._ref_counts = refs
        self._ref_files = ref_files
        self._unresolved = unresolved
        self.log("引用层：%d 个 XML/脚本，%d 条引用（%d 个不同 ID）；"
                 "未解析 %d 个"
                 % (len(ref_files), sum(refs.values()), len(refs),
                    len(unresolved)))
        if unresolved:
            top = sorted(unresolved.items(), key=lambda kv: -kv[1])[:20]
            self.log("未解析引用（保留原文，前 20）：%s"
                     % "、".join("%s×%d" % (k, v) for k, v in top), "warn")
        return refs, ref_files, unresolved


    def _pick_source(self, ent):
        """按游戏语言取源文：显式源语言 → localization → rus 优先。

        STALKER 系 MOD 的主语言基本是俄语。旧逻辑用 vendor 的
        getRecommendLangText 会优先 eng，把英语意译当原文（例如
        “all right”被翻成“所有 向右”、“rest”被翻成“休息”），
        俄文原文反而没被使用。这里默认 rus 优先，只有缺 rus 才用 eng。
        """
        texts = ent.texts or {}
        if not isinstance(texts, dict):
            return self.p.getRecommendLangText(ent, self.tgt)
        order = []
        if isinstance(self.src_lang, str) and self.src_lang != "auto":
            order.append(self.src_lang)
        if self._lang_hint:
            order.append(self._lang_hint)
        order += ["rus", "text", "eng", "ukr", "tur"]
        order += sorted(key for key in texts
                        if isinstance(key, str) and key not in ("chs", "zh")
                        and key not in order)
        seen = set()
        for lang in order:
            if lang in seen:
                continue
            seen.add(lang)
            value = texts.get(lang, "")
            if value and value.strip():
                return lang, value
        return self.p.getRecommendLangText(ent, self.tgt)


    def _reference_candidates(self):
        total = todo = protected = 0
        tm_values = []
        for full, table in self._table_entries.items():
            rel = os.path.relpath(full, self.in_dir)
            for ent in table.values():
                _lang, src = self._pick_source(ent)
                if not src or not src.strip():
                    continue
                if self._looks_like_target(src):
                    continue
                if not _has_translatable_text(src):
                    continue
                if looks_like_code_or_debug(rel, src):
                    continue
                total += 1
                tm_values.append(src)
                if self.only_referenced and ent.id not in self._ref_counts:
                    continue
                if self.protect_proper and looks_like_proper_text(rel, src):
                    protected += 1
                    continue
                todo += 1
        self._tm_enable_global(tm_values)
        return total, todo, protected


    def _run_reference_layer(self, done_dir, reuse):
        self._build_text_index()
        if not self._table_entries:
            self.log("没有找到 configs/text 文本表", "warn")
            return False
        self._scan_references()
        total, todo, protected = self._reference_candidates()
        self.log("引用层预览：可译条目 %d 条，其中待译 %d 条，保护专名 %d 条"
                 % (total, todo, protected))
        if self.preview_reference:
            self.log("预览完成（未写盘）。取消勾选「先预览」后再开始即执行。",
                     "ok")
            return True

        # 早期生成的根目录表没有注册进 localization.ltx，
        # 属于旧机制残留；引用层模式不再产出它，顺手清掉避免误部署。
        for stale in ("___auto_put_this_to_text_folder.xml",
                      "待确认_未解析引用.csv"):
            try:
                os.remove(os.path.join(done_dir, stale))
            except OSError:
                pass

        tables = sorted(self._table_entries)
        n_tables = len(tables)
        changed = copied = translated = skipped = failed = 0

        def _one(item):
            idx, full = item
            rel = os.path.relpath(full, self.in_dir)
            out_path = os.path.join(done_dir, rel)
            st = self._translate_table(full, out_path, rel)
            return idx, rel, st

        items = list(enumerate(tables, 1))
        done_n = 0
        trans_total = 0
        ent_total = todo
        trans_tables = 0

        def _report(rel):
            """每处理完一张文本表就回报一次：已处理张数 / 总张数 + 累计翻译量。"""
            self.progress({"file": rel, "file_idx": done_n, "files": n_tables,
                           "ids": trans_total, "total_ids": ent_total,
                           "entries_total": total,
                           "entries_translatable": ent_total,
                           "tabs": trans_tables, "pending": self.pending_files,
                           "reqs": self.req_count, "unit": "个文件"})

        if self.parallel > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            self.log("多路翻译：%d 路并发处理 %d 张文本表（每路 %d token）"
                     % (self.parallel, n_tables, self.ctx_size), "ok")
            results = []
            with ThreadPoolExecutor(max_workers=self.parallel) as pool:
                futures = [pool.submit(_one, item) for item in items]
                for fut in as_completed(futures):
                    if self._cancelled():
                        for f in futures:
                            f.cancel()
                        self.log("已取消，已处理的文本表保留在输出目录")
                        return False
                    idx, rel, st = fut.result()
                    done_n += 1
                    trans_total += st["translated"]
                    if st["translated"]:
                        trans_tables += 1
                    results.append((idx, rel, st))
                    _report(rel)
            results.sort(key=lambda x: x[0])
        else:
            results = []
            for item in items:
                if self._cancelled():
                    self.log("已取消，已处理的文本表保留在输出目录")
                    return False
                idx, rel, st = _one(item)
                done_n += 1
                trans_total += st["translated"]
                if st["translated"]:
                    trans_tables += 1
                results.append((idx, rel, st))
                _report(rel)

        for idx, rel, st in results:
            translated += st["translated"]
            skipped += st["skipped"]
            failed += st["failed"]
            changed += 1 if st["changed"] else 0
            copied += 1 if st["copied"] else 0
            if idx == 1 or idx == n_tables or idx % 20 == 0:
                self.log("文本表 %d/%d（%d%%）：%s"
                         % (idx, n_tables, 100 * idx // n_tables, rel))
        copied_refs = self._copy_reference_layer(done_dir)
        lit = {"files": 0, "entries": 0}
        if self.translate_literals:
            lit = self._run_literal_layer(done_dir)
        self.empty_files = sum(1 for t in self._table_entries.values()
                               if not t)
        self.log("引用层完成：改写文本表 %d 个，原样复制 %d 个；"
                 "复制引用/配置 %d 个；译 %d 条，跳过噪声 %d 条，失败 %d 条；"
                 "脚本/XML 字面文本：改写 %d 个文件 / %d 条"
                 % (changed, copied, copied_refs, translated, skipped, failed,
                    lit["files"], lit["entries"]), "ok")
        return (failed == 0 and self.failed_strings == 0
                and not self._cancelled())


    def _run_literal_layer(self, done_dir):
        """把脚本/XML 里**不是 ID** 的字面文本抽到新表并注册。

        文本表只解决“ID → 正文”这一层；脚本和 UI XML 里仍有直接写着的
        俄文/英文（补丁的 scripts.xml / ui.xml / gameplay.xml 就是这些）。
        不处理它们，游戏里就会看到俄文或 ID。这里复用旧的提取器：
        字面文本 → 新 ID，原文件只换 ASCII ID，新表 UTF-8。
        """
        prefix = "sgtat_gen_text_%d_" % time.time_ns()
        gen = [0]
        extract = {}
        written = 0
        total = 0
        targets = []
        for full in self._iter_files():
            rel = os.path.relpath(full, self.in_dir)
            kind = self._classify_file(full, rel)
            if kind in ("cfg", "script"):
                targets.append((kind, rel, full))
        targets.sort(key=lambda x: x[1])
        try:
            for kind, rel, full in targets:
                if self._cancelled():
                    self.log("已取消：字面文本层已处理的文件保留在输出目录")
                    break
                out_path = os.path.join(done_dir, rel)
                old_mode = self.mode
                self.mode = "cfgxml" if kind == "cfg" else "scriptE"
                file_extract = {}
                try:
                    rep = self._process_non_text(
                        full, {}, file_extract, prefix, gen)
                except Exception as e:
                    self.failed_strings += 1
                    self.detail("  字面文本提取失败：%s（%s: %s）"
                                % (rel, type(e).__name__, e))
                    rep = None
                finally:
                    self.mode = old_mode
                if rep is None:
                    continue
                whole, rep_dict, enc = rep
                if not rep_dict:
                    continue
                try:
                    self._write_atomic(
                        out_path,
                        lambda p: self.p.generateOutputFileFromString(
                            p, whole, needXmlHeader=False,
                            encoding=enc or "utf-8"))
                except Exception as e:
                    self.failed_strings += 1
                    self._ledger.pop(rel.replace("\\", "/"), None)
                    self.detail("  字面文本回写失败：%s（%s: %s）"
                                % (rel, type(e).__name__, e))
                    continue
                self._merge_generated_extract(extract, file_extract)
                written += 1
                total += len(rep_dict)
                self._record_done(rel, full, out_path)
                if written % 20 == 0:
                    self.log("字面文本：已改写 %d 个文件 / %d 条…"
                             % (written, total))
        finally:
            if extract:
                name = "sgtat_auto_texts"
                table_path = os.path.join(done_dir, "configs", "text",
                                          name + ".xml")
                self._flush_generated_ids(done_dir, extract,
                                          name="configs/text/%s.xml" % name)
                try:
                    self._register_localization(done_dir, name)
                except Exception as e:
                    self.failed_strings += 1
                    self.detail("  localization.ltx 注册失败：%s" % e)
                    raise
                self.log("字面文本表：%s（%d 条），已写入 localization.ltx"
                         % (table_path, len(extract)), "ok")
        return {"files": written, "entries": total}


    def _register_localization(self, done_dir, table_name):
        """把生成的文本表名追加到 configs/localization.ltx 的 files 行。"""
        src = os.path.join(self.in_dir, "configs", "localization.ltx")
        out = os.path.join(done_dir, "configs", "localization.ltx")
        if os.path.isfile(src):
            text = read_text_any(src)
        else:
            text = ("[string_table]\nlanguage = %s\nfont_prefix =\n"
                    % (self._lang_hint or "rus"))
        lines = text.splitlines()
        found = False
        for i, line in enumerate(lines):
            s = line.strip()
            if s.lower().startswith("files") and "=" in s:
                key, value = line.split("=", 1)
                names = [x.strip() for x in value.split(",") if x.strip()]
                if table_name not in names:
                    names.append(table_name)
                lines[i] = key + "= " + ", ".join(names)
                found = True
                break
        if not found:
            lines.extend(["", "files = " + table_name])
        def write_localization(path):
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write("\n".join(lines) + "\n")

        self._write_atomic(out, write_localization)


    def _copy_reference_layer(self, done_dir):
        if not self.copy_reference:
            return 0
        copied = 0
        for full in sorted(self._ref_files):
            rel = os.path.relpath(full, self.in_dir)
            out = os.path.join(done_dir, rel)
            if os.path.isfile(out) and files_equal(full, out):
                continue
            try:
                self._copy_file(full, out)
                copied += 1
            except Exception as e:
                self.failed_strings += 1
                self.detail("  复制引用层失败：%s（%s）" % (rel, e))
        # 顶层 configs/*.ltx：localization.ltx 是文本表注册表，
        # external.ltx/fonts.ltx 等是解析/字体配置；补丁把它们算作有用文件。
        # 原样复制（不改内容），保证“不漏文件”。
        cfg = os.path.join(self.in_dir, "configs")
        if os.path.isdir(cfg):
            for fn in sorted(os.listdir(cfg)):
                if not fn.lower().endswith(".ltx"):
                    continue
                full = os.path.join(cfg, fn)
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, self.in_dir)
                out = os.path.join(done_dir, rel)
                if os.path.isfile(out) and files_equal(full, out):
                    continue
                try:
                    self._copy_file(full, out)
                    copied += 1
                except Exception as e:
                    self.failed_strings += 1
                    self.detail("  复制配置失败：%s（%s）" % (rel, e))
        if self._unresolved:
            try:
                path = os.path.join(done_dir, "待确认_未解析引用.csv")
                with open(path, "w", encoding="utf-8-sig",
                          newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["id", "count"])
                    for key, count in sorted(
                            self._unresolved.items(),
                            key=lambda kv: (-kv[1], kv[0])):
                        writer.writerow([key, count])
            except Exception as e:
                self.failed_strings += 1
                self.detail("  未解析引用清单写入失败：%s" % e)
        return copied


    def _copy_file(self, src, dst):
        self._write_atomic(dst, lambda tmp: shutil.copy2(src, tmp))


    @staticmethod
    def _prune_empty_dirs(root):
        """自底向上删除不含任何文件的空目录；保留根目录本身。
        返回删除的目录数。"""
        removed = 0
        if not os.path.isdir(root):
            return 0
        for cur, dirs, files in os.walk(root, topdown=False):
            if os.path.abspath(cur) == os.path.abspath(root):
                continue
            try:
                if not os.listdir(cur):
                    os.rmdir(cur)
                    removed += 1
            except OSError:
                pass
        return removed


    def _classify_file(self, full, rel):
        """文件类型判定（转发到模块级 classify_xray_file，与勘察共用一处口径）。"""
        return classify_xray_file(full, rel)


    def _flush_generated_ids(self, done_dir, extract, chars_all=None,
                             name="___auto_put_this_to_text_folder.xml"):
        data = dict(extract or {})
        chars = set(chars_all or ())
        chars.update(data.pop("chars_all_in", set()) or set())
        path = os.path.join(done_dir, name)
        normalized = name.replace("\\", "/")
        invalid = normalized in getattr(self, "_invalid_generated_tables", set())
        if os.path.isfile(path) and not invalid:
            try:
                old = self._parse(self.p.parse_xray_text_xml, path,
                                  read_encoding_chain(path))
                for ent in (old or []):
                    _lang, value = self.p.getRecommendLangText(ent, self.tgt)
                    if not value:
                        values = [v for v in (ent.texts or {}).values() if v]
                        value = values[0] if values else ""
                    if value:
                        data.setdefault(ent.id, value)
            except Exception:
                pass
        if chars:
            text = "".join(sorted(chars))
            for i, piece in enumerate(
                    self.p.splitTextToPiecesAtLength(text, 500)):
                data["text_gen_for_font_%d" % i] = piece
        if not data and invalid:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            with self._ledger_lock:
                self._ledger.pop(normalized, None)
            self._invalid_generated_tables.discard(normalized)
            return
        if data:
            try:
                self._write_atomic(
                    path,
                    lambda p: self.p.generateOutputXml(p, data, splitLength=1000))
                self._record_generated(normalized, path)
            except Exception as exc:
                self.failed_strings += 1
                self._revoke_done(normalized)
                self.detail("  生成字符串表写入失败：%s（%s）"
                            % (name, exc))
                return False
            return True
        return False


    def _run_auto(self, done_dir, reuse):
        temp_dir = os.path.join(done_dir, "__temp__")
        os.makedirs(temp_dir, exist_ok=True)
        reuse.update(self._read_existing(temp_dir))

        targets = []
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, self.in_dir)
                kind = self._classify_file(full, rel)
                if kind:
                    targets.append((kind, rel, full))
        targets.sort(key=lambda x: x[1])
        total = len(targets)
        counts = {}
        for kind, _, _ in targets:
            counts[kind] = counts.get(kind, 0) + 1
        self.log("识别到可翻译文件 %d 个：文本XML %d / 配置XML %d / ltx %d / 脚本 %d"
                 % (total, counts.get("text", 0), counts.get("cfg", 0),
                    counts.get("ltx", 0), counts.get("script", 0)))

        extract = {}
        prefix = "sgtat_gen_text_%d_" % time.time_ns()
        gen = [0]
        chars_all = set()
        written = 0
        failed = 0
        trans_files = 0
        try:
            for idx, (kind, rel, full) in enumerate(targets, 1):
                if self._cancelled():
                    self.log("已取消，已完成文件保留在输出目录")
                    break
                out_path = os.path.join(done_dir, rel)
                if self._should_skip(rel, full, out_path, kind):
                    continue
                self._log_progress(idx, total, rel)
                self.progress({"file": rel, "file_idx": idx, "files": total,
                               "ids": trans_files, "total_ids": 0,
                               "tabs": trans_files, "pending": self.pending_files,
                               "reqs": self.req_count, "unit": "个文件"})
                if kind == "text":
                    tmp = os.path.join(temp_dir, rel)
                    try:
                        n_texts = self._run_text_file(
                            os.path.basename(full), full, out_path,
                            tmp, reuse, idx, total)
                    except Exception as e:
                        failed += 1
                        self.log("文件处理失败，已跳过：%s（%s: %s）"
                                 % (rel, type(e).__name__, e), "err")
                        continue
                    if n_texts == -1:
                        break
                    if n_texts == -2:
                        failed += 1
                        continue
                    if n_texts > 0:
                        written += 1
                        trans_files += 1
                        self._record_done(rel, full, out_path)
                else:
                    mode = {"cfg": "cfgxml", "ltx": "ltx",
                            "script": "scriptL"}[kind]
                    old_mode = self.mode
                    self.mode = mode
                    file_extract = {}
                    try:
                        rep = self._process_non_text(
                            full, reuse, file_extract, prefix, gen)
                    except Exception as e:
                        failed += 1
                        self.log("文件处理失败，已跳过：%s（%s: %s）"
                                 % (rel, type(e).__name__, e), "err")
                        continue
                    finally:
                        self.mode = old_mode
                    if rep is None:
                        continue
                    whole_text, rep_dict, enc = rep
                    if rep_dict or self.output_anyway:
                        try:
                            if kind in ("cfg", "ltx"):
                                self._write_atomic(
                                    out_path,
                                    lambda p: self.p.generateOutputFileFromString(
                                        p, whole_text, needXmlHeader=False,
                                        encoding=enc or "utf-8"))
                            else:
                                self._write_atomic(
                                    out_path,
                                    lambda p: self.p.generateOutputFileFromString(
                                        p, whole_text, needXmlHeader=False))
                        except Exception as e:
                            failed += 1
                            self._ledger.pop(rel.replace("\\", "/"), None)
                            self.log("文件写入失败，已跳过：%s（%s: %s）"
                                     % (rel, type(e).__name__, e), "err")
                            continue
                        self._merge_generated_extract(extract, file_extract)
                        if kind == "script":
                            chars_all |= file_extract.get("chars_all_in", set())
                        written += 1
                        self._record_done(rel, full, out_path)
        except Exception as e:
            failed += 1
            self.log("自动翻译中断：%s: %s" % (type(e).__name__, e), "err")
            return False
        finally:
            self._flush_generated_ids(done_dir, extract, chars_all)
        self.log("已写出文件 %d 个（镜像原目录结构，格式保持不变）" % written)
        if failed:
            self.log("有 %d 个文件处理失败（已跳过，其余照常完成），详见日志"
                     % failed, "warn")
        return not failed and not self.failed_strings and not self._cancelled()


    def _flush_temp(self, temp_path, done):
        """把当前进度写进 __temp__：取消/中断后重跑可直接复用已译部分。"""
        if not done:
            return
        try:
            self._write_atomic(
                temp_path,
                lambda p: self.p.generateOutputXml(p, done, splitLength=1000))
            self._temp_clock = time.time()
        except Exception as e:
            self.detail("  临时进度写入失败：%s: %s" % (type(e).__name__, e))


    def _run_text_file(self, fn, full, out_path, temp_path, reuse, idx, total):
        """返回条目数；0 表示无变化，-1 表示取消，-2 表示写盘失败。"""
        rel = os.path.relpath(full, self.in_dir)
        chain = read_encoding_chain(full)
        before_parse = len(getattr(self, "_parse_failures", ()))
        texts = self._parse(self.p.parse_xray_text_xml, full, chain)
        if len(getattr(self, "_parse_failures", ())) > before_parse:
            self.log("源文件无法解析，已跳过：%s（请检查编码/非法 XML 字符）"
                     % fn, "warn")
        self.detail("  共 %d 条文本" % len(texts))
        if not texts:
            if os.path.exists(out_path):
                if self._is_stale_empty_output(out_path):
                    try:
                        os.remove(out_path)
                        self.detail("  已清理空占位输出：%s" % out_path)
                    except OSError:
                        pass
                else:
                    self.log("  未解析出条目，但既有译文有内容，已保留：%s"
                             "（确认是空文件请手动删除）" % out_path, "warn")
            return 0
        if chain and chain[0] not in _ENC_UTF8:
            samples = []
            for ent in texts[:20]:
                samples.extend((ent.texts or {}).values())
            self._warn_mojibake(fn, samples)
        done = {}
        self._temp_clock = time.time()
        metas = []
        for ent in texts:
            lang0, src0 = self._pick_source(ent)
            metas.append({
                "id": ent.id, "ent": ent, "lang": lang0,
                "src": src0 if isinstance(src0, str) else "",
                "rel": full,
                "style": self._entry_style(full, src0 or ""),
            })
        batched = set()
        reused = set()
        if "dialog" in full.lower() and not self.force_glossary:
            def _ok(meta):
                src0 = meta["src"]
                if not src0 or not src0.strip() or meta["id"] in reuse:
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
                if (looks_like_code_or_debug(full, src0)
                        or looks_like_generated_noise(full, src0)
                        or looks_like_technical_text(src0)
                        or (looks_like_identifier_text(src0)
                            and not is_player_facing_label(full, src0)
                            and not _id_player_visible(meta["id"]))):
                    return False
                if self.only_referenced and meta["id"] not in self._ref_counts:
                    return False
                if self._termbase_lookup(src0) is not None:
                    return True
                if self.protect_proper and looks_like_proper_text(full, src0):
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
                        for eid, (_lang, new) in got.items():
                            done[eid] = new
                        batched.update(got)
                        i = j
                        continue
                i += 1
        for n, ent in enumerate(texts, 1):
            if self._cancelled():
                self._flush_temp(temp_path, done)
                self.log("已取消：本文件已完成 %d/%d 条（已存入 __temp__，"
                         "重跑可复用）" % (len(done), len(texts)))
                return -1
            if ent.id in self._past:
                if ent.id not in self.redundant:
                    self.redundant.append(ent.id)
            else:
                self._past.add(ent.id)
            if ent.id in batched:
                continue
            if ent.id in reuse:
                done[ent.id] = reuse[ent.id]
                reused.add(ent.id)
                continue
            lang, src = self._pick_source(ent)
            if self.tgt == lang or self._looks_like_target(src):
                done[ent.id] = src
                self._record_filter_decision(rel, ent.id, "keep", "target_language")
            elif src:
                action, value, reason = self._filter_decision(
                    rel, ent.id, src, lang)
                self._record_filter_decision(rel, ent.id, action, reason)
                if action == "keep":
                    done[ent.id] = src
                elif action == "skip":
                    done[ent.id] = src
                elif action == "exact":
                    done[ent.id] = value
                else:
                    use_lang = self.src_lang if self.src_lang != "auto" else lang
                    # 字段名不可靠：不少 MOD 把英语放进 <rus>，或把俄语放进 <text>。
                    # 自动模式下按本条正文的字母构成覆盖字段判断。
                    if use_lang != "ukr":
                        det = detect_text_lang(src)
                        if det and det != use_lang:
                            use_lang = det
                    style = self._entry_style(full, src)
                    ctx = ""
                    if style == "dialogue" and "dialog" in full.lower():
                        ctx = self._scene_context(metas, n - 1)
                    try:
                        translated = self._translate_one(
                            src, use_lang, context=ctx, style=style)
                        done[ent.id] = translated
                        if translated.strip() == src.strip():
                            self.failed_strings += 1
                    except Exception as e:
                        self.failed_strings += 1
                        self.detail("  条目翻译失败，保留原文：%s（%s: %s）"
                                    % (ent.id, type(e).__name__, e))
                        done[ent.id] = src
            else:
                done[ent.id] = ""
            if time.time() - self._temp_clock > 120:
                self._flush_temp(temp_path, done)
            if n % 20 == 0:
                self.progress({"file": fn, "ids": n, "total_ids": len(texts),
                               "file_idx": idx, "files": total,
                               "reqs": self.req_count, "unit": "个文件"})
        if not any(done.get(meta["id"], "") != meta["src"] for meta in metas):
            rel = os.path.relpath(full, self.in_dir)
            self._discard_unchanged_output(rel, out_path)
            return 0
        rel = os.path.relpath(full, self.in_dir)
        try:
            changes = {
                meta["id"]: (meta["lang"], done[meta["id"]])
                for meta in metas
                if done.get(meta["id"], "") != meta["src"]
            }
            try:
                with open(full, "rb") as handle:
                    handle.read().decode("utf-8-sig")
                utf8_table = True
            except (OSError, UnicodeDecodeError):
                utf8_table = False
            wrote = (self._write_table_changes(full, out_path, changes, rel)
                     if utf8_table else
                     self._write_table_as_utf8(full, out_path, texts,
                                               changes, rel))
            if not wrote:
                raise OSError("语言标签原文定位或写入失败")
        except Exception as e:
            with self._ledger_lock:
                self._ledger.pop(rel.replace("\\", "/"), None)
            self.detail("  文本表写入失败，未记录完成：%s（%s: %s）"
                        % (rel, type(e).__name__, e))
            return -2
        issues = self._verify_table_roundtrip(full, out_path, rel)
        if issues:
            with self._ledger_lock:
                self._ledger.pop(rel.replace("\\", "/"), None)
            for issue in issues:
                self.detail("  回写校验失败：%s（%s）" % (issue, rel))
            return -2
        for meta in metas:
            eid = meta["id"]
            new_text = done.get(eid, "")
            if not new_text or new_text == meta["src"]:
                continue
            reason = self._filter_reason.get((rel, eid), "")
            origin = "reuse" if eid in reused else (
                "model_batch" if eid in batched else
                "deterministic" if reason.endswith("exact") or reason == "proper_partial"
                else "model")
            self._record_provenance(rel, eid, meta["src"], new_text, origin)
        self._audit_tables.add(rel)
        self.progress({"file": fn, "ids": len(texts), "total_ids": len(texts),
                       "file_idx": idx, "files": total, "reqs": self.req_count,
                       "unit": "个文件"})
        return len(texts)


    def _pruned_walk(self):
        """遍历输入树，统一排除：输出目录（按绝对路径，即使它在输入树内或
        不叫 translated_hy_mt2）、__temp__、.git，避免把自己的产物再翻一遍。"""
        for root, dirs, files in os.walk(self.in_dir):
            kept = []
            for d in dirs:
                if (d.endswith(".git") or d in ("__temp__", "translated_hy_mt2")
                        or d.lower().startswith("translated_")):
                    continue
                child = os.path.join(root, d)
                if os.path.isfile(os.path.join(child, ".ai_translate_done.json")):
                    continue
                ap = os.path.normcase(os.path.abspath(child))
                if any(ap == ex or ap.startswith(ex + os.sep)
                       for ex in self._excluded_paths):
                    continue
                kept.append(d)
            dirs[:] = kept
            yield root, dirs, files


    def _collect_text_targets(self):
        """递归收集文本 XML（相对路径, 绝对路径），排除输出目录。"""
        targets = []
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                if fn.lower().endswith(".xml"):
                    full = os.path.join(root, fn)
                    targets.append((os.path.relpath(full, self.in_dir), full))
        targets.sort()
        return targets


    def _iter_files(self):
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                yield os.path.join(root, fn)


    def _run_recursive(self, done_dir, reuse):
        suffix = {
            "cfgxml": ".xml", "ltx": ".ltx",
            "scriptL": ".script", "scriptE": ".script",
        }.get(self.mode)
        if not suffix:
            self.log("未知文件类型：%r（应为 auto/text/cfgxml/ltx/scriptE/scriptL）"
                     % self.mode, "err")
            return False
        targets = [p for p in self._iter_files() if p.lower().endswith(suffix)]
        if self.mode == "cfgxml":
            targets = [p for p in targets
                       if os.path.basename(os.path.dirname(p)).lower() != "text"]
        total = len(targets)
        extract = {}
        text_id_prefix = "sgtat_gen_text_%d_" % time.time_ns()
        gen = [0]
        self.log("待处理 %s 文件 %d 个" % (suffix, total))
        failed = 0
        try:
            for idx, full in enumerate(targets, 1):
                if self._cancelled():
                    self.log("已取消")
                    break
                rel = os.path.relpath(full, self.in_dir)
                out_path = os.path.join(done_dir, rel)
                if self._should_skip(rel, full, out_path, self.mode):
                    continue
                self._log_progress(idx, total, rel)
                file_extract = {}
                try:
                    rep = self._process_non_text(full, reuse, file_extract,
                                                 text_id_prefix, gen)
                except Exception as e:
                    failed += 1
                    self.log("文件处理失败，已跳过：%s（%s: %s）"
                             % (rel, type(e).__name__, e), "err")
                    continue
                if rep is None:
                    continue
                whole_text, rep_dict, enc = rep
                if rep_dict or self.output_anyway:
                    try:
                        if self.mode in ("ltx", "cfgxml"):
                            self._write_atomic(
                                out_path,
                                lambda p: self.p.generateOutputFileFromString(
                                    p, whole_text, needXmlHeader=False,
                                    encoding=enc or "utf-8"))
                        else:
                            self._write_atomic(
                                out_path,
                                lambda p: self.p.generateOutputFileFromString(
                                    p, whole_text, needXmlHeader=False))
                    except Exception as e:
                        failed += 1
                        self._ledger.pop(rel.replace("\\", "/"), None)
                        self.log("文件写入失败，已跳过：%s（%s: %s）"
                                 % (rel, type(e).__name__, e), "err")
                        continue
                    self._merge_generated_extract(extract, file_extract)
                    self._record_done(rel, full, out_path)
        except Exception as e:
            failed += 1
            self.log("%s 翻译中断：%s: %s"
                     % (self.mode, type(e).__name__, e), "err")
            return False
        finally:
            name = "___%s__put_this_to_text_folder.xml" % self.mode
            self._flush_generated_ids(done_dir, extract, name=name)
        if failed:
            self.log("有 %d 个文件处理失败（已跳过，其余照常完成），详见日志"
                     % failed, "warn")
        return not failed and not self.failed_strings and not self._cancelled()


    def _process_non_text(self, full, reuse, extract, prefix, gen):
        chain = read_encoding_chain(full)
        if self.mode == "cfgxml":
            whole, cands, enc = self._parse(
                self.p.parse_xray_cfgxml_xml, full, chain)
        elif self.mode == "ltx":
            whole, cands, enc = self._parse(
                self.p.parse_xray_ltx_file, full, chain)
        else:
            whole, cands = self._parse(
                self.p.parse_xray_script_file, full, chain)
            # 脚本按原编码写回（只把字面文本换成 ASCII 新 ID，不改编码）
            enc = "utf-8"
            for candidate_enc in chain:
                try:
                    whole.encode(candidate_enc)
                    enc = candidate_enc
                    break
                except (UnicodeEncodeError, LookupError):
                    continue
        if chain and chain[0] not in _ENC_UTF8 and looks_like_mojibake(whole):
            self.log("⚠ %s 疑为编码误判（乱码特征），请先用「编码转换」页"
                     "统一转成 UTF-8" % os.path.basename(full), "warn")
        if not cands:
            self.empty_files += 1
            if self.empty_files <= 20:
                self.detail("  %s 未提取到可译文本（%s 模式的提取规则见上游）"
                            % (os.path.basename(full), self.mode))
        prewrapped = set()
        if self.mode in ("scriptE", "scriptL"):
            # 上游提取器不认识上下文；这里只保留显示类调用/文本变量赋值，
            # 跳过 log、string_find/string_gsub、比较运算。见 script_literal_allowed。
            # 上游还会漏掉 set_level_changer_invitation / set_description /
            # AddItem(game.translate_string(...))，这里按显示调用补回。
            extra = extra_script_display_literals(whole)
            prewrapped = script_translate_literal_args(whole)
            cands = list(dict.fromkeys(list(cands) + sorted(extra)))
            cands = [c for c in cands
                     if c in extra or script_literal_allowed_in_file(whole, c)]
        if self._cancelled():
            return None
        rep = {}
        for cand in cands:
            # 这里不能按 reuse 跳过：reuse 的键是文本 XML 的 string id，
            # 与 ltx/cfg/script 的候选串不是同一命名空间；真命中了也拿不到
            # 译文，只会把原文留在输出里。非文本模式的复用由账本负责。
            if self.mode == "cfgxml":
                if cand.strip() == "" or self.p.doesTextLookLikeId(cand):
                    continue
                send = cand
                quoted = False
            elif self.mode == "ltx":
                if self.p.doesTextLookLikeId(cand):
                    continue
                quoted = len(cand) > 1 and cand.startswith('"') and cand.endswith('"')
                send = cand[1:-1] if quoted else cand
            else:
                if len(cand) < 2:
                    continue
                qch = cand[0]
                send = cand[1:-1]
                if (self.p.doesTextLookLikeId(send)
                        or self.p.doesTextLookLikeScript(send)):
                    continue
            rel = os.path.relpath(full, self.in_dir)
            if looks_like_code_or_debug(rel, send):
                continue
            if looks_like_identifier_text(send):
                continue
            # 术语直取只用于 UTF-8 的文本表；脚本/ltx 按源编码（cp1251）
            # 写回，塞中文译名会 UnicodeEncodeError，交给模型走转码路径。
            _exact = self._termbase_exact_translation(send) \
                if send not in self.glossary and enc.lower() in (
                    "utf-8", "utf8", "utf-8-sig") else None
            if _exact is not None and _exact != send:
                # 术语命中当"已翻译结果"，继续走后续 rep 替换流程；
                # 这里绝不能 return 单值——_process_non_text 契约是三元组。
                rep[send] = _exact
                continue
            if self.protect_proper and looks_like_proper_text(rel, send):
                # ★ 2.0.21：硬层子串替换仅限 UTF-8 表（cp1251 写回塞中文
                # 会 UnicodeEncodeError，与术语直取同口径）。
                if enc.lower() in ("utf-8", "utf8", "utf-8-sig"):
                    _p = (self._partial_glossary_translation(send, rel)
                          if send not in self.glossary else None)
                    if _p is not None and _p != send:
                        rep[send] = _p
                continue
            if self._cancelled():
                return None
            try:
                lang = self.src_lang
                detected = detect_text_lang(send)
                if detected and lang != "ukr":
                    lang = detected
                elif lang in ("auto", "text"):
                    lang = self._lang_hint or "rus"
                transed = self._translate_one(send, lang)
            except Exception as e:
                self.failed_strings += 1
                self.detail("  条目翻译失败，保留原文：%s（%s: %s）"
                            % (str(send)[:40], type(e).__name__, e))
                continue
            if self._cancelled():
                return None
            if not transed or not transed.strip():
                self.failed_strings += 1
                continue
            if transed.strip() == send.strip():
                self.failed_strings += 1
                continue
            if self.mode == "ltx" and quoted:
                transed = '"%s"' % transed
            if self.mode in ("cfgxml", "ltx", "scriptE"):
                key = prefix + str(gen[0])
                gen[0] += 1
                extract[key] = transed
                if self.mode == "scriptE":
                    # 脚本里裸 ID 是未定义全局变量（nil）；补丁统一用
                    # game.translate_string("id")。已经在 translate_string
                    # 里的字面串只换 ID，不能再套一层。
                    if cand in prewrapped:
                        # 外层已有 game.translate_string(...)，只把引号内
                        # 的字面串换成带引号的新 ID，不能退化成裸变量。
                        rep[cand] = '"%s"' % key
                    else:
                        rep[cand] = 'game.translate_string("%s")' % key
                else:
                    rep[cand] = key
            else:
                if "chars_all_in" not in extract:
                    extract["chars_all_in"] = set()
                for ch in transed:
                    extract["chars_all_in"].add(ch)
                inline = unescape_newlines(transed)
                rep[qch + self.p.escapeLiteralText(send, quote=qch) + qch] = (
                    qch + self.p.escapeLiteralText(inline, quote=qch) + qch)
        if not rep:
            return (whole, {}, enc)
        whole = self.p.replaceFromText(whole, rep)
        return (whole, rep, enc)

