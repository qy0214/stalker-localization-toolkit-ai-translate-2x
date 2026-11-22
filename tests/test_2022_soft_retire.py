# -*- coding: utf-8 -*-
"""2.0.22 回归：软层退役 + 硬层子串替换加固 + title 长句豁免 + 型号直取。

来源：每 MOD 随机 20 文件测试三轮（678 文件，零重叠）+ 人工参考对照 +
逐条语义自判。报告 audit-reports/TEST_RANDOM20_PER_MOD.txt / _R2 / _R3。
"""
import importlib.util
import json
import os
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate", "plugin.py")
ASSETS = os.path.join(ROOT, "plugins", "ai_translate", "assets")

spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

R = []
def check(name, ok, detail=""):
    R.append((name, bool(ok)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))
    return ok

# ── 1 版本 ──────────────────────────────────────────────────────
check("version:2.0.22", m.PLUGIN_INFO["version"] == "2.0.22",
      m.PLUGIN_INFO["version"])

# ── 2 软层退役：GAMMA / Gunslinger 参考不再参与整串直取 ──────────
tb = json.load(open(os.path.join(ASSETS, "stalker_termbase.json"),
                    encoding="utf-8"))
gb = json.load(open(os.path.join(ASSETS, "glossary_builtin.json"),
                    encoding="utf-8"))
soft = ("GAMMA 参考", "Gunslinger 参考")
check("软层分类已移出词库", not any(c in tb for c in soft),
      [c for c in soft if c in tb])
check("_meta.layers.reference 不含软层",
      not any(c in tb["_meta"]["layers"]["reference"] for c in soft),
      tb["_meta"]["layers"]["reference"])
check("_DEFAULT_LAYERS.reference 不含软层",
      not any(c in m._DEFAULT_LAYERS["reference"] for c in soft),
      m._DEFAULT_LAYERS["reference"])
# 抽样自判明确错的软层译名，退役后必须查不到
for src in ("HK USP Match", "Find %s for Cardan", "Вставить",
            ".45 Hydra-shock", "Остров валунов", "HK G36", "FN P90"):
    check("直取不再命中软层误译 %r" % src,
          m.termbase_lookup(src) is None and src not in gb,
          m.termbase_lookup(src))
check("垃圾键 '.' 已不存在", m.termbase_lookup(".") is None and "." not in gb)
check("垃圾键 '...' 已删除", m.termbase_lookup("...") is None)
# 退役数据留档可追溯
ret = os.path.join(ROOT, "corpus", "soft_reference_retired.json")
check("退役数据留档 corpus/soft_reference_retired.json", os.path.exists(ret))
if os.path.exists(ret):
    rd = json.load(open(ret, encoding="utf-8"))
    check("留档含两层原文", all(c in rd for c in soft),
          [c for c in rd if not c.startswith("_")])
    check("留档带退役原因", bool(rd.get("_meta", {}).get("reason")))

# ── 3 词库计数自洽 ──────────────────────────────────────────────
n_cat = sum(len(v) for k, v in tb.items()
            if not k.startswith("_") and isinstance(v, dict))
check("_meta.total_entries == 分类求和",
      tb["_meta"]["total_entries"] == n_cat,
      "%s / %d" % (tb["_meta"]["total_entries"], n_cat))
check("_meta.categories 逐类计数正确",
      all(tb["_meta"]["categories"][c] == len(tb[c])
          for c in tb["_meta"]["categories"] if isinstance(tb.get(c), dict)))
check("扁平镜像条数 == 分类求和", len(gb) == n_cat, "%d / %d" % (len(gb), n_cat))

# ── 4 硬层子串替换：破碎产物必须被拦 ────────────────────────────
broken = [
    ("stable_game_credits.xml", "Executive Vice President"),
    ("stable_game_credits.xml", "Database Applications Engineer"),
    ("st_characters.xml", 'Senior Sergeant "Mangun" Potryko'),
    ("st_characters.xml", 'Lieutenant "Aspirin" Sostradov'),
    ("st_items_outfit.xml", 'Duty Sphere M12 "Red Line"'),
    ("string_table_general.xml",
     "SOS signal: Somebody help! I'm in a hut in the swamp..."),
    ("mlr_addon.xml", "Following the tracks of Clear Sky: talk with Forester"),
    ("ui_st_mm_mp.xml", "Max players"),
    ("ui_st_secrets.xml", "Case in the fountain"),
]
for rel, s in broken:
    check("破碎替换被拦 %r" % s[:34],
          m.partial_glossary_translation(s, rel) is None,
          m.partial_glossary_translation(s, rel))

clean = [
    ("st_characters.xml", 'Vasya "Boar"', "瓦夏"),
    ("scripts.xml", "FRITZ：", "弗里茨"),
    ("string_table_enc_weapons.xml", 'AS "Val" PSO-1', "维尔"),
    ("string_table_enc_weapons.xml", "Browning High-Power", "勃朗宁"),
    ("string_table_enc_weapons.xml", "SIG SG-550 «Sniper»", "狙击手"),
    ("string_table_enc_weapons.xml", "Sniper AKMS", "狙击手"),
]
for rel, s, want in clean:
    got = m.partial_glossary_translation(s, rel)
    check("干净替换保留 %r" % s[:30], got is not None and want in got, got)

check("制作名单硬豁免（Harris 不替换）",
      m.partial_glossary_translation("Harris", "stable_game_credits.xml") is None)
check("非名单文件同串仍可替换",
      m.partial_glossary_translation("Harris", "string_table_enc_weapons.xml")
      == "哈里斯",
      m.partial_glossary_translation("Harris", "string_table_enc_weapons.xml"))
check("音乐表硬豁免",
      m.partial_glossary_translation("Rob Zombie", "string_table_ogg_player.xml")
      is None)

# ── 5 title 守卫长句豁免 ────────────────────────────────────────
prose = [
    "Connection error: CD-Key already in use.",
    "Connection error: Your CD-Key is disabled. Contact customer service.",
    "Stash: blocked entrance to the underground, inside an L-shaped pipe.",
    "It's all simple: clear the farmstead of bandits.",
    "Old deserted plant: silent piles of metal behind concrete walls are all "
    "that's left.",
    "WARNING: UNSTABLE GAME STATE\\n \\nA critical Lua error has occurred.",
]
for s in prose:
    check("正文不再判专名 %r" % s[:38],
          not m.looks_like_proper_text("ui_st_other.xml", s))
check("_is_prose_sentence 句末标点+长度",
      m._is_prose_sentence("Stash: blocked entrance to the underground.")
      and not m._is_prose_sentence("Vano:"))

titles = [("string_table_enc_weapons.xml", "Vano:"),
          ("string_table_enc_weapons.xml", "Radio:"),
          ("string_table_enc_weapons.xml", "MP5 — Sanitizer"),
          ("string_table_enc_weapons.xml", 'AK-74 "Rubin"'),
          ("binoc.xml", "NVIDIA DLSS")]
for rel, s in titles:
    check("真标题/型号仍受保护 %r" % s,
          m.looks_like_proper_text(rel, s))

# ── 6 型号形态直取（code 过滤豁免）──────────────────────────────
model_cases = [("string_table_enc_weapons.xml", "PPSH-41"),
               ("st_items_weapons.xml", "BT MP9"),
               ("string_table_enc_weapons.xml", "AK-74")]
for rel, s in model_cases:
    check("型号形态判定 %r" % s, m._is_model_code_form(rel, s))
check("PPSH-41 直取有译文",
      m.termbase_exact_translation("PPSH-41") == "PPSH-41冲锋枪",
      m.termbase_exact_translation("PPSH-41"))
debug_cases = ["True", "False", "None", "dead", "Dead", "ч",
               "http://example.com", "© GSC"]
for s in debug_cases:
    check("调试词/URL/单字符不属型号形态 %r" % s,
          not m._is_model_code_form("ui_st_pda.xml", s))
check("调试词仍被 code 过滤拦", m.looks_like_code_or_debug("ui_st_pda.xml", "True"))
check("名字表不适用型号豁免",
      not m._is_model_code_form("character_name/stable_generate_snames.xml",
                                "AK-74"))

# ── 7 藏宝点/储物点目录豁免 ─────────────────────────────────────
check("treasure 目录属玩家可见", "treasure" in m._PLAYER_LABEL_DIRS)
check("secrets 目录属玩家可见", "secrets" in m._PLAYER_LABEL_DIRS)
for rel, s in [("st_treasures.xml", "Backpack under tree"),
               ("stable_treasure_manager.xml", "Chest on the roof"),
               ("ui_st_secrets.xml", "Safe under the bush")]:
    check("藏宝点名放行送翻 %r" % s,
          m.is_player_facing_label(rel, s)
          and not (m.looks_like_identifier_text(s)
                   and not m.is_player_facing_label(rel, s)))

# ── 7b ogg/music 强保护收窄：物品描述放行，短标签与名单仍保护 ────
OGG = "string_table_ogg_player.xml"
CREDITS = "stable_game_credits.xml"
ogg_prose = ("Влаго-ударостойкий MP3-плеер с радиоприёмником. Целая партия "
             "таких китайских подделок.")
check("ogg 物品长描述放行送翻", not m.looks_like_proper_text(OGG, ogg_prose))
check("ogg 含曲目列表的条目仍保护（歌名不可译，全库 54 条）",
      m.looks_like_proper_text(
          OGG, "Флеш-карта с музыкальными треками для MP3-плеера. \\n \\n"
               "%c[255,1,255,255]ТРЕК 01:%c[255,1,255,1] Алиса — Веретено. \\n"
               "%c[255,1,255,255]ТРЕК 02:%c[255,1,255,1] Сплин — Мы сидели."))
check("ogg 中文曲目列表同样保护",
      m.looks_like_proper_text(
          OGG, "用于 MP3 播放器的音乐曲目闪存卡。 \\n \\n%c[255,1,255,255]"
               "曲目 01:%c[255,1,255,1] Michael Brook — Pearl."))
check("_TRACK_LIST_RE 命中 ТРЕК/曲目",
      bool(m._TRACK_LIST_RE.search("ТРЕК 01: x"))
      and bool(m._TRACK_LIST_RE.search("曲目 01:x"))
      and not m._TRACK_LIST_RE.search("MP3-плеер с радиоприёмником."))
for s in ("ТРЕК 01", "MP3-плеер"):
    check("ogg 短标签仍保护 %r" % s, m.looks_like_proper_text(OGG, s))
check("制作名单短名仍保护", m.looks_like_proper_text(CREDITS, "Steve Nelson"))
check("制作名单长句仍保护（整表不翻是既定策略）",
      m.looks_like_proper_text(
          CREDITS, "Мы хотели бы выразить огромную признательность следующим "
                   "Open-source проектам."))

# ── 8 既有查表行为不回归 ────────────────────────────────────────
check("lookup: Pripyat", m.termbase_lookup("Pripyat") == "普里皮亚季",
      m.termbase_lookup("Pripyat"))
check("lookup: КПК → PDA", m.termbase_lookup("КПК") == "PDA",
      m.termbase_lookup("КПК"))
check("lookup: 大小写不敏感",
      m.termbase_lookup("PRIPYAT") == "普里皮亚季"
      and m.termbase_lookup("pripyat") == "普里皮亚季",
      (m.termbase_lookup("PRIPYAT"), m.termbase_lookup("pripyat")))

print("\n==== %d/%d 通过 ====" % (sum(1 for _n, ok in R if ok), len(R)))
sys.exit(0 if all(ok for _n, ok in R) else 1)
