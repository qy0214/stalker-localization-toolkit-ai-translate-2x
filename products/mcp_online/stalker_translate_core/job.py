# -*- coding: utf-8 -*-
"""共享任务生命周期：账本、跳过判定、终检与输出目录锁。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time

from .assets import sha256_file
from .pipeline import (
    _MASK_RESIDUE_RE,
    _has_translatable_text,
    _id_player_visible,
    fmt_size,
    is_player_facing_label,
    looks_like_code_or_debug,
    looks_like_context_echo,
    looks_like_identifier_text,
    looks_like_proper_text,
    looks_like_technical_text,
    partial_source_review,
    runtime_tokens_match,
    semantic_role_issues,
)
from .xray import read_encoding_chain, read_text_any


class JobPipeline:
    def _is_stale_empty_output(self, out_path):
        """早期缺陷会给不含文本条目的文件生成空 XML 占位，
        这类“译文”必须能被重跑覆盖；有真实条目的输出一律保留。"""
        try:
            ents = self.p.parse_xray_text_xml(
                out_path, read_encoding_chain(out_path))
        except Exception:
            return False
        return len(ents) == 0


    def _load_ledger(self):
        try:
            with open(self._ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


    def _save_ledger(self):
        if not self._ledger_path:
            return
        with self._ledger_lock:
            try:
                def write_json(path):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(self._ledger, handle, ensure_ascii=False,
                                  sort_keys=True)
                self._write_atomic(self._ledger_path, write_json)
                self._ledger_saved = time.time()
                self._ledger_save_failed = False
            except OSError:
                self._ledger_save_failed = True


    def _record_done(self, rel, full, out_path):
        try:
            ist = os.stat(full)
            ost = os.stat(out_path)
            in_hash = sha256_file(full)
            out_hash = sha256_file(out_path)
        except OSError:
            return
        with self._ledger_lock:
            self._ledger[rel.replace("\\", "/")] = {
                "in_size": ist.st_size,
                "in_mtime_ns": ist.st_mtime_ns,
                "in_hash": in_hash,
                "out_size": ost.st_size,
                "out_hash": out_hash,
                "pipeline": self._pipeline_version(),
            }
            # 每文件全量重写是 O(n²)（几千文件时写放大明显）；5 秒节流，
            # 崩溃最多让最近几秒的文件下次重译（不会漏、不会错）。
            if time.time() - self._ledger_saved > 5:
                self._save_ledger()


    def _revoke_dangling_generated_refs(self, done_dir):
        defined = set()
        referenced = {}
        unparsable = set()
        generated_id_re = re.compile(r"sgtat_gen_text_\d+_\d+")
        for root, dirs, files in os.walk(done_dir):
            dirs[:] = [directory for directory in dirs if directory != "__temp__"]
            for filename in files:
                if not filename.lower().endswith((".xml", ".ltx", ".script")):
                    continue
                path = os.path.join(root, filename)
                rel = os.path.relpath(path, done_dir)
                if filename.lower().endswith(".xml"):
                    before = len(self._parse_failures)
                    try:
                        entities = self._parse(
                            self.p.parse_xray_text_xml, path,
                            read_encoding_chain(path))
                        defined.update(entity.id for entity in (entities or [])
                                       if entity.id)
                    except Exception as exc:
                        # 定义表本身解析失败时不能反推“引用悬空”，否则会
                        # 撤销合法账本并触发无谓重译。
                        unparsable.add(rel)
                        self.detail("  生成 ID 定义表解析失败，跳过撤销判定："
                                    "%s（%s）" % (rel, exc))
                    if len(self._parse_failures) > before:
                        unparsable.add(rel)
                try:
                    raw = read_text_any(path)
                except Exception:
                    continue
                for generated_id in set(generated_id_re.findall(raw or "")):
                    referenced.setdefault(generated_id, []).append(rel)
        dangling = {
            gid for gid, rels in referenced.items()
            if gid not in defined and not any(rel in unparsable for rel in rels)
        }
        revoked = []
        if not dangling:
            return revoked
        with self._ledger_lock:
            for rel, info in list(self._ledger.items()):
                if not isinstance(info, dict) or info.get("generated"):
                    continue
                path = os.path.join(done_dir, rel.replace("/", os.sep))
                try:
                    raw = read_text_any(path)
                except Exception:
                    continue
                if any(generated_id in raw for generated_id in dangling):
                    self._ledger.pop(rel, None)
                    revoked.append(rel)
        if revoked:
            self.log("发现悬空生成 ID，相关正文账本已撤销并重译：%s"
                     % ", ".join(revoked[:10]), "warn")
        return revoked

    def _output_covers_input(self, full, out_path):
        """输出 XML 是否包含输入的全部 string id（用于旧输出版本迁移）。"""
        try:
            in_ents = self._parse(self.p.parse_xray_text_xml, full,
                                  read_encoding_chain(full))
            out_ents = self.p.parse_xray_text_xml(
                out_path, read_encoding_chain(out_path))
        except Exception:
            return False
        if not in_ents:
            # 输入里没有条目：交给 _run_text_file 走"清理空占位输出"的既有路径
            return False
        return {e.id for e in in_ents} <= {e.id for e in out_ents}


    def _discard_unchanged_output(self, rel, out_path):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError as exc:
            self.failed_strings += 1
            self.detail("  零变化输出删除失败：%s（%s）" % (rel, exc))
        self._revoke_done(rel)

    def _revoke_done(self, rel):
        with self._ledger_lock:
            self._ledger.pop(rel.replace("\\", "/"), None)

    def _record_generated(self, rel, out_path):
        try:
            stat = os.stat(out_path)
        except OSError:
            return
        normalized = rel.replace("\\", "/")
        with self._ledger_lock:
            self._ledger[normalized] = {
                "in_size": 0,
                "in_mtime_ns": 0,
                "in_hash": "",
                "out_size": stat.st_size,
                "out_hash": sha256_file(out_path),
                "pipeline": self._pipeline_version(),
                "generated": True,
            }
        self._invalid_generated_tables.discard(normalized)


    def _text_value_pairs(self, src_e, out_e):
        try:
            lang, src_val = self._pick_source(src_e)
            out_texts = getattr(out_e, "texts", None) or {}
            out_val = out_texts.get(lang)
            if out_val is None:
                _out_lang, out_val = self.p.getRecommendLangText(out_e, self.tgt)
        except Exception:
            return []
        if src_val is not None and out_val is not None:
            return [(lang or "text", src_val, out_val)]
        return []


    def _text_output_has_changes(self, full, out_path):
        try:
            in_ents = self._parse(self.p.parse_xray_text_xml, full,
                                  read_encoding_chain(full))
            out_ents = self._parse(self.p.parse_xray_text_xml, out_path,
                                   read_encoding_chain(out_path))
        except Exception:
            return False
        out_map = {e.id: e for e in (out_ents or [])}
        for ent in (in_ents or []):
            other = out_map.get(ent.id)
            if other is None:
                continue
            if any(src_val != out_val
                   for _lang, src_val, out_val in self._text_value_pairs(
                       ent, other)):
                return True
        return False


    def _should_skip(self, rel, full, out_path, kind):
        """该文件是否可以跳过（= 输入未变且输出完整）。"""
        if not os.path.isfile(out_path):
            return False
        try:
            ist = os.stat(full)
            ost = os.stat(out_path)
        except OSError:
            return False
        ent = self._ledger.get(rel.replace("\\", "/"))
        if (ent
                and ent.get("pipeline") == self._pipeline_version()
                and ent.get("in_size") == ist.st_size
                and ent.get("in_mtime_ns") == ist.st_mtime_ns
                and ent.get("out_size") == ost.st_size):
            try:
                return (ent.get("in_hash") == sha256_file(full)
                        and ent.get("out_hash") == sha256_file(out_path))
            except OSError:
                return False
        if self._ledger_present:
            # 有账本 = 严格模式：没记录过的输出一律重做（可能是中断残留，
            # 也可能是早期产物；重做一次后即入账）。
            return False
        # 首次升级：早期输出没有账本。文本按"输出是否覆盖输入 id"
        # 判断（能识别缺新条目/半截 XML），其余格式退回 mtime 判断。
        if kind == "text":
            ok = (self._output_covers_input(full, out_path)
                  and self._text_output_has_changes(full, out_path))
        else:
            ok = ost.st_mtime >= ist.st_mtime and ost.st_size > 0
        if ok:
            self._record_done(rel, full, out_path)   # 补账，下次走严格模式
        return ok


    def _final_audit(self, done_dir):
        """整 MOD 交付前的强制终检；保留输出，但撤销问题文件账本。"""
        with self._ledger_lock:
            ledger_items = list(self._ledger.items())
        missing, empty = [], []
        for rel, info in ledger_items:
            out = os.path.join(done_dir, rel.replace("/", os.sep))
            try:
                size = os.path.getsize(out)
            except OSError:
                missing.append(rel)
                continue
            if size == 0 and int(info.get("out_size") or 0) > 0:
                empty.append(rel)
        bad_parse, lost_ids, residue, n_tables = [], [], [], 0
        untranslated = []          # ★ 2.0.7 条目级漏译（输出=俄文源文）
        ctx_bleed = []             # ★ 2.0.8 上下文串入（前一句/后一句标签）
        amp_lost = []              # ★ 2.0.22 & 保真哨兵（源含 & 译文双空格）
        token_bad = []
        semantic_bad = []
        term_bad = []
        partial_review = []
        unchanged_outputs = []
        dangling_ids = []
        _CYR_AUDIT = re.compile(r"[а-яёА-ЯЁ]{2,}")
        _LATIN_AUDIT = re.compile(r"[A-Za-z]{2,}")
        _GEN_ID_RE = re.compile(r"sgtat_gen_text_\d+_\d+")
        for rel in sorted(self._audit_tables):
            rel = rel.replace("\\", "/")
            n_tables += 1
            out = os.path.join(done_dir, rel.replace("/", os.sep))
            full = os.path.join(self.in_dir, rel.replace("/", os.sep))
            try:
                out_ents = self._parse(self.p.parse_xray_text_xml, out,
                                       read_encoding_chain(out), strict=True)
            except Exception as e:
                bad_parse.append("%s（%s）" % (rel, str(e)[:60]))
                continue
            if os.path.isfile(full):
                try:
                    in_ents = self._parse(self.p.parse_xray_text_xml, full,
                                          read_encoding_chain(full),
                                          strict=True)
                    in_ids = {e.id for e in (in_ents or [])}
                    out_ids = {e.id for e in (out_ents or [])}
                    miss = in_ids - out_ids
                    if miss:
                        lost_ids.append("%s（缺 %d 条：%s…）"
                                        % (rel, len(miss),
                                           ", ".join(sorted(miss)[:3])))
                    # ④ 条目级漏译：同一 id 输出文本仍是输入俄文原样。
                    # 排除已登记专名（词库命中）——那是有意保留，不是漏译。
                    in_map = {e.id: e for e in (in_ents or [])}
                    hits = []
                    token_ids = []
                    semantic_ids = []
                    term_ids = []
                    review_ids = []
                    file_changed = compared = False
                    for e in (out_ents or []):
                        src_e = in_map.get(e.id)
                        if src_e is None:
                            continue
                        for lang_key, in_val, out_val in self._text_value_pairs(
                                src_e, e):
                            if not in_val or not out_val:
                                continue
                            compared = True
                            if in_val != out_val:
                                file_changed = True
                                if not runtime_tokens_match(in_val, out_val):
                                    token_ids.append(e.id)
                                if semantic_role_issues(
                                        in_val, out_val, lang_key):
                                    semantic_ids.append(e.id)
                                if partial_source_review(in_val, out_val):
                                    review_ids.append(e.id)
                                term_counts = {}
                                for _start, _end, key in self._glossary_spans(
                                        in_val):
                                    term_counts[key] = term_counts.get(key, 0) + 1
                                for key, source_count in term_counts.items():
                                    expected = str(
                                        self.gloss._map.get(key)
                                        or self.glossary.get(key) or "").strip(
                                            "\"'«»“”‘’")
                                    if (expected and out_val.lower().count(
                                            expected.lower()) < source_count):
                                        term_ids.append(e.id)
                                        break
                                continue
                            if not (_CYR_AUDIT.search(in_val)
                                    or _LATIN_AUDIT.search(in_val)):
                                continue
                            if not _has_translatable_text(in_val):
                                continue
                            if self._termbase_lookup(in_val) is not None:
                                continue
                            if (looks_like_code_or_debug(rel, in_val)
                                    or looks_like_technical_text(in_val)
                                    or (looks_like_identifier_text(in_val)
                                        and not is_player_facing_label(
                                            rel, in_val)
                                        and not _id_player_visible(e.id))):
                                continue
                            if (self.protect_proper
                                    and looks_like_proper_text(rel, in_val)):
                                continue
                            hits.append(e.id)
                            break
                    if token_ids:
                        token_bad.append("%s（%d 条：%s…）" % (
                            rel, len(token_ids), ", ".join(token_ids[:3])))
                    if semantic_ids:
                        semantic_bad.append("%s（%d 条：%s…）" % (
                            rel, len(semantic_ids),
                            ", ".join(semantic_ids[:3])))
                    if term_ids:
                        term_bad.append("%s（%d 条：%s…）" % (
                            rel, len(term_ids), ", ".join(term_ids[:3])))
                    if review_ids:
                        partial_review.append("%s（%d 条：%s…）" % (
                            rel, len(review_ids), ", ".join(review_ids[:3])))
                    if compared and not file_changed:
                        unchanged_outputs.append(rel)
                    if hits:
                        untranslated.append("%s（%d 条：%s…）"
                                            % (rel, len(hits),
                                               ", ".join(hits[:3])))
                    # ⑤ 上下文串入：输出文本仍带「前一句/后一句」标签。
                    # 这是译文被相邻条目污染的直接证据，必须点名补译。
                    bleed_ids = []
                    for e in (out_ents or []):
                        for out_val in (e.texts or {}).values():
                            if looks_like_context_echo(out_val):
                                bleed_ids.append(e.id)
                                break
                    if bleed_ids:
                        ctx_bleed.append("%s（%d 条：%s…）"
                                         % (rel, len(bleed_ids),
                                            ", ".join(bleed_ids[:3])))
                    # ⑥ & 保真哨兵：源含 & 的已翻条目，译文出现源没有的
                    # 双空格 = 吞 & 特征签名（Heckler & Koch → Heckler  Koch）。
                    # 只点名，不自动改写（译成「黑克勒-科赫」专名属正常）。
                    amp_ids = []
                    for e in (out_ents or []):
                        src_e = in_map.get(e.id)
                        if src_e is None:
                            continue
                        for _lang_key, in_val, out_val in self._text_value_pairs(
                                src_e, e):
                            if not in_val or not out_val or in_val == out_val:
                                continue
                            if "&" not in in_val:
                                continue
                            if "  " in out_val and "  " not in in_val:
                                amp_ids.append(e.id)
                                break
                    if amp_ids:
                        amp_lost.append("%s（%d 条：%s…）"
                                        % (rel, len(amp_ids),
                                           ", ".join(amp_ids[:3])))
                except Exception as e:
                    bad_parse.append("%s（输入表无法复核：%s）" % (rel, str(e)[:60]))
            try:
                raw = read_text_any(out)
            except Exception:
                raw = ""
            if _MASK_RESIDUE_RE.search(raw or ""):
                residue.append(rel)
        defined_ids = set()
        referenced_ids = {}
        for root, dirs, files in os.walk(done_dir):
            dirs[:] = [d for d in dirs if d != "__temp__"]
            for fn in files:
                low = fn.lower()
                if not low.endswith((".xml", ".ltx", ".script")):
                    continue
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, done_dir)
                if low.endswith(".xml"):
                    try:
                        ents = self._parse(
                            self.p.parse_xray_text_xml, path,
                            read_encoding_chain(path))
                        defined_ids.update(e.id for e in (ents or []) if e.id)
                    except Exception:
                        pass
                try:
                    raw = read_text_any(path)
                except Exception:
                    continue
                for gid in set(_GEN_ID_RE.findall(raw or "")):
                    referenced_ids.setdefault(gid, []).append(rel)
        for gid in sorted(set(referenced_ids) - defined_ids):
            dangling_ids.append("%s（%s）" % (
                gid, ", ".join(referenced_ids[gid][:3])))
        details = {
            "missing_files": missing,
            "empty_files": empty,
            "unparseable_tables": bad_parse,
            "missing_ids": lost_ids,
            "mask_residue": residue,
            "untranslated_entries": untranslated,
            "context_bleed": ctx_bleed,
            "runtime_token_damage": token_bad,
            "semantic_role_conflicts": semantic_bad,
            "glossary_conflicts": term_bad,
            "partial_translation_review": partial_review,
            "unchanged_outputs": unchanged_outputs,
            "dangling_generated_ids": dangling_ids,
            "amp_warnings": amp_lost,
        }
        hard_keys = [k for k, v in details.items()
                     if k not in ("amp_warnings", "partial_translation_review")]
        counts = {k: len(v) for k, v in details.items()}
        counts.update({"ledger_files": len(ledger_items),
                       "text_tables": n_tables,
                       "amp_warnings": len(amp_lost)})
        hard_problems = sum(counts[k] for k in hard_keys)
        warnings = len(amp_lost) + len(partial_review)
        retry_files = set()
        for key in hard_keys:
            for item in details[key]:
                retry_files.add(item.split("（", 1)[0])
        for gid in set(referenced_ids) - defined_ids:
            retry_files.update(referenced_ids[gid])
        if retry_files:
            with self._ledger_lock:
                for rel in retry_files:
                    self._ledger.pop(rel.replace("\\", "/"), None)
            self._save_ledger()
        result = {
            "ok": hard_problems == 0,
            "hard_problems": hard_problems,
            "warnings": warnings,
            "counts": counts,
            "details": details,
        }
        self.audit_result = result
        if hard_problems or warnings:
            self.log("终检：账本 %d 个文件 / 改写文本表 %d 张，"
                     "硬性问题 %d 处，警告 %d 处"
                     % (len(ledger_items), n_tables, hard_problems, warnings),
                     "warn")
            for key, label in (
                    ("missing_files", "账本有记录但输出缺失"),
                    ("empty_files", "输出为空文件"),
                    ("unparseable_tables", "文本表解析失败"),
                    ("missing_ids", "输出疑似漏条目"),
                    ("mask_residue", "疑似未还原掩码（$$R…）"),
                    ("runtime_token_damage", "颜色码/占位符损坏"),
                    ("semantic_role_conflicts", "任务/对话语义方向冲突"),
                    ("glossary_conflicts", "硬术语译名缺失或不一致"),
                    ("partial_translation_review", "疑似部分源文未翻译（需人工复核）"),
                    ("unchanged_outputs", "整张输出无实际译文变化"),
                    ("dangling_generated_ids", "生成字符串 ID 缺少定义"),
                    ("untranslated_entries", "条目未翻译（保留原文）"),
                    ("context_bleed", "上下文串入（前一句/后一句）"),
                    ("amp_warnings", "疑似吞 &（& 塌成双空格）")):
                for item in details[key][:20]:
                    self.log("  终检｜%s：%s" % (label, item), "warn")
            if hard_problems > 40:
                self.log("  终检｜其余问题只点名前 40 处，完整计数见返回结果",
                         "warn")
        else:
            self.log("终检通过：账本 %d 个文件、改写文本表 %d 张，"
                     "无悬空 ID/零变化输出/占位符损坏/漏译/语义方向冲突"
                     % (len(ledger_items), n_tables), "ok")
        return result


    def run(self):
        done_dir = self._resolve_done_dir(self.out_dir)
        in_abs = os.path.normcase(os.path.realpath(self.in_dir))
        out_abs = os.path.normcase(os.path.realpath(done_dir))
        try:
            out_contains_input = os.path.commonpath([in_abs, out_abs]) == out_abs
        except ValueError:
            out_contains_input = False
        if out_contains_input:
            self.log("输出目录不能与输入目录相同或是它的上级目录；已拒绝启动",
                     "err")
            return False
        self.out_dir = done_dir
        self.done_dir = done_dir
        self._excluded_paths = {os.path.normcase(os.path.abspath(done_dir))}
        os.makedirs(done_dir, exist_ok=True)
        lock_key = hashlib.sha256(
            os.path.normcase(os.path.realpath(done_dir)).encode("utf-8")
        ).hexdigest()[:20]
        lock_path = os.path.join(tempfile.gettempdir(),
                                 "ait_translate_%s.lock" % lock_key)
        # Windows 上两个进程同时首次 open 同一个锁文件会直接抛
        # PermissionError；短退避重试让“重复启动”走统一的友好拒绝路径。
        lock_stream = None
        for attempt in range(6):
            try:
                lock_stream = open(lock_path, "a+b")
                break
            except OSError:
                if attempt == 5:
                    self.log("输出目录锁文件被占用，已拒绝重复启动", "err")
                    return False
                time.sleep(0.05 * (attempt + 1))
        try:
            lock_stream.seek(0, os.SEEK_END)
            if lock_stream.tell() == 0:
                lock_stream.write(b"0")
                lock_stream.flush()
            lock_stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_stream.close()
            self.log("输出目录已有翻译任务在运行，已拒绝重复启动", "err")
            return False
        try:
            return self._run_locked(done_dir)
        finally:
            try:
                lock_stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            finally:
                lock_stream.close()


    def _run_locked(self, done_dir):
        self._ledger_path = os.path.join(done_dir, ".ai_translate_done.json")
        self._ledger_present = os.path.isfile(self._ledger_path)
        self._ledger = self._load_ledger()
        self._invalid_generated_tables = set()
        for rel, info in list(self._ledger.items()):
            if not isinstance(info, dict) or not info.get("generated"):
                continue
            path = os.path.join(done_dir, rel.replace("/", os.sep))
            try:
                valid = (os.path.getsize(path) == info.get("out_size")
                         and sha256_file(path) == info.get("out_hash"))
            except OSError:
                valid = False
            if not valid:
                self._invalid_generated_tables.add(rel.replace("\\", "/"))
                self.log("生成字符串表缺失或已损坏，相关文件将重新翻译：%s" % rel,
                         "warn")
        if self._invalid_generated_tables:
            with self._ledger_lock:
                self._ledger = {
                    rel: info for rel, info in self._ledger.items()
                    if isinstance(info, dict) and info.get("generated")
                    and rel.replace("\\", "/") not in
                    self._invalid_generated_tables}
        self._revoke_dangling_generated_refs(done_dir)

        if self.auto_session_terms and not self.preview_reference:
            try:
                self._build_session_glossary()
                self.req_count += self.session_requests
            except Exception as e:
                self.log("会话词表生成失败，继续使用通用术语库：%s：%s"
                         % (type(e).__name__, e), "warn")

        if self.auto_freq_names and not self.preview_reference:
            try:
                self._build_freq_names()
            except Exception as e:
                self.log("高频专名定译失败，跳过：%s：%s"
                         % (type(e).__name__, e), "warn")

        reuse = {}
        if (self.reuse_dir and os.path.isdir(self.reuse_dir)
                and not self.reference_mode):
            reuse = self._read_existing(self.reuse_dir)
            self.log("已载入复用译文 %d 条" % len(reuse))

        ok = False
        audit_result = None
        try:
            if self.reference_mode and self.mode in ("auto", "text"):
                ok = self._run_reference_layer(done_dir, reuse)
                if (not ok and self.mode == "auto"
                        and not self._table_entries
                        and not self._cancelled()):
                    # ★ 2.0.23：引用层收编为固定模式后，没有 configs/text
                    # 文本表的 MOD（纯脚本/ltx）自动回退整目录模式，不再
                    # 警告后终止。引用索引为空，only_referenced 若开着会把
                    # 所有条目过滤掉，回退时强制关闭。
                    if self.only_referenced:
                        self.log("无引用索引，「只译被引用条目」在回退模式下不生效",
                                 "warn")
                    self.only_referenced = False
                    self.log("回退：按整目录直接翻译模式继续", "warn")
                    ok = self._run_auto(done_dir, reuse)
            elif self.mode == "auto":
                ok = self._run_auto(done_dir, reuse)
            elif self.mode == "text":
                temp_dir = os.path.join(done_dir, "__temp__")
                os.makedirs(temp_dir, exist_ok=True)
                reuse.update(self._read_existing(temp_dir))
                targets = self._collect_text_targets()
                total = len(targets)
                self.log("待翻译文本 XML %d 个（递归子目录，已排除输出目录）"
                         % total)
                if total == 0:
                    self.log("未发现任何 .xml 文件。请把目录选到 configs"
                             "（文本 XML 在其 text\\ 与 ui\\ 子目录内）", "warn")
                skipped_empty = 0
                trans_files = 0
                failed = 0
                for idx, (rel, full) in enumerate(targets, 1):
                    if self._cancelled():
                        self.log("已取消")
                        ok = False
                        break
                    out_path = os.path.join(done_dir, rel)
                    if self._should_skip(rel, full, out_path, "text"):
                        continue
                    self._log_progress(idx, total, os.path.basename(full))
                    self.progress({"file": rel, "file_idx": idx, "files": total,
                                   "ids": trans_files, "total_ids": 0,
                                   "tabs": trans_files,
                                   "pending": self.pending_files,
                                   "reqs": self.req_count, "unit": "个文件"})
                    try:
                        n_texts = self._run_text_file(
                            os.path.basename(full), full, out_path,
                            os.path.join(temp_dir, rel), reuse, idx, total)
                    except Exception as e:
                        failed += 1
                        self.log("文件处理失败，已跳过：%s（%s: %s）"
                                 % (rel, type(e).__name__, e), "err")
                        continue
                    if n_texts == -2:
                        failed += 1
                        continue
                    if n_texts > 0:
                        trans_files += 1
                        self._record_done(rel, full, out_path)
                    if n_texts == 0:
                        skipped_empty += 1
                else:
                    ok = True
                if skipped_empty:
                    self.log("跳过 %d 个不含 <string> 文本条目的 XML"
                             % skipped_empty)
                if failed:
                    self.log("有 %d 个文件处理失败（已跳过，其余照常完成），"
                             "详见日志" % failed, "warn")
                ok = not failed and not self.failed_strings and not self._cancelled()
            else:
                ok = self._run_recursive(done_dir, reuse)
            ok = bool(ok) and not self.failed_strings and not self._cancelled()
            ok = ok and not getattr(self, "_ledger_save_failed", False)
        finally:
            # 结束时强制落盘（节流写可能还没写过）。但**空账本不写**：
            # 用户一开跑就取消时若落一个空账本，下次会进入严格模式，
            # 把早期遗留的输出全部重译一遍（安全但白白耗时）。
            if not (self.reference_mode and self.preview_reference):
                if self._ledger or self._ledger_present:
                    self._save_ledger()
                removed = self._prune_empty_dirs(done_dir)
                if removed:
                    self.log("已清理 %d 个空目录" % removed)

        # ★ 2.0.2 终检：整 MOD 交付前的最后一道闸。逐条校验（占位符 mask/
        #   校验/回填、失败保留原文）在翻译时就做了，这里验收"整面墙"——
        #   只报告不删改；取消/部分完成时也跑（报告的就是已落盘的部分）。
        if not (self.reference_mode and self.preview_reference):
            try:
                audit_result = self._final_audit(done_dir)
                ok = (bool(ok) and bool(audit_result.get("ok"))
                      and not getattr(self, "_ledger_save_failed", False))
            except Exception as e:
                ok = False
                self.log("终检未完成，本次不能标记为全部完成：%s: %s"
                         % (type(e).__name__, e), "err")

        # ★ 2.0.3 提示词体检：规则违规遥测汇总，为提示词迭代提供数据。
        try:
            self._audit_report()
        except Exception:
            pass

        if ok:
            self.log("全部完成：请求 %d 段，%s 字符%s" %
                     (self.req_count, fmt_size(self.trans_chars),
                      ("，超长文本自动分句 %d 次" % self.long_chunks)
                      if self.long_chunks else ""))
            if self._parse_failures:
                self.log("注意：有 %d 个源文件无法解析并被跳过，详见详细日志"
                         % len(self._parse_failures), "warn")
        else:
            self.log("未全部完成：已生成的内容保留，失败/待复核条目下次运行会重译",
                     "warn")
        if self.tm_stats.get("scope") != "off" or self.tm_stats.get("hits"):
            self.log("TM-lite：%s，精确复用 %d 条，节省 %s 字符"
                     % ("跨表" if self.tm_stats.get("scope") == "global"
                        else "表内", self.tm_stats.get("hits", 0),
                        fmt_size(self.tm_stats.get("saved_chars", 0))),
                     "dim")
        if self.empty_files:
            self.log("有 %d 个文件未提取到可译文本（前 20 个见详细日志）"
                     % self.empty_files)
        if self.failed_strings:
            self.log("有 %d 条文本翻译失败（已保留原文，详见详细日志）"
                     % self.failed_strings, "warn")
        if self.redundant:
            self.log("以下 ID 在多个文件重复，请人工核对：%s"
                     % ", ".join(self.redundant[:50]))
        self.log("译文目录：%s" % done_dir, "ok")
        return ok

