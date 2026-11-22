# -*- coding: utf-8 -*-
"""2.0.12 完成产物的补充审计：复数专名盲区/圆点/冠词/未翻长句。只诊断不改文件。"""
import os
import re
import glob
import json
import importlib.util

BASE = r'C:/Users/Admin/Desktop/IWP/gamedata'
OUT = BASE + '/translated_hy_mt2/configs/text/eng'


def dec(p):
    raw = open(p, 'rb').read()
    try:
        return raw.decode('utf-8-sig')
    except Exception:
        return raw.decode('cp1251', 'ignore')


def ents(p):
    t = dec(p)
    out = []
    for mm in re.finditer(r'<string\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</string>', t, re.S):
        tm = re.search(r'<text\b[^>]*>(.*?)</text>', mm.group(2), re.S)
        if tm:
            out.append((mm.group(1), tm.group(1)))
    return out


s = importlib.util.spec_from_file_location('p', 'plugins/ai_translate/plugin.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

ART_RE = re.compile(r'(?:^(?:An|A|The)|(%c\[[^\]]*\])(?:An|A|The))(?=[\u4e00-\u9fff])')
PLURAL_RE = re.compile(r'(?<![A-Za-z])([A-Z][A-Za-z]{3,}?)(?:s|\u2019s|\'s)(?![A-Za-z])')

long_untr = []
bullets = []
articles = []
plurals = {}

tl = m.load_term_layers()
names = set(tl['names'])
d = json.load(open('plugins/ai_translate/assets/stalker_termbase.json', encoding='utf-8'))
flat = {}
for k, v in d.items():
    if not k.startswith('_') and isinstance(v, dict):
        flat.update(v)

for p in sorted(glob.glob(OUT + '/*.xml')):
    fn = os.path.basename(p)
    for eid, v in ents(p):
        if not v.strip():
            continue
        zh = sum('\u4e00' <= c <= '\u9fff' for c in v)
        words = len(re.findall(r'\b[A-Za-z]{2,}\b', v))
        if zh == 0 and words >= 8:
            long_untr.append((fn, eid, words, v[:60]))
        if '\u2022' in v:
            bullets.append((fn, eid, v[:60]))
        if ART_RE.search(v):
            articles.append((fn, eid, v[:60]))
        if zh:
            for mm in PLURAL_RE.finditer(v):
                w = mm.group(1)
                if w in names and w in flat:
                    v_zh = str(flat[w])
                    if any('\u4e00' <= c <= '\u9fff' for c in v_zh):
                        plurals.setdefault(w, []).append((fn, eid))

print('纯英文长句(>=8词):', len(long_untr))
for x in long_untr[:10]:
    print('  U', x[0], '|', x[1], '| 词', x[2], '|', repr(x[3]))
print('含圆点条目:', len(bullets))
for x in bullets[:6]:
    print('  B', x[0], '|', x[1], '|', repr(x[2]))
print('粘连冠词条目:', len(articles))
for x in articles[:6]:
    print('  A', x[0], '|', x[1], '|', repr(x[2]))
print('复数形态专名残留(2.0.14回填盲区):')
for w, hits in sorted(plurals.items(), key=lambda kv: -len(kv[1])):
    print('  %-14s -> %s : %d 处' % (w, flat.get(w), len(hits)), hits[:3])
