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
    entry_style,
    parse_numbered_batch,
    repair_known_semantic_terms,
    scene_context,
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
            self.assertEqual(job._pipeline_version(), "test")
            self.assertEqual(job.termbase.lookup("Boar"), "野猪")
            self.assertEqual(job._data_root(), tmp)
            self.assertTrue(callable(job.run))
            self.assertTrue(callable(job._translate_one))

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
        self.assertEqual([tool["name"] for tool in TOOL_DEFINITIONS],
                         ["check_env", "translate_mod", "translate_text"])
        self.assertIn("status=done", SERVER_INSTRUCTIONS)

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
