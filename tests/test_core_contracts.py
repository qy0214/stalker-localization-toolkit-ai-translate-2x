# -*- coding: utf-8 -*-
"""stalker_translate_core 契约测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core.glossary import (  # noqa: E402
    UserGlossaryStore,
    clean_term_line,
    glossary_hint_block,
    session_category,
    session_term_shape,
)
from stalker_translate_core.matching import (  # noqa: E402
    GlossaryIndex as TermIndex,
    enforce_preferred_terms,
    glossary_hits,
    glossary_spans,
    inline_term_value,
    inside_quotes,
    repair_residual_latin_names,
    repair_residual_source_terms,
)
from stalker_translate_core.models import TIER_PROFILES  # noqa: E402
from stalker_translate_core.pipeline import (  # noqa: E402
    OutputTruncated,
    _ANY_PLACEHOLDER_RE,
    cross_entry_contamination,
    entry_style,
    latin_residue_review,
    parse_numbered_batch,
    repair_known_semantic_terms,
    repair_model_tokens,
    repair_number_values,
    scene_context,
    semantic_role_issues,
    temp_for_style,
)
from stalker_translate_core.single_text import SingleTextPipeline  # noqa: E402
from stalker_translate_core.script import (  # noqa: E402
    script_literal_allowed,
    script_literal_allowed_in_file,
)
from stalker_translate_core.termbase import Termbase  # noqa: E402
from stalker_translate_core.text import (  # noqa: E402
    pattern_translate,
    split_long_text,
    split_protected,
    tm_key,
    tm_measure,
    tm_norm,
    tm_should_enable,
)
from stalker_translate_core.vendor import load_vendor  # noqa: E402
from stalker_translate_core.xray import (  # noqa: E402
    analyze_input_dir,
    classify_xray_file,
    decide_ctx,
    encoding_chain,
    looks_like_ref_value,
    merge_generated_extract,
    patch_table_text,
)
from stalker_translate_core import (  # noqa: E402
    AssetBundle,
    GlossaryIndex,
    OfflineTranslationJob,
    PROTOCOL_VERSION,
    QualityIssue,
    SERVER_INSTRUCTIONS,
    TOOL_DEFINITIONS,
    TokenUsage,
    TranslationRequest,
    TranslationResult,
    flatten_glossary,
    layer_counts,
    pipeline_fingerprint,
    resolve_asset_root,
    tool_result,
)


class GlossaryTests(unittest.TestCase):
    def test_flatten_nested_and_mixed(self):
        data = {
            "_meta": {"ignored": True},
            "hard": {"Alpha": "甲", "Beta": "乙"},
            "flat": "丙",
            "mixed": {"Gamma": {"Delta": "丁"}},
        }
        self.assertEqual(
            flatten_glossary(data),
            {"Alpha": "甲", "Beta": "乙", "flat": "丙", "Delta": "丁"},
        )

    def test_quote_insensitive_both_directions(self):
        index = GlossaryIndex.from_mapping({
            "Boar": "野猪",
            "Учёный «О-Сознания»": "“共同意识”科学家",
            "\"Tears of Electra\"": "“电之泪”",
        })
        self.assertEqual(index.lookup('"Boar"'), "野猪")
        self.assertEqual(index.lookup("«Бармен»"), None)
        self.assertEqual(
            index.lookup("Учёный «О-Сознания»"), "“共同意识”科学家"
        )
        self.assertEqual(index.lookup('"Tears of Electra"'), "“电之泪”")

    def test_casefold_and_first_value_wins(self):
        index = GlossaryIndex.from_mapping({"Pripyat": "普里皮亚季"})
        self.assertEqual(index.lookup("PRIPYAT"), "普里皮亚季")
        self.assertEqual(index.lookup("pripyat"), "普里皮亚季")

    def test_hits_are_bounded_and_longest_first(self):
        index = GlossaryIndex.from_mapping({"Boar": "野猪", "Boar pack": "野猪群"})
        hits = index.hits("Boar pack", limit=1)
        self.assertEqual(hits, (("Boar pack", "野猪群"),))


class PipelineTests(unittest.TestCase):
    def test_pattern_translation_uses_injected_lookup(self):
        self.assertEqual(
            pattern_translate(
                "We were attacked by a boar", lambda term: "野猪" if term == "boar" else None
            ),
            "我们被野猪袭击了。",
        )
        self.assertIsNone(pattern_translate("We met a mystery", lambda term: None))

    def test_split_preserves_protected_tokens_and_all_text(self):
        text = "前文 $$ACTION$$ 后文" * 20
        parts = split_protected(text, 45, _ANY_PLACEHOLDER_RE)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part) < len(text) for part in parts))
        self.assertTrue(all(
            "$$ACTION" not in part.replace("$$ACTION$$", "") for part in parts
        ))

    def test_long_text_split_keeps_sentence_boundaries(self):
        text = "第一句。第二句！第三句？"
        parts = split_long_text(text, 4)
        self.assertEqual("".join(parts), text)
        self.assertIn("。", "".join(parts))

    def test_term_index_honors_boundaries_case_and_variants(self):
        index = TermIndex(
            {"Сталкер": "潜行者", "artifact": "神器"},
            case_sensitive={"Сталкер"},
            variants={"Сталкер": "Сталкер"},
        )
        self.assertEqual(index.hits("Сталкер и сталкер"), [("Сталкер", "潜行者")])
        self.assertEqual(index.hits("一个 artifact 装备"), [("artifact", "神器")])
        self.assertEqual(index.find("preartifact"), [])

    def test_encoding_scan_and_context_selection(self):
        self.assertEqual(encoding_chain("中文".encode("utf-8"))[0], "utf-8")
        self.assertEqual(encoding_chain(b"\xef\xbb\xbf")[0], "utf-8-sig")
        self.assertEqual(decide_ctx("custom", {"max_str": 10000}, cap=8192), 8192)
        self.assertEqual(decide_ctx("mt2"), TIER_PROFILES["mt2"]["ctx"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "text").mkdir()
            path = root / "text" / "sample.xml"
            path.write_text("<string><text>Hello wanderer.</text></string>", encoding="utf-8")
            self.assertEqual(classify_xray_file(str(path), "text/sample.xml"), "text")
            scan = analyze_input_dir(str(root), "auto")
            self.assertEqual(scan["files"], 1)
            self.assertEqual(scan["n_strings"], 1)


class TermbaseTests(unittest.TestCase):
    def test_layers_lookup_and_quote_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase_path = root / "termbase.json"
            builtin_path = root / "builtin.json"
            document = {
                "_meta": {"layers": {
                    "canon": ["物品"], "names": ["人物"],
                    "technical": ["技术"], "reference": ["参考"],
                }},
                "物品": {"Boar": "野猪"},
                "人物": {'"Tears of Electra"': "“电之泪”"},
                "技术": {"PDA": "PDA"},
                "参考": {"Background": "背景"},
            }
            termbase_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            builtin_path.write_text(json.dumps({"Pripyat": "普里皮亚季"}), encoding="utf-8")
            store = Termbase(str(termbase_path), (str(builtin_path),), technical_exact={})
            self.assertEqual(store.lookup('"Tears of Electra"'), "“电之泪”")
            self.assertEqual(store.lookup("pripyat"), "普里皮亚季")
            self.assertEqual(store.hard_keys(), {"Boar"})
            self.assertEqual(store.technical(), {"PDA": "PDA"})
            self.assertEqual(store.exact_translation('"Boar"'), "野猪")

    def test_effective_snapshot_honors_user_layer_and_hard_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "termbase.json"
            path.write_text(json.dumps({
                "_meta": {"layers": {
                    "canon": ["硬词"], "reference": ["软词"],
                }},
                "硬词": {"Zone": "禁区", "Duty": "纪律团"},
                "软词": {"arena": "竞技场", "Background": "背景"},
            }, ensure_ascii=False), encoding="utf-8")
            store = Termbase(str(path))
            store.configure_user_layer(
                {"Zone": "封锁区", "Custom": "自定义"},
                deleted={"Duty", "arena"}, clear_builtin=False)
            self.assertEqual(store.exact_translation("Zone"), "封锁区")
            self.assertEqual(store.exact_translation("Custom"), "自定义")
            self.assertIsNone(store.exact_translation("Duty"))
            self.assertIsNone(store.exact_translation("arena"))
            self.assertIsNone(store.exact_translation("Background"))
            self.assertEqual(store.lookup("Background"), "背景")
            self.assertEqual(store.hard_keys(), {"Zone", "Custom"})
            self.assertEqual(store.partial_translation("Zone"), "封锁区")
            self.assertIsNone(store.partial_translation("Duty"))

    def test_termbase_v3_status_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "termbase.json"
            path.write_text(json.dumps({
                "_meta": {
                    "term_schema": 3,
                    "layers": {"canon": ["硬词"], "technical": ["技术"],
                               "reference": ["软词"]},
                    "terms": {
                        "PDA": {"status": "preserve"},
                        "Zone": {"status": "translate", "preferred": "禁区",
                                 "forbidden": ["区域"], "domain": "location"},
                        "arena": {"status": "contextual", "review_status": "approved"},
                    },
                },
                "硬词": {"Zone": "禁区"},
                "技术": {"PDA": "PDA"},
                "软词": {"arena": "竞技场"},
            }, ensure_ascii=False), encoding="utf-8")
            store = Termbase(str(path))
            self.assertEqual(store.exact_translation("PDA"), "PDA")
            self.assertEqual(store.exact_translation("Zone"), "禁区")
            self.assertIsNone(store.exact_translation("arena"))
            self.assertEqual(store.hard_keys(), {"Zone"})
            self.assertEqual(store.term_status("PDA"), "preserve")
            self.assertEqual(store.term_status("arena"), "contextual")
            self.assertEqual(store.term_details("Zone")["forbidden"], ["区域"])

    def test_partial_translation_rejects_mixed_language_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "termbase.json"
            path.write_text(json.dumps({
                "_meta": {"layers": {"canon": ["物品"], "names": ["人物"]}},
                "物品": {"Boar": "野猪", "Vice": "台钳"},
                "人物": {"Vasya": "瓦夏"},
            }, ensure_ascii=False), encoding="utf-8")
            store = Termbase(str(path))
            self.assertEqual(store.partial_translation('Vasya "Boar"'), '瓦夏 "野猪"')
            self.assertIsNone(store.partial_translation("Executive Vice President"))

    def test_session_and_hint_contracts(self):
        self.assertEqual(session_category("configs/items/weapons.ltx", "gun_name"), "物品·武器·装备·神器")
        self.assertIsNone(session_category("configs/dialog/dialogs.xml", "talk_name"))
        self.assertEqual(session_term_shape("Ваня", "人物与军衔"), "Ваня")
        self.assertEqual(session_term_shape("This is a sentence?", "人物与军衔"), "")
        block, cost = glossary_hint_block([("и", "和"), ("Boar", "野猪")])
        self.assertEqual(block, "术语(用给定译名)：\nBoar=野猪")
        self.assertGreaterEqual(cost, len(block))

    def test_user_glossary_overlay_and_legacy_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user.json"
            store = UserGlossaryStore(str(path), lambda: {"A": "甲", "B": "乙"})
            store.save({"C": "丙"}, deleted={"A"})
            merged, changes, deleted, clear = store.layers()
            self.assertEqual(merged, {"B": "乙", "C": "丙"})
            self.assertEqual(changes, {"C": "丙"})
            self.assertEqual(deleted, {"A"})
            self.assertFalse(clear)
            path.write_text(json.dumps({"A": "甲改", "B": "乙", "D": "丁"}), encoding="utf-8")
            self.assertEqual(store.read_layer(), ({"A": "甲改", "D": "丁"}, set(), False))

    def test_script_literals_require_safe_context_everywhere(self):
        self.assertFalse(script_literal_allowed('log("Hello")', '"Hello"'))
        self.assertTrue(script_literal_allowed('SetText("Hello")', '"Hello"'))
        self.assertFalse(script_literal_allowed_in_file(
            'log("Hello")\nSetText("Hello")', '"Hello"'
        ))


class ApplicationHelperTests(unittest.TestCase):
    def test_glossary_context_and_residual_repairs(self):
        index = TermIndex(
            {"Долга": "“纪律团”", "Nimble": "机灵鬼"},
            case_sensitive={"Долга", "Nimble"},
        )
        source = 'долга и "Броня Долга"'
        start = source.index("Долга")
        self.assertTrue(inside_quotes(source, start, start + len("Долга")))
        self.assertEqual(
            glossary_spans(source, index, {"Долга"}, {"Долга"}),
            [(start, start + len("Долга"), "Долга")],
        )
        self.assertEqual(glossary_hits(source, index, {"Долга"}, quote_required={"Долга"}),
                         [("Долга", "“纪律团”")])
        self.assertEqual(inline_term_value('"Долга"', 1, 6, "“纪律团”"), "纪律团")
        self.assertEqual(
            repair_residual_source_terms(
                '"Долга"', "这是 Долга", index, index._map, {"Долга"}, {"Долга"}
            ),
            "这是 纪律团",
        )
        self.assertEqual(
            repair_residual_latin_names(
                "Nimble", "Nimble 和 Sidorovich", index, index._map, {"Nimble"}
            ),
            "机灵鬼 和 Sidorovich",
        )

    def test_tm_helpers_preserve_exact_signature(self):
        self.assertEqual(tm_norm("  a   b  "), "a b")
        self.assertEqual(tm_key("a $$ACTION$$", "rus")[:2], ("rus", 12))
        stats = tm_measure(["same text", "same text", "other text", "x"])
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["unique"], 2)
        self.assertAlmostEqual(stats["ratio"], 1 / 3)
        self.assertTrue(tm_should_enable(stats, min_entries=3, min_ratio=0.3))
        self.assertFalse(tm_should_enable(stats, enabled=False))

    def test_batch_style_and_semantic_helpers(self):
        self.assertEqual(clean_term_line('1. "Nimble"'), "Nimble")
        self.assertEqual(entry_style("configs/dialog/a.xml", "Hello."), "dialogue")
        self.assertEqual(entry_style("configs/ui/a.xml", "Health"), "label")
        self.assertEqual(temp_for_style("label"), 0.0)
        self.assertEqual(temp_for_style("formal"), 0.1)
        self.assertEqual(temp_for_style("dialogue", 0.25), 0.25)
        self.assertEqual(temp_for_style("formal", 0.7, formal_temp=0.6), 0.6)
        self.assertEqual(temp_for_style("label", 0.7, label_temp=0.05), 0.05)
        self.assertEqual(
            scene_context([{"src": "А"}, {"src": "Б"}, {"src": "В"}], 1),
            "前一句：А\\n后一句：В",
        )
        self.assertEqual(parse_numbered_batch("[1] 甲\n[2] 乙", 2), ["甲", "乙"])
        self.assertIsNone(parse_numbered_batch("[1] 甲\n[1] 乙", 2))
        self.assertEqual(repair_known_semantic_terms("Controller", "控制器", "eng"), "脑波怪")
        self.assertEqual(repair_known_semantic_terms("Controller", "控制器", "rus"), "控制器")

    def test_single_text_pipeline_retries_truncated_chunks(self):
        class Host(SingleTextPipeline):
            def __init__(self):
                self.safe_chars = 1000
                self.ctx_size = 8192
                self.tgt = "chs"
                self._ctx_hint = ""
                self._style_hint = "dialogue"
                self.dialogue_temp = 0.1
                self._stat_lock = threading.Lock()
                self.long_chunks = 0
                self.calls = []

            def log(self, *_args):
                pass

            def _temp_for_style(self):
                return temp_for_style(self._style_hint, self.dialogue_temp)

            def _translate_text(self, text, lang, glossary_hits=None, **_kwargs):
                self.calls.append(text)
                if len(self.calls) == 1:
                    raise OutputTruncated("truncated")
                return "译：" + text

        host = Host()
        source = "A long source sentence that must be split after truncation."
        result = host._translate_chunk(source, "eng", None)
        self.assertGreater(len(host.calls), 1)
        self.assertTrue(result.startswith("译："))
        self.assertEqual("".join(call.removeprefix("译：") for call in host.calls[1:]), source)

    def test_cross_entry_contamination_and_latin_residue(self):
        neighbor = "当然，到底要我做什么？区域已经让我受了很多伤。"
        mixed = neighbor + "你在这儿待了多久？"
        self.assertEqual(
            cross_entry_contamination(mixed, [("previous", neighbor)]),
            ["previous"],
        )
        self.assertEqual(cross_entry_contamination("完全不同的译文", [("previous", neighbor)]), [])
        shared_source = "A prototype designed to protect against psychic radiation."
        current_source = shared_source + " It needs tuning."
        shared_output = "科学家研发的原型，用于保护人们免受心理辐射。"
        self.assertEqual(cross_entry_contamination(
            shared_output + "需要调整。", [("next", shared_output)],
            source_text=current_source,
            neighbor_sources={"next": shared_source},
        ), [])
        self.assertEqual(semantic_role_issues(
            "Tell me about yourself.", "说说你自己吧。", "eng"), [])
        self.assertEqual(semantic_role_issues(
            "Расскажи о себе.", "说说你自己吧。", "rus"), [])
        self.assertIn("guiding", latin_residue_review("我只剩人 guiding，但工具很多。"))
        self.assertNotIn("AK", latin_residue_review("使用 AK 和 PDA。"))

    def test_preferred_terms_replace_forbidden_variants(self):
        index = TermIndex({"Zone": "禁区", "Monolith": "黑石"})
        output = enforce_preferred_terms(
            "Zone and Monolith", "区域和巨石", index,
            {"Zone": "禁区", "Monolith": "黑石"},
            hard={"Zone", "Monolith"},
            details={"Zone": {"status": "translate", "forbidden": ["区域"]},
                     "Monolith": {"status": "translate", "forbidden": ["巨石"]}},
        )
        self.assertEqual(output, "禁区和黑石")

    def test_number_and_model_repair_is_in_place(self):
        self.assertEqual(repair_number_values(
            "Pay 30000 rubles.", "支付20000卢布。"), "支付30000卢布。")
        self.assertEqual(repair_model_tokens(
            'Look at the "FNX-45".', "看看P220。"), "看看FNX-45。")
        self.assertEqual(repair_model_tokens(
            "FNX-45 and P220", "FNX-45和P220"), "FNX-45和P220")

    def test_xray_patch_reference_and_generated_merge(self):
        source = '<string id="x"><text>  source </text></string>'
        patched, ok = patch_table_text(source, "x", "text", "A&B")
        self.assertTrue(ok)
        self.assertIn("<text>  A&amp;B </text>", patched)
        self.assertEqual(patch_table_text(source, "missing", "text", "译文"), (source, False))
        self.assertTrue(looks_like_ref_value("st_character_name"))
        self.assertFalse(looks_like_ref_value("character.xml"))
        target = {"chars_all_in": {"a"}}
        merge_generated_extract(target, {"chars_all_in": {"b"}, "count": 2})
        self.assertEqual(target, {"chars_all_in": {"a", "b"}, "count": 2})


class StandaloneJobTests(unittest.TestCase):
    def test_offline_job_constructs_without_plugin_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "termbase.json"
            path.write_text(json.dumps({
                "_meta": {"layers": {"canon": ["物品"], "names": [],
                                     "technical": [], "reference": []}},
                "物品": {"Boar": "野猪"},
            }, ensure_ascii=False), encoding="utf-8")
            job = OfflineTranslationJob(
                None, None, tmp, "auto", "rus", "chs",
                termbase=Termbase(str(path)), data_root=tmp,
                pipeline_version="test", parallel_cap=4,
            )
            self.assertTrue(job._pipeline_version().startswith("test:"))
            self.assertEqual(job._pipeline_version(), job._pipeline_version())
            other = OfflineTranslationJob(
                None, type("Server", (), {"model_id": "other-model"})(),
                tmp, "auto", "rus", "chs",
                termbase=Termbase(str(path)), data_root=tmp,
                pipeline_version="test", parallel_cap=4,
            )
            self.assertNotEqual(job._pipeline_version(), other._pipeline_version())
            self.assertEqual(job.termbase.lookup("Boar"), "野猪")
            self.assertEqual(job._data_root(), tmp)
            self.assertTrue(callable(job.run))
            self.assertTrue(callable(job._translate_one))

    def test_unified_filter_chain_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "termbase.json"
            path.write_text(json.dumps({
                "_meta": {"layers": {"canon": ["硬词"], "reference": ["软词"]}},
                "硬词": {"Zone": "禁区"}, "软词": {"arena": "竞技场"},
            }, ensure_ascii=False), encoding="utf-8")
            store = Termbase(str(path))
            job = OfflineTranslationJob(
                None, None, tmp, "text", "eng", "chs",
                termbase=store, data_root=tmp, pipeline_version="test",
                hard_glossary_keys=store.hard_keys(),
            )
            self.assertEqual(job._filter_decision(
                "configs/text/a.xml", "a", "Zone", "eng")[:2],
                ("exact", "禁区"))
            job._record_filter_decision(
                "configs/text/a.xml", "a", "exact", "termbase_exact")
            self.assertEqual(job._filter_decision(
                "configs/text/a.xml", "b", "The arena is ready.", "eng")[0], "translate")
            self.assertEqual(job._filter_decision(
                "configs/text/a.xml", "c", "AK-74", "eng")[0], "skip")
            self.assertEqual(job._filter_decision(
                "configs/text/a.xml", "color", "%c[default]Hello", "eng")[0],
                "translate")
            job.only_referenced = True
            self.assertEqual(job._filter_decision(
                "configs/text/a.xml", "d", "Open the door.", "eng")[2],
                "unreferenced")
            self.assertTrue(job.filter_trace)
            self.assertEqual(job._filter_counts["termbase_exact"], 1)

    def test_roundtrip_preserves_structure_and_file_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase_path = root / "termbase.json"
            termbase_path.write_text("{}", encoding="utf-8")
            source = root / "source.xml"
            output = root / "output.xml"
            raw = (b'\xef\xbb\xbf<?xml version="1.0" encoding="windows-1251"?>\r\n'
                   b'<string_table version="1">\r\n'
                   b'  <!-- keep this note -->\r\n'
                   b'  <string id="a" custom="1"><text>Hello &amp; welcome.</text></string>\r\n'
                   b'  <string id="b"><eng>Open the door.</eng></string>\r\n'
                   b'</string_table>\r\n')
            source.write_bytes(raw)
            job = OfflineTranslationJob(
                load_vendor(str(ROOT / "assets/xray_pkg.zip")), None,
                str(root), "text", "eng", "chs",
                out_dir=str(root), termbase=Termbase(str(termbase_path)),
                data_root=str(root), pipeline_version="test",
            )
            self.assertTrue(job._write_table_changes(
                str(source), str(output),
                {"a": ("text", "你好，欢迎。"), "b": ("eng", "开门。")},
                "source.xml"))
            self.assertEqual(job._verify_table_roundtrip(
                str(source), str(output), "source.xml"), [])
            result = output.read_bytes()
            self.assertTrue(result.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b'encoding="utf-8"', result[:100])
            self.assertIn(b"<!-- keep this note -->", result)
            self.assertIn(b'custom="1"', result)

    def test_roundtrip_rejects_structural_and_format_damage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase_path = root / "termbase.json"
            termbase_path.write_text("{}", encoding="utf-8")
            source = root / "source.xml"
            output = root / "output.xml"
            source.write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<string_table version="1">\n<!-- note -->\n'
                '<string id="a" custom="1"><text>Hello.</text></string>\n'
                '<string id="b"><text>Welcome.</text></string>\n'
                '</string_table>\n', encoding="utf-8")
            job = OfflineTranslationJob(
                load_vendor(str(ROOT / "assets/xray_pkg.zip")), None,
                str(root), "text", "eng", "chs", out_dir=str(root),
                termbase=Termbase(str(termbase_path)), data_root=str(root),
                pipeline_version="test",
            )
            cases = {
                "属性": source.read_text(encoding="utf-8").replace(
                    ' custom="1"', '').replace("Hello.", "你好。"),
                "注释": source.read_text(encoding="utf-8").replace(
                    "<!-- note -->", "").replace("Hello.", "你好。"),
                "缺失": '<string_table><string id="a"><text>你好。</text>'
                        '</string></string_table>',
                "多余": source.read_text(encoding="utf-8").replace(
                    "Hello.", "你好。").replace(
                    "</string_table>",
                    '<string id="c"><text>多余。</text></string></string_table>'),
                "重复": source.read_text(encoding="utf-8").replace(
                    'id="b"', 'id="a"').replace("Hello.", "你好。"),
                "编码": source.read_text(encoding="utf-8").replace(
                    'encoding="utf-8"', 'encoding="windows-1251"').replace(
                    "Hello.", "你好。").encode("utf-8"),
            }
            for label, payload in cases.items():
                with self.subTest(label=label):
                    if isinstance(payload, str):
                        payload = payload.encode("utf-8")
                    output.write_bytes(payload)
                    issues = job._verify_table_roundtrip(
                        str(source), str(output), "source.xml")
                    self.assertTrue(issues, label)
            good = source.read_bytes().replace(b"Hello.", "你好。".encode("utf-8"))
            output.write_bytes(good)
            self.assertEqual(job._verify_table_roundtrip(
                str(source), str(output), "source.xml"), [])
            output.write_bytes(b"\xef\xbb\xbf" + good)
            self.assertTrue(any("BOM" in issue for issue in
                                job._verify_table_roundtrip(
                                    str(source), str(output), "source.xml")))
            output.write_bytes(good.replace(b"\n", b"\r\n"))
            self.assertTrue(any("换行" in issue for issue in
                                job._verify_table_roundtrip(
                                    str(source), str(output), "source.xml")))

    def test_provenance_is_private_and_reports_snapshot_diff(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase_path = root / "termbase.json"
            termbase_path.write_text(json.dumps({
                "_meta": {"layers": {"canon": ["硬词"], "names": [],
                                     "technical": [], "reference": []}},
                "硬词": {"Zone": "禁区"},
            }, ensure_ascii=False), encoding="utf-8")
            job = OfflineTranslationJob(
                None, type("Server", (), {"model_id": "test-model"})(),
                str(root), "text", "eng", "chs",
                termbase=Termbase(str(termbase_path)), data_root=str(root),
                glossary={"Zone": "禁区"}, hard_glossary_keys={"Zone"},
                pipeline_version="test",
            )
            src = "Zone is calm."
            out = "禁区很平静。"
            job._filter_reason[("configs/text/a.xml", "a")] = "termbase_exact"
            job._record_provenance("configs/text/a.xml", "a", src, out,
                                   "deterministic")
            entry = job.provenance["configs/text/a.xml::a"]
            self.assertEqual(entry["source_sha1"], hashlib.sha1(
                src.encode("utf-8")).hexdigest())
            self.assertEqual(entry["output_sha1"], hashlib.sha1(
                out.encode("utf-8")).hexdigest())
            self.assertEqual(entry["terms"], ["Zone"])
            self.assertNotIn(src, json.dumps(entry, ensure_ascii=False))
            self.assertNotIn(out, json.dumps(entry, ensure_ascii=False))
            done = root / "done"
            done.mkdir()
            job._save_provenance(str(done))
            path = done / ".ai_translate_provenance.json"
            first = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(first["entries"], job.provenance)
            job._record_provenance("configs/text/a.xml", "a", src,
                                   "禁区暂时安全。", "model")
            job._record_provenance("configs/text/a.xml", "b", "Hello.", "你好。")
            job._save_provenance(str(done))
            second = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(second["changes"]["added"], 1)
            self.assertEqual(second["changes"]["changed"], 1)
            self.assertEqual(second["changes"]["removed"], 0)

    def test_nested_translation_outputs_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase_path = root / "termbase.json"
            termbase_path.write_text("{}", encoding="utf-8")
            for relative in ("configs/text/source.xml",
                             "translated_online_mcp/configs/text/old.xml"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("<string_table/>", encoding="utf-8")
            job = OfflineTranslationJob(
                None, None, str(root), "auto", "eng", "chs",
                termbase=Termbase(str(termbase_path)), data_root=str(root),
            )
            walked = list(job._pruned_walk())
            self.assertFalse(any("translated_online_mcp" in root_path for root_path, _dirs, _files in walked))

    def test_final_audit_detects_neighbor_translation_contamination(self):
        class Entity:
            def __init__(self, entity_id, texts):
                self.id = entity_id
                self.texts = texts

        class Parser:
            def __init__(self, tables):
                self.tables = tables

            def parse_xray_text_xml(self, path, _chain, strict=False):
                return self.tables[str(path)]

            def getRecommendLangText(self, entity, _target):
                return "chs", entity.texts.get("chs", "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase_path = root / "termbase.json"
            termbase_path.write_text(json.dumps({
                "_meta": {"layers": {"canon": [], "names": ["人物"],
                                     "technical": [], "reference": []}},
                "人物": {"Guide": "向导", "Cold": "寒冷"},
            }, ensure_ascii=False), encoding="utf-8")
            input_dir = root / "in"
            output_dir = root / "out"
            input_dir.mkdir()
            output_dir.mkdir()
            source = input_dir / "source.xml"
            output = output_dir / "source.xml"
            source.write_text("source", encoding="utf-8")
            output.write_text("output", encoding="utf-8")
            parser = Parser({
                str(source): [
                    Entity("a", {"eng": "Guide arrived with enough information for everyone."}),
                    Entity("b", {"eng": "Cold spoke about the old days in the swamp."}),
                    Entity("c", {"eng": "The trader explained the route to the northern camp."}),
                ],
                str(output): [
                    Entity("a", {"chs": "向导带来了足够多的信息，这是第一条完整译文，包含足够长的语义内容用于检测。"}),
                    Entity("b", {"chs": "向导带来了足够多的信息，这是第一条完整译文，包含足够长的语义内容用于检测。冷战说起了沼泽里的旧日时光。"}),
                    Entity("c", {"chs": "商人说明了通往北部营地的路线，这是第三条完整译文，也包含足够长的语义内容用于检测。"}),
                ],
            })
            job = OfflineTranslationJob(
                parser, None, str(input_dir), "text", "eng", "chs",
                out_dir=str(output_dir), termbase=Termbase(str(termbase_path)),
                data_root=str(root), glossary={"Guide": "向导", "Cold": "寒冷"},
                force_glossary=False, tm_lite=False, freq_names=False,
            )
            job._audit_tables.add("source.xml")
            job._ledger["source.xml"] = {"out_size": 1}
            audit = job._final_audit(str(output_dir))
            self.assertFalse(audit["ok"])
            self.assertGreaterEqual(audit["counts"]["cross_entry_contamination"], 1)
            self.assertGreaterEqual(audit["counts"]["name_consistency"], 1)
            self.assertNotIn("source.xml", job._ledger)

    def test_final_audit_checks_numbers_models_and_glue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "configs/text/source.xml"
            output = root / "out/configs/text/source.xml"
            source.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            source.write_text('<string_table><string id="a"><text>'
                              'Pay 30000 rubles for the "FNX-45" pistol.</text></string>'
                              '</string_table>', encoding="utf-8")
            output.write_text('<string_table><string id="a"><text>'
                              '支付20000卢布，使用P220。</text></string>'
                              '</string_table>', encoding="utf-8")
            termbase_path = root / "termbase.json"
            termbase_path.write_text("{}", encoding="utf-8")
            job = OfflineTranslationJob(
                load_vendor(str(ROOT / "assets/xray_pkg.zip")), None,
                str(root), "text", "eng", "chs",
                out_dir=str(root / "out"), termbase=Termbase(str(termbase_path)),
                data_root=str(root), pipeline_version="test",
            )
            job._audit_tables.add("configs/text/source.xml")
            audit = job._final_audit(str(root / "out"))
            self.assertGreaterEqual(audit["counts"]["number_fidelity"], 1)
            self.assertGreaterEqual(audit["counts"]["model_fidelity"], 1)

    def test_vendor_loader_returns_shared_xray_parser(self):
        parser = load_vendor(str(ROOT / "assets" / "xray_pkg.zip"))
        self.assertTrue(callable(parser.parse_xray_text_xml))
        self.assertTrue(callable(parser.generateOutputXml))


class AssetTests(unittest.TestCase):
    def test_asset_bundle_hashes_are_stable(self):
        bundle = AssetBundle.load(ROOT / "assets")
        self.assertEqual(set(bundle.hashes), {
            "stalker_termbase.json",
            "glossary_builtin.json",
            "xray_pkg.zip",
            "THIRD_PARTY_NOTICES.txt",
        })
        self.assertTrue(all(len(value) == 64 for value in bundle.hashes.values()))
        self.assertEqual(bundle.fingerprint(), AssetBundle.load(ROOT / "assets").fingerprint())

    def test_resolve_1_9x_asset_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "ai_translate_assets"
            assets.mkdir()
            for name in ("stalker_termbase.json", "glossary_builtin.json",
                         "xray_pkg.zip"):
                (assets / name).write_bytes(b"x")
            plugin = root / "ai_translate.py"
            plugin.write_text("", encoding="utf-8")
            self.assertEqual(
                resolve_asset_root(plugin, "ai_translate_assets"), assets
            )

    def test_pipeline_fingerprint_is_order_independent(self):
        left = pipeline_fingerprint({"core": "1.0.0", "termbase": "abc"})
        right = pipeline_fingerprint({"termbase": "abc", "core": "1.0.0"})
        self.assertEqual(left, right)
        self.assertNotEqual(left, pipeline_fingerprint({"core": "1.0.1", "termbase": "abc"}))


class McpContractTests(unittest.TestCase):
    def test_tool_definitions_are_shared_and_stable(self):
        self.assertEqual(PROTOCOL_VERSION, "2024-11-05")
        self.assertEqual([tool["name"] for tool in TOOL_DEFINITIONS], [
            "check_env", "translation_plan", "translate_mod",
            "job_status", "job_cancel", "translate_text",
        ])
        self.assertIn("status=done", SERVER_INSTRUCTIONS)
        self.assertIn("job_cancel", SERVER_INSTRUCTIONS)

    def test_tool_result_shape(self):
        result = tool_result({"ok": True})
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertFalse(result["isError"])
        self.assertIn('"ok": true', result["content"][0]["text"])

    def test_layer_counts_follow_meta(self):
        document = {
            "_meta": {"layers": {"canon": ["A"], "names": ["B"]}},
            "A": {"one": "一", "two": "二"},
            "B": {"three": "三"},
        }
        self.assertEqual(layer_counts(document), {"canon": 2, "names": 1})


class DomainTests(unittest.TestCase):
    def test_value_objects_are_immutable(self):
        request = TranslationRequest(text="hello")
        with self.assertRaises(Exception):
            request.text = "changed"

    def test_usage_total_and_result_defaults(self):
        usage = TokenUsage(input_tokens=10, output_tokens=5)
        self.assertEqual(usage.total_tokens, 15)
        result = TranslationResult(text="你好", usage=usage)
        self.assertFalse(result.retained_source)
        self.assertEqual(result.issues, ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
