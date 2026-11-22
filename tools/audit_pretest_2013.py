# -*- coding: utf-8 -*-
"""2.0.13 测试前深度审计：只诊断，不修改任何译文。

覆盖前几轮未系统检查的维度：
  1) XML 良构性 / 编码 / BOM / 行尾符
  2) id 集合差异 / 重复 id / 缺 text 节点
  3) 颜色码 %c[...] 、printf 占位符、转义 \\n、数字 的保真
  4) 前导/尾随空格、内嵌真实换行
  5) 空译文、部分英文残留（混合语言）、重复源文译文不一致
  6) 长度比异常、标点异常、西里尔字母残留
"""
import os, re, glob, sys
from collections import Counter, defaultdict
import xml.etree.ElementTree as ET

SRC = r'C:/Users/Admin/Desktop/IWP/gamedata/configs/text'
OUT = r'C:/Users/Admin/Desktop/IWP/gamedata/translated_hy_mt2/configs/text'
LANGS = ('eng', 'rus')

STR_RE = re.compile(r'<string\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</string>', re.S)
TEXT_RE = re.compile(r'<text\b[^>]*>(.*?)</text>', re.S)
COLOR_RE = re.compile(r'%c\[[^\]]*\]')
PRINTF_RE = re.compile(r'%(?:\d+\$)?[sdifgexX%]')
NUM_RE = re.compile(r'(?<![\w])\d[\d.,:]*\d|\d')
ASCII_WORD_RE = re.compile(r'\b[A-Za-z][A-Za-z\'\-]{2,}\b')
CYRILLIC_RE = re.compile(r'[\u0400-\u04ff]')
CJK_RE = re.compile(r'[\u4e00-\u9fff]')


def parse_entries(path):
    raw = open(path, 'rb').read()
    try:
        txt = raw.decode('utf-8')
    except UnicodeDecodeError:
        txt = raw.decode('utf-8', 'ignore')
    d = {}
    dup = []
    missing_text = []
    for m in STR_RE.finditer(txt):
        eid, body = m.group(1), m.group(2)
        tm = TEXT_RE.search(body)
        if not tm:
            missing_text.append(eid)
            continue
        if eid in d:
            dup.append(eid)
        d[eid] = tm.group(1)
    return txt, raw, d, dup, missing_text


def show(title, rows, limit=12):
    print('\n### %s: %d' % (title, len(rows)))
    for r in rows[:limit]:
        print('   ', r)


def main():
    report = []
    def w(line=''):
        print(line)
        report.append(line)

    w('=== 2.0.13 测试前深度审计（只诊断，不改文件）===')
    all_stats = Counter()
    file_pairs = []
    for lang in LANGS:
        sd = os.path.join(SRC, lang)
        od = os.path.join(OUT, lang)
        sfiles = {os.path.basename(p): p for p in glob.glob(sd + '/*.xml')}
        ofiles = {os.path.basename(p): p for p in glob.glob(od + '/*.xml')}
        for fn in sorted(set(sfiles) & set(ofiles)):
            file_pairs.append((lang, fn, sfiles[fn], ofiles[fn]))

    w('文件对: %d (源与输出都存在)' % len(file_pairs))
    for lang in LANGS:
        s = {os.path.basename(p) for p in glob.glob(os.path.join(SRC, lang) + '/*.xml')}
        o = {os.path.basename(p) for p in glob.glob(os.path.join(OUT, lang) + '/*.xml')}
        if s - o:
            w('  %s 源文件无输出: %d %s' % (lang, len(s - o), sorted(s - o)[:8]))
        if o - s:
            w('  %s 输出无源: %d %s' % (lang, len(o - s), sorted(o - s)[:8]))

    # ---- 1) XML 良构 / 编码 / 行尾 ----
    bad_xml, bom_out, crlf_out, lf_out = [], [], [], []
    for lang, fn, sp, op in file_pairs:
        raw = open(op, 'rb').read()
        if raw.startswith(b'\xef\xbb\xbf'):
            bom_out.append((lang, fn))
        if b'\r\n' in raw:
            crlf_out.append((lang, fn))
        else:
            lf_out.append((lang, fn))
        try:
            ET.parse(op)
        except Exception as e:
            bad_xml.append((lang, fn, str(e)[:100]))
    show('1a XML 解析失败', bad_xml)
    show('1b 输出带 BOM', bom_out)
    w('\n### 1c 输出行尾: CRLF=%d  LF=%d' % (len(crlf_out), len(lf_out)))
    all_stats['bad_xml'] = len(bad_xml)

    # ---- 2) id 集合 ----
    dup_ids, missing_texts, id_missing_out, id_missing_src = [], [], [], []
    pairs = {}  # (lang,fn) -> (S,O)
    for lang, fn, sp, op in file_pairs:
        _, _, S, dup_s, mis_s = parse_entries(sp)
        _, _, O, dup_o, mis_o = parse_entries(op)
        for eid in dup_s: dup_ids.append((lang, fn, 'src', eid))
        for eid in dup_o: dup_ids.append((lang, fn, 'out', eid))
        for eid in mis_o: missing_texts.append((lang, fn, eid))
        for eid in set(S) - set(O): id_missing_out.append((lang, fn, eid))
        for eid in set(O) - set(S): id_missing_src.append((lang, fn, eid))
        pairs[(lang, fn)] = (S, O)
    show('2a 重复 string id', dup_ids)
    show('2b 输出 string 缺 <text>', missing_texts)
    show('2c 源有 id 但输出缺', id_missing_out)
    show('2d 输出多出 id', id_missing_src)
    all_stats.update(bad_xml=0, dup_ids=len(dup_ids), missing_text=len(missing_texts),
                     id_missing_out=len(id_missing_out), id_missing_src=len(id_missing_src))

    # ---- 3) 保真（eng 为主，rus 源是俄语，占位符同样要保真）----
    color_bad, printf_bad, escape_bad, num_bad = [], [], [], []
    lead_lost, trail_lost, embed_nl_diff = [], [], []
    total = 0
    src_by_text = defaultdict(set)  # 重复源文一致性
    for (lang, fn), (S, O) in pairs.items():
        for eid, sv in S.items():
            if eid not in O:
                continue
            ov = O[eid]
            total += 1
            if sv.strip():
                src_by_text[sv.strip()].add((lang, fn, eid, ov))
            # 颜色码：多重集合比较
            sc, oc = sorted(COLOR_RE.findall(sv)), sorted(COLOR_RE.findall(ov))
            if sc != oc:
                color_bad.append((lang, fn, eid, 'src=%d out=%d' % (len(sc), len(oc)), repr(sv[:60])))
            # printf 占位符
            sp_, op_ = sorted(PRINTF_RE.findall(sv)), sorted(PRINTF_RE.findall(ov))
            if sp_ != op_:
                printf_bad.append((lang, fn, eid, ''.join(sp_), ''.join(op_)))
            # 字面 \n 转义序列
            sn, on = sv.count('\\n'), ov.count('\\n')
            if sn != on:
                escape_bad.append((lang, fn, eid, 'src=%d out=%d' % (sn, on)))
            # 数字多重集合
            snum, onum = sorted(NUM_RE.findall(sv)), sorted(NUM_RE.findall(ov))
            if snum != onum:
                num_bad.append((lang, fn, eid, ''.join(snum[:8]), ''.join(onum[:8])))
            # 首尾空格
            sl = sv[:len(sv) - len(sv.lstrip())]; ol = ov[:len(ov) - len(ov.lstrip())]
            st = sv[len(sv.rstrip()):]; ot = ov[len(ov.rstrip()):]
            if sl != ol:
                lead_lost.append((lang, fn, eid, repr(sl), repr(ol), repr(ov[:30])))
            if st != ot:
                trail_lost.append((lang, fn, eid, repr(st), repr(ot), repr(ov[-30:])))
            # 真实换行数量
            snl, onl = sv.count('\n'), ov.count('\n')
            if snl != onl:
                embed_nl_diff.append((lang, fn, eid, 'src=%d out=%d' % (snl, onl), repr(ov[:50])))
    w('\n### 对比条目总数: %d' % total)
    show('3a 颜色码集合不符', color_bad)
    show('3b printf 占位符不符', printf_bad)
    show('3c 字面 \\n 数量不符', escape_bad)
    show('3d 数字集合不符', num_bad)
    show('3e 前导空格不符', lead_lost)
    show('3f 尾随空格不符', trail_lost)
    show('3g 真实换行数不符', embed_nl_diff)
    all_stats.update(color_bad=len(color_bad), printf_bad=len(printf_bad),
                     escape_bad=len(escape_bad), num_bad=len(num_bad),
                     lead_lost=len(lead_lost), trail_lost=len(trail_lost),
                     nl_diff=len(embed_nl_diff))

    # ---- 4) 重复源文译文不一致 ----
    inconsistent = []
    for sv, rows in src_by_text.items():
        outs = set(r[3] for r in rows)
        if len(outs) > 1:
            inconsistent.append((len(rows), repr(sv[:50]), [repr(x[3][:50]) for x in list(rows)[:3]]))
    inconsistent.sort(key=lambda x: -x[0])
    show('4 相同源文译文不一致', inconsistent)
    all_stats['src_inconsistent'] = len(inconsistent)

    # ---- 5) 空译文 / 混合语言 / 西里尔残留 ----
    empty, mixed, cyr_in_eng, cyr_in_rus_no_cjk = [], [], [], []
    for (lang, fn), (S, O) in pairs.items():
        for eid, ov in O.items():
            if not ov.strip():
                empty.append((lang, fn, eid))
                continue
            cjk = len(CJK_RE.findall(ov))
            words = [w for w in ASCII_WORD_RE.findall(ov) if w.lower() not in
                     ('the', 'and', 'for', 'you', 'are', 'not', 'with', 'this', 'that')]
            cyr = CYRILLIC_RE.findall(ov)
            if cjk > 0 and len(words) >= 2:
                mixed.append((lang, fn, eid, repr(ov[:90])))
            if lang == 'eng' and cyr:
                cyr_in_eng.append((lang, fn, eid, repr(ov[:80])))
            if lang == 'rus' and cyr and cjk == 0:
                cyr_in_rus_no_cjk.append((lang, fn, eid, repr(ov[:80])))
    show('5a 空译文', empty)
    show('5b 中文中夹 >=2 个英文词（混合语言）', mixed)
    show('5c eng 译文含西里尔字母', cyr_in_eng)
    show('5d rus 译文含西里尔但无中文（可能未翻）', cyr_in_rus_no_cjk)
    all_stats.update(empty=len(empty), mixed=len(mixed), cyr_in_eng=len(cyr_in_eng),
                     cyr_no_cjk=len(cyr_in_rus_no_cjk))

    # ---- 6) 长度比异常 ----
    len_ratio = []
    for (lang, fn), (S, O) in pairs.items():
        for eid, sv in S.items():
            if eid not in O or not sv.strip() or not O[eid].strip():
                continue
            a, b = len(sv.strip()), len(O[eid].strip())
            if a < 12:
                continue
            r = b / a
            if r < 0.25 or r > 2.6:
                len_ratio.append((round(r, 2), lang, fn, eid, repr(sv[:50]), repr(O[eid][:60])))
    len_ratio.sort(key=lambda x: x[0])
    show('6 长度比异常（<0.25 或 >2.6）', len_ratio)
    all_stats['len_ratio'] = len(len_ratio)

    # ---- 7) 标点异常 ----
    punct = []
    for (lang, fn), (S, O) in pairs.items():
        for eid, ov in O.items():
            cjk = len(CJK_RE.findall(ov))
            if not cjk:
                continue
            # 中文语境里的半角句读（排除数字小数点/缩写）
            half = len(re.findall(r'(?<=[\u4e00-\u9fff])[,;.!?](?!\d)', ov))
            repeat = len(re.findall(r'([，。！？；、,.;!?])\1+', ov))
            if half >= 2 or repeat >= 1:
                punct.append((lang, fn, eid, 'half=%d rep=%d' % (half, repeat), repr(ov[:80])))
    show('7 标点异常', punct)
    all_stats['punct'] = len(punct)

    w('\n=== 汇总 ===')
    for k in sorted(all_stats):
        w('  %-20s %d' % (k, all_stats[k]))

    with open(r'C:/STALKER-AiTranslate-2x/audit-reports/AUDIT_PRETEST_2013.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))


if __name__ == '__main__':
    main()
