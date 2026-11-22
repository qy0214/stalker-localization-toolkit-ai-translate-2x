# -*- coding: utf-8 -*-
"""2.0.12 回归测试：全大写标签豁免/串段闸门/专名硬词/数字兜底/直连并发。"""
import importlib.util
import os
import sys
from unittest.mock import patch

PLUGIN = os.path.join(os.path.dirname(__file__), "..", "plugins",
                      "ai_translate", "plugin.py")
s = importlib.util.spec_from_file_location("p2012", PLUGIN)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond), ""))
    return bool(cond)


def t1_upper_labels():
    """全大写玩家命令放行，真型号仍保护。"""
    cases = [
        ("st_addon_companion_quick_menu.xml", "FOLLOW ME", False),
        ("st_addon_companion_quick_menu.xml", "WAIT HERE", False),
        ("st_addon_companion_quick_menu.xml", "DO NOT LOOT CORPSES", False),
        ("st_achievement.xml", "S.C.H.O.L.A.R.", False),
        ("st_game_relations.xml", "ENEMY", False),
        ("gunsl_up_binoc.xml", "NVIDIA DLSS", True),
        ("x.xml", "AK-74", True),
        ("x.xml", 'MP5 "Sanitizer"', True),
    ]
    for rel, text, expected in cases:
        check("upper:%s" % text[:18],
              m.looks_like_proper_text(rel, text) == expected)


def t2_bleed():
    """串段污染检测：译文含双\\n源无换行应触发；正常译文不触发。"""
    src = "You know Strider, the leader of the new Freedom squad? If you saw"
    bleed = "他是个诚实的人。\\n\\n你知道新成立的自由团小队的领袖吗？"
    check("bleed:串段Detect", m.looks_like_context_bleed(src, bleed))
    check("bleed:正常NoDetect",
          not m.looks_like_context_bleed("Hello world how are you today",
                                         "你好，今天怎么样？"))
    check("bleed:显式标签", m.looks_like_context_bleed(
        "短", "前一句：\n翻译内容"))


def t3_termbase():
    """Zone 入硬词层、Yanov 补入词库。"""
    L = m.load_term_layers()
    check("term:Zone硬词", "Zone" in L["canon"])
    check("term:Zone非软", "Zone" not in L.get("reference", ()))
    d = m.load_termbase_data()
    flat = m.flatten_glossary(d)
    check("term:Yanov译名", flat.get("Yanov") == "亚诺夫")
    check("term:Zone译名", flat.get("Zone") == "禁区")


def t4_number_patch():
    """数字闸门兜底：补数字保留译文，不整段回退。"""
    src = 'FNX-45 in 2007-2009, 2800 rubles'
    out = '这款手枪售价很高，是知名产品。'
    patched = m.patch_missing_numbers(src, out)
    check("num:补数字保留译文",
          patched and "2800" in patched and "这款手枪" in patched)
    # 无中文的译文不能补
    check("num:无中文不补", m.patch_missing_numbers(src, "no chinese") is None)
    # 数字齐全时不改动
    check("num:齐全不改", m.patch_missing_numbers(src, "售价2800卢布") == "售价2800卢布")


def t5_direct_http():
    """直连开关：推理直连、非推理走门面、默认关闭。"""
    m.set_direct_infer_http(False)
    check("http:默认门面", m._DIRECT_INFER_HTTP is False)

    fake_host = type("H", (), {
        "http_post": lambda *a, **k: {"ok": True, "status": 200,
                                      "body": b"FACE"},
        "http_get": lambda *a, **k: None,
    })()
    old_host = m._HOST
    m._HOST = fake_host
    try:
        # 门面模式
        r = m._http_post("http://127.0.0.1:1/v1/chat/completions", b"{}")
        check("http:门面模式", b"FACE" in r.read())
        # 直连推理
        m.set_direct_infer_http(True)
        with patch.object(m, "_http_request",
                          return_value=type("R", (), {
                              "read": lambda s: b"DIRECT"})()):
            r = m._http_post("http://127.0.0.1:1/v1/chat/completions", b"{}")
            check("http:推理直连", b"DIRECT" in r.read())
            r = m._http_post("http://127.0.0.1:1/health", b"{}")
            check("http:非推理仍门面", b"FACE" in r.read())
    finally:
        m._HOST = old_host
        m.set_direct_infer_http(False)


def main():
    t1_upper_labels()
    t2_bleed()
    t3_termbase()
    t4_number_patch()
    t5_direct_http()
    ok = sum(1 for _, r, _ in RESULTS if r)
    for name, r, extra in RESULTS:
        if not r:
            print("FAIL %s %s" % (name, extra))
    print("test_2012_all_fixes: %d/%d 通过" % (ok, len(RESULTS)))
    return ok == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
