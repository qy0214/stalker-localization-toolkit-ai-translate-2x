# -*- coding: utf-8 -*-
"""共享翻译管线纯函数：提示词、分类、占位符与质量闸门。"""
from __future__ import annotations

import os
import re
from collections import Counter


class OutputTruncated(Exception):
    """模型输出因 token 上限被截断，必须切短后重试。"""


TRANSLATION_SYS_PROMPT = (
    "你是《潜行者 S.T.A.L.K.E.R.》后末日游戏的专业译员，"
    "输入为对话、任务或物品文本。遵守以下规则。\n"
    "\n"
    "【硬性规则】\n"
    "1. 逐字保留、数量与顺序不变：占位符（%s、%d、%1$s）、"
    "控制符（$$ACTION$$、$变量）、脚本与条目ID（如 zat_b101_x、"
    "sgtat_gen_text_1_x、a.b.c）、\\n；疑似代码或标记一律照抄勿译；\n"
    "2. 人名、阵营、地名、怪物、武器物品等专名使用游戏约定译名，"
    "前后统一，不另译、不音译成多个版本；\n"
    "3. 忠实完整，不增译、不漏译、不脑补剧情；\n"
    "4. 粗话、脏话、辱骂、威胁、黑色幽默与残酷描写一律按原意直译，"
    "保留同等粗鲁力度与情绪强度：不得弱化、省略、删除或改写成书面语、"
    "委婉语（дерьмо 就是「屎／狗屎」，不是「糟糕」；заткнись 就是「闭嘴」，"
    "不是「请安静」；башку оторву 就是「拧下你的脑袋」，"
    "不是「我不会放过你」）；原文粗俗则译文粗俗，"
    "不得因内容而回避或粉饰；\n"
    "5. 输入是格变、复数、单数片段或半句时，按词库和上下文还原语义后\n"
    "译成中文，不要保留原文，也不要只译一半；\n"
    "6. 保留原文的换行数量与段落数；不要把两段合并成一段，也不要多加空行；\n"
    "\n"
    "【语气与风格】\n"
    "7. 译文必须是地道的中文口语，读起来要像中国玩家自己在说话："
    "用中文的语序、短句和中文习惯的省略与断句；称呼、亲疏、口气与情绪"
    "贴合说话人身份，自然使用「啊、吧、嘛、呢、唉、喂」等语气词；\n"
    "8. 不要翻译腔：不要「我不得不说」「就……而言」「如此」「因此」「并且」"
    "这类欧化说法，不要生硬的被动句，不要把从句堆成长句，"
    "不要连缀多余的「的」；\n"
    "9. 物品名称、按钮、菜单项这类短标签保持简洁直白，不口语化、不加语气词；\n"
    "10. 反问、讽刺、威胁按说话人的意图译，不要逐字翻转否定："
    "«Ну а на хрен тебе не пойти?» 是「你他妈怎么还不滚？」，"
    "不是「你别去了吧」；\n"
    "11. 引号不是保留原文的理由：引号内的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径才保留原文；\n"
    "12. 术语提示只在语义相符时采用：同一拼写在普通语境（例如 крот=鼹鼠）"
    "不要硬套人名译名；同一人物、物品、地名全篇保持同一个译名。\n"
    "\n"
    "口语对照示例（左为生硬译法，右为地道中文；照右边的路子写）：\n"
    "· 「听着，我有件事要告诉你。」→「哎，我有事找你。」\n"
    "· 「我们无法再继续留在这里了。」→「这儿咱待不下去了。」\n"
    "· 「我不知道你该如何做到这一点。」→「我不知道你咋弄，反正东西我得要。」\n"
    "· 「你明白正在发生什么吗？」→「你懂现在发生啥了吗？」\n"
    "· 「如果你因失血过多而死，我概不负责。」→「你要是流血死了，我可不负责。」\n"
    "\n"
    "只输出译文本身，不加任何解释或说明；原文已是目标语言时原样返回。\n"
    "最后再强调两条：占位符、控制符、条目ID 必须逐字保留；"
    "译文里不得残留整词或整段的俄文。"
)


TRANSLATION_SYS_PROMPT_EN = (
    "你是《潜行者 S.T.A.L.K.E.R.》后末日游戏的专业译员，"
    "输入可能是英语的对话、任务、物品或界面文本。遵守以下规则。\n"
    "\n"
    "【硬性规则】\n"
    "1. 逐字保留、数量与顺序不变：占位符（%s、%d、%1$s）、"
    "控制符（$$ACTION$$、$变量）、脚本与条目ID（如 zat_b101_x、"
    "sgtat_gen_text_1_x、a.b.c）、\\n；疑似代码或标记一律照抄勿译；\n"
    "2. 人名、阵营、地名、怪物、武器物品等专名使用游戏约定译名；"
    "有官方中文名的用官方名，没有的按常见音译，首次出现后保持统一；"
    "缩写、型号、代号（AK-74、PDA、SEVA）保留原文，不要硬造中文；\n"
    "3. 忠实完整，不增译、不漏译、不脑补剧情；英语的冠词、助动词、"
    "重复主语不要逐词搬进中文；\n"
    "4. 粗话、脏话、辱骂、俚语、威胁、黑色幽默与残酷描写一律按原意直译，"
    "保留同等粗鲁力度与情绪强度：不得弱化、省略、删除或改写成书面语、"
    "委婉语（fuck 就是「操／妈的」，shit 就是「屎／狗屎」，不是「糟糕」；"
    "shut up 就是「闭嘴」，不是「请安静」；I'll rip your head off "
    "就是「我拧下你的脑袋」，不是「我不会放过你」）；"
    "原文粗俗则译文粗俗；\n"
    "5. 输入是复数、缩写、单数片段或半句时，按词库和上下文还原语义后"
    "译成中文，不要保留英文原文；\n"
    "6. 保留原文的换行数量与段落数；不要把两段合并成一段，也不要多加空行；\n"
    "\n"
    "【语气与风格】\n"
    "7. 译文必须是地道的中文口语，读起来要像中国玩家自己在说话："
    "用中文的语序、短句和中文习惯的省略与断句；称呼、亲疏、口气与情绪"
    "贴合说话人身份，自然使用「啊、吧、嘛、呢、唉、喂」等语气词；\n"
    "8. 不要翻译腔：不要「我不得不说」「就……而言」「如此」「因此」「并且」"
    "这类欧化说法，不要生硬的被动句，不要把从句堆成长句，"
    "不要连缀多余的「的」；英语习语按中文口语意译"
    "（例如 It's a piece of cake → 小菜一碟），不要逐词硬译；\n"
    "8a. 物品描述、百科正文虽不是对话，同样不许有翻译腔：用自然的中文"
    "说明文，短句直说（「low radiation won't kill you」是「低剂量辐射"
    "死不了人」，不是「你不会因低辐射而死」）；「并」「其」「则」「"
    "免受……的伤害」这类书面连接能免则免；\n"
    "9. 物品名称、按钮、菜单项这类短标签保持简洁直白，不口语化、不加语气词；\n"
    "10. 反问、讽刺、威胁按说话人的意图译，不要逐字翻转否定："
    "「Why don't you just leave?」是「你他妈怎么还不滚？」，"
    "不是「你为什么不离开」；\n"
    "11. 引号不是保留原文的理由：引号内的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径才保留原文；\n"
    "12. 术语提示只在语义相符时采用；同一人物、物品、地名全篇保持同一个译名；"
    "普通语境里的同形词不要硬套专名译名。\n"
    "\n"
    "英语口语对照示例（左为生硬译法，右为地道中文；照右边的路子写）：\n"
    "· 「Listen, I've got something to tell you.」→「哎，我有事找你。」\n"
    "· 「We can't stay here anymore.」→「这儿咱待不下去了。」\n"
    "· 「I don't know how you're going to do it.」→「我不知道你咋弄，反正东西我得要。」\n"
    "· 「Do you understand what's going on?」→「你懂现在发生啥了吗？」\n"
    "· 「Get out of my sight.」→「滚，别让我看见你。」\n"
    "· 「You're a dead man.」→「你死定了。」\n"
    "\n"
    "只输出译文本身，不加任何解释或说明；原文已是中文时原样返回；"
    "不要把英文原文抄进译文，也不要把规则复述出来。\n"
    "最后再强调两条：占位符、控制符、条目ID 必须逐字保留；"
    "译文里不得残留整词或整段的英文或俄文原文。"
)


TRANSLATION_SYS_PROMPT_LABEL = (
    "你是《潜行者 S.T.A.L.K.E.R.》的游戏本地化译者。输入是物品、按钮、"
    "菜单或界面短标签。译成简洁、直白、统一的简体中文；不加语气词，"
    "不扩写，不解释。占位符、控制符、代码/条目ID逐字保留。"
    "专名使用游戏约定译名；只输出译文本身。"
)


TRANSLATION_BATCH_SYS_PROMPT = (
    "你是《潜行者 S.T.A.L.K.E.R.》的场景对话译者。输入是多行连续对话，"
    "每行以 [n] 开头。逐行翻译成自然、口语化的简体中文；必须保留每行"
    "开头的 [n] 编号、行数和顺序，不合并、不拆分、不解释、不加角色名。"
    "上下文里前后句只帮助理解，不要翻译没有编号的内容。"
    "术语提示只在语义相符时采用；引号内的阵营、地名、物品、人名也要译成中文。"
    "输入里的 $$R0$$、$$R1$$ 等控制符必须逐字原样保留，不得翻译、"
    "拆分或改动。"
    "只输出带编号的译文行。"
)


_PROMPT_ECHO_MARKERS = (
    "你是《潜行者",
    "S.T.A.L.K.E.R.》后末日游戏的专业译员",
    "只输出译文本身",
    "口语对照示例",
    "术语(用给定译名)",
)


def looks_like_prompt_echo(text, source=None):
    s = text or ""
    markers = [marker for marker in _PROMPT_ECHO_MARKERS if marker in s]
    if not markers:
        return False
    return not all(marker in (source or "") for marker in markers)


_CONTEXT_ECHO_MARKERS = (
    "前一句：", "后一句：", "上一句：", "下一句：",
    "上下文（仅供理解", "上下文(仅供理解",
)


def looks_like_context_echo(text):
    s = text or ""
    return any(marker in s for marker in _CONTEXT_ECHO_MARKERS)


def looks_like_context_bleed(src, out):
    """译文串入场景上下文：显式标签，或极短源文吐出超长段落。

    后者兜住模型只翻了上下文正文、却没照抄「前一句：」标签的情况：
    例如源文 `Я не готов.`（11 字符）却输出一整段上一条的译文。
    """
    if not out:
        return False
    if looks_like_context_echo(out):
        return True
    # ★ 2.0.12：串段污染——模型把相邻条目的译文一并吐进当前条目，
    # 形成「另一段译文\\n\\n正确译文」。特征是源文无换行、译文却出现双 \\n，
    # 或译文长度远超源文。旧判据只抓极短源文（<24），抓不到长对话串段。
    s = (src or "").strip()
    o = (out or "").strip()
    if s and "\\n" not in s and "\\n" in o:
        return True
    if o.count("\\n") >= 2 and s.count("\\n") == 0:
        return True
    return len(s) < 24 and len(o) > 150


_BIG_NUM_RE = re.compile(r"(?<![\d,.])(\d{3,})(?![\d,.])")


_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _zh_number_values(text):
    """把译文里的中文计数法还原成数值集合，用于核对原文大额数字。

    支持：1.5万 / 3万 / 两万 / 三万 / 十五万 / 100,000 / 100 000。
    中文汉字数字只覆盖万位以内的常见写法，够核对金额即可。
    """
    vals = set()
    if not text:
        return vals
    # 阿拉伯数字 + 万/千（含千分位逗号、空格）
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*([万千])", text):
        try:
            base = float(m.group(1).replace(",", ""))
            mult = 10000 if m.group(2) == "万" else 1000
            v = base * mult
            vals.add(int(v) if v == int(v) else v)
        except ValueError:
            pass
    # 纯汉字数字串，如 三万 / 一万五千 / 两万 / 十五万 / 一百二十五
    # 一次性解析整串（含万/千/百/十多级），避免「一万五千」被拆成 1万+5千。
    for m in re.finditer(r"[零〇一二两三四五六七八九十百千万]+", text):
        seg = m.group(0)
        total, section, num = 0, 0, 0
        for i, ch in enumerate(seg):
            if ch in _ZH_DIGITS:
                num = _ZH_DIGITS[ch]
            elif ch in ("十", "百", "千", "万"):
                unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[ch]
                if ch == "万":
                    # 万之前的部分（section+num）是"几万"
                    total += (section + num or 1) * unit
                    section, num = 0, 0
                else:
                    section += (num or 1) * unit
                    num = 0
            # 末端数字累加到 section/num 由下方统一处理
        value = total + section + num
        if value:
            vals.add(value)
    return vals


def missing_big_numbers(src, out):
    """源文大额数字在译文中找不到（原样/千分位/中文计数）时返回缺失列表。

    找不到 = 模型改写或漏掉了关键数字（金额、年份、坐标、密码），
    必须拦截。返回空列表表示保真通过。
    """
    if not src or not out:
        return []
    s = str(src)
    # ★ 2.0.21（IWP 产物审计）：色码 %c[r,g,b,a] 内的数字是渲染参数，
    # 不参与保真校验——实测 m249 百科的 109/193 为 %c 分量，被当缺失
    # 金额补成「（原文数字 109）（原文数字 193）」注记污染译文。
    s = re.sub(r"%c\[[^\]]*\]", " ", s)
    src_nums = set(_BIG_NUM_RE.findall(s))
    # ★ 2.0.11：型号尾号与年份区间不参与保真校验。
    # 实测武器长段描述含 `FN FNX-45` / `developed in 2007-2009` / `2800`，
    # 旧逻辑把型号 45、年份 2007/2009 也当金额，模型译成中文后数字对不齐，
    # 两次重试仍判缺失 → 整段保留英文（比纯漏译更糟）。
    # 只豁免「字母-数字」的型号尾号和「4位-4位」年份；独立金额/坐标仍严格校验。
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*[-‐‑‒–—]\s*(\d{3,})", s):
        src_nums.discard(m.group(1))
    # ★ 2.0.14：年份区间不再全部豁免。旧逻辑把 2007-2009 两端都丢掉，
    # 模型把「developed in 2007-2009」改成「P220 / 220」时闸门完全放行
    # （实测 esc_2_12_nimble_get_order_131）。改为只豁免区间尾年，保留
    # 起始年参与校验——译文若两个年份都换成别的数字就会被拦下。
    for m in re.finditer(r"\b(\d{4})\s*[-‐‑‒–—]\s*(\d{4})\b", s):
        src_nums.discard(m.group(2))
    if not src_nums:
        return []
    # 仅去掉千分位逗号；小数点逗号不能与普通分隔符混为一谈。
    out_text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", str(out))
    out_nums = set(re.findall(r"\d+", re.sub(r"\s+", "", out_text)))
    out_nums |= {str(int(float(x))) for x in out_nums if x.isdigit()}
    out_vals = _zh_number_values(out)
    missing = []
    for n in src_nums:
        # 原样出现（含千分位：源 30000，译 30,000 → 去逗号后 30000）
        if n in out_nums:
            continue
        iv = int(n)
        if iv in out_vals or float(iv) in out_vals or iv in out_nums:
            continue
        missing.append(n)
    # ★ 2.0.24（IWP 全量审计 D2）：年代的中文纪年表述视为保真。
    # 实测 "1960s / 1990s" 译作「20世纪60年代/90年代」被严格数字核对
    # 判缺失，59 条注记里 38 条是这种误报。译文出现「YY年代」（可带
    # 世纪前缀）或「YYYY年代」时，认定对应十年位年份已表达。
    # 通用规则，不依赖具体 MOD/文本。
    if missing:
        keep = []
        for n in missing:
            if (len(n) == 4 and n.endswith("0")
                    and re.search(r"(?:(?:1[5-9]|20)\s*世纪\s*)?"
                                  + n[2:4] + r"\s*年代|" + n + r"年代",
                                  str(out))):
                continue
            keep.append(n)
        missing = keep
    # 确定性输出顺序：集合迭代顺序随进程哈希变化，会让补数字/测试不稳定
    missing.sort(key=lambda x: (len(x), x))
    return missing


def patch_missing_numbers(src, out, skip=()):
    """数字闸门两次重试仍缺失时的兜底：不整段回退原文，而是把缺失的
    关键数字按出现顺序补进译文，保留已经译好的正文。

    实测武器长段描述（FNX-45 段落）因金额 2800 两次重试都没带上，
    旧逻辑整段保留英文——玩家看到的是一整段看不懂的外文，比数字
    缺失严重得多。这里改为：译文已有中文时，把缺失金额以「（2800）」
    形式补到对应位置附近；找不到锚点则追加到末尾。

    ★ 2.0.24：skip 传入缺失型号串——数字若是某个缺失型号的一部分
    （AK-103 里的 103），由型号保真闸门整串补「（AK-103）」即可，
    再补裸「（103）」就是双注记（实测交互缺陷，回归测试抓出）。
    """
    if not src or not out or not any("\u4e00" <= c <= "\u9fff" for c in out):
        return None                      # 无中文的译文不能补，交给调用方回退
    missed = missing_big_numbers(src, out)
    if not missed:
        return out
    if skip:
        _sk = [str(t) for t in skip]
        missed = [n for n in missed
                  if not any(n in t for t in _sk)]
        if not missed:
            return out
    fixed = out
    for n in missed:
        # 优先插到译文里出现的货币/单位词之后（卢布/美元/格里夫纳等）
        # ★ 2.0.24（IWP 全量审计 D2）：注记字样「原文数字」退役——
        # 兜底补的是裸数字，玩家读到的仍是成品文本而非审计注记。
        m = re.search(r"(卢布|美元|元|格雷|格里夫纳|coin| coins)", fixed)
        if m:
            pos = m.end()
            fixed = fixed[:pos] + "（%s）" % n + fixed[pos:]
        else:
            fixed = fixed.rstrip() + "（%s）" % n
    return fixed


_RU_PROFANITY_RE = re.compile(
    r"дерьм|заткн|хрен|чёрт|черт|бля|хуй|пизд|ебат|еб[ао]|говн|суч|"
    r"сдох|жоп|муд[ао]|уёб|уеб|нахуй|иди ты|пошёл ты|пошел ты", re.I)


_EN_PROFANITY_RE = re.compile(
    r"\b(?:shit|fuck(?:ing|ed|er)?|bitch|bastard|asshole|dick|piss(?:ed|ing)?|"
    r"crap|damn|bullshit|motherfucker|wtf)\b", re.I)


_CYR_WORD_RE = re.compile(r"[А-Яа-яЁё]{2,}")


_ZH_PROFANITY_RE = re.compile(
    r"他妈|妈的|操(?:你|他|她|它|死|蛋|逼|屄)|我操|"
    r"靠[！!。.，,]|滚(?:蛋|开|！|!|。|，)|闭嘴|混蛋|畜生|王八|去死|"
    r"该死|见鬼|鸡巴|屌|日你|找死|活腻|屎|狗屎|放屁")


_PROFANITY_RETRY_NOTE = (
    "本句原文含粗口、辱骂或威胁，必须保留同等粗鲁力度：不得弱化、"
    "删除或改成书面语。按原文直译，例如 на хрен→他妈/滚，"
    "дерьмо→屎/狗屎，заткнись→闭嘴。")


_PLACEHOLDER_RETRY_NOTE = (
    "必须逐字保留输入里的 $$R0$$、$$R1$$ 等控制占位符，"
    "数量、顺序和写法都不能改；只翻译占位符以外的正文。")


_RESIDUAL_CYRILLIC_NOTE = (
    "译文里仍夹着俄文（%s）。引号里的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径保留原文，"
    "不要保留俄文单词。")


def has_source_profanity(text):
    """源文含粗口（俄语/英语/中文任一）——决定是否启用粗口力度闸门。"""
    return bool(_RU_PROFANITY_RE.search(text or "")
                or _EN_PROFANITY_RE.search(text or "")
                or _ZH_PROFANITY_RE.search(text or ""))


def has_zh_profanity(text):
    return bool(_ZH_PROFANITY_RE.search(text or ""))


def has_cjk(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


GLOSSARY_HINT_HEADER = "术语(用给定译名)：\n"


GLOSSARY_HINT_HEADER_OFFICIAL = "参考下面的翻译：\n"


def estimate_tokens(text):
    """保守的多语言 token 估算（混元 12 万词表）：
    汉字/假名/谚文约 1 token/字；拉丁/西里尔字母文本约 0.45 token/字符，
    按 0.55 取偏安全上界；标点空白计入长度。"""
    if not text:
        return 0
    cjk = 0
    for c in text:
        o = ord(c)
        if (0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF
                or 0xAC00 <= o <= 0xD7AF):
            cjk += 1
    other = len(text) - cjk
    return int(cjk * 1.0 + other * 0.55) + 1


def safe_chunk_chars(ctx_size):
    """单次请求可送的最大源文字符数（与画像中的"约 N 字符/段"一致）：
    8192 → 约 3768；16384 → 约 7536。"""
    return int(ctx_size * 0.46)


_SENT_SPLIT = re.compile(r"([。！？!?…\n；;])")


_CLAUSE_SPLIT = re.compile(r"([，,：:])")


_PLACEHOLDER_RE = re.compile(r"\$\$[TR]\d+\$\$")


_ANY_PLACEHOLDER_RE = re.compile(r"\$\$[A-Za-z0-9_]+\$\$")


_RUNTIME_TOKEN_RE = re.compile(
    r"(\$\$[A-Za-z0-9_]+\$\$"          # $$ACTION$$
    r"|\$[A-Za-z_][A-Za-z0-9_]*"        # $level / $name
    r"|%c\[[^\]]*\]"                    # %c[255,1,255,255]
    r"|%[-+ #0-9.*$]*[A-Za-z]"          # %s / %d / %1$s
    r"|\b[rsg][0-9]?_[a-z0-9_]+\b)")    # r2_details_opt / s_light 等技术后缀


_MASK_RESIDUE_RE = re.compile(r"\$\$R\d+\$\$")

# ★ 小模型代码端加固：提示词"只输出译文本身"守不住——小模型常给输出加壳
# （代码栅栏、"译文："前缀、整串引号包裹）。壳不剥，占位符/术语闸门会把
# $$R0$$ 连壳一起判失败，或让译文带栅栏直接落盘。这里在**拿到响应的第一
# 时刻**做确定性剥离，属于不可协商的代码级闸门，不依赖模型自觉。
_FENCE_RE = re.compile(
    r"^\s*```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)[ \t]*\r?\n?```\s*$", re.DOTALL)
_LABEL_RE = re.compile(
    r"^\s*(?:译文|翻译|翻译结果|结果|输出|译文如下|翻译如下|以下是译文|"
    r"以下是翻译|翻译结果如下|"
    r"Translation(?: result| output)?|Output|"
    r"Here(?:'s| is)(?: the)? translation(?: of[^:：\n]{0,40})?)\s*[:：]\s*",
    re.IGNORECASE)
_WRAP_PAIRS = {"“": "”", "«": "»", "「": "」", "『": "』",
               '"': '"', "'": "'"}


def _quote_class(ch):
    """把各语言引号归到同一类，用于"源文本来就带引号"的判定。"""
    if ch in "“”\"«»「」『』":
        return "dquote"
    if ch in "‘’'":
        return "squote"
    return ""


def strip_output_wrappers(text, source=""):
    """剥离小模型在译文外常加的确定性外壳，返回 (cleaned, notes)。

    只做零风险剥离：
      1. 整体代码栅栏 ```...```（含语言标注行）；
      2. 开头的"译文：/Translation:"类标签（最多两层）；
      3. 整串被一对引号包裹且**源文并非以同类引号开头**时剥引号
         （源文本身带引号的对话——«Привет»、"你好"——不受影响）。
    不做内容级改写——拿不准的原文一律原样返回（notes 为空）。
    """
    if not text:
        return text, []
    cleaned = text
    notes = []
    fence = _FENCE_RE.match(cleaned)
    if fence:
        cleaned = fence.group(1)
        notes.append("fence")
    for _ in range(2):
        stripped = _LABEL_RE.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
        notes.append("label")
    if len(cleaned) >= 2:
        first = cleaned[0]
        closing = _WRAP_PAIRS.get(first)
        if closing and cleaned[-1] == closing:
            if _quote_class((source or "").strip()[:1]) != _quote_class(first):
                cleaned = cleaned[1:-1]
                notes.append("quote")
    return cleaned, notes


def budget_max_tokens(src_chars, ctx_size):
    """★ 2.0.13 按本次输入长度估算输出 token 预算（通用，不随 MOD 失效）。

    为什么不用固定 1536：别的 MOD 若有接近 safe_chars 的长段，译文可能需要
    上千 token，固定值触发 finish_reason=length 截断→递归二分重试，反而更慢。
    为什么不用 ctx*0.6：那与本次输入无关，短句也虚设 4915，放大失控生成。

    正解是看本次要翻多少字：中文输出 token 上界约源字符数的 1.2 倍
    （汉字约 1 token/字，加标点/空格/换行余量），另加 256 缓冲；下限 1024
    覆盖实测最大输出（940），上限不超过窗口 0.6（与旧行为一致的硬顶）。
    """
    est = int(src_chars * 1.2) + 256
    return max(1024, min(est, int(ctx_size * 0.6)))


def mask_runtime_tokens(text):
    """把运行期占位符换成 $$R0$$…，返回 (masked, mapping)。"""
    mapping = {}

    def repl(m):
        ph = "$$R%d$$" % len(mapping)
        mapping[ph] = m.group(0)
        return ph

    return _RUNTIME_TOKEN_RE.sub(repl, text), mapping


def restore_runtime_tokens(text, mapping):
    """回填占位符；任何一个不是恰好出现一次就返回 None（调用方保留原文）。"""
    for ph in mapping:
        if text.count(ph) != 1:
            return None
    for ph, tok in mapping.items():
        text = text.replace(ph, tok)
    return text


def runtime_token_counts(text):
    return Counter(_RUNTIME_TOKEN_RE.findall(text or ""))


def runtime_tokens_match(source, output):
    return (_RUNTIME_TOKEN_RE.findall(source or "")
            == _RUNTIME_TOKEN_RE.findall(output or ""))


def partial_source_review(source, output):
    """返回疑似只翻了一部分的源文词；仅作人工复核，不自动回退。"""
    if not has_cjk(output or ""):
        return []
    src = _RUNTIME_TOKEN_RE.sub(" ", source or "")
    src = re.sub(r"\b[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\b", " ", src)
    words = set(re.findall(
        r"[A-Za-z]{2,}|[А-Яа-яЁё]{2,}", src.lower()))
    if len(words) < 2:
        return []
    dst = (output or "").lower()
    retained = {word for word in words
                if re.search(r"(?<![a-zа-яё])" + re.escape(word)
                             + r"(?![a-zа-яё])", dst)}
    if not retained:
        return []
    return sorted(words - retained)


def semantic_role_issues(source, output, lang=""):
    src = re.sub(r"\s+", " ", source or "").strip()
    dst = re.sub(r"\s+", " ", output or "").strip()
    if not src or not dst:
        return []
    low = src.lower()
    issues = []
    src_first = bool(re.search(
        r"(?<![a-zа-яё])(?:i|me|my|mine|we|us|our|я|мне|меня|мой|мы|нам|нас)"
        r"(?![a-zа-яё])", low))
    src_second = bool(re.search(
        r"(?<![a-zа-яё])(?:you|your|yours|ты|тебе|тебя|твой|вы|вам|вас)"
        r"(?![a-zа-яё])", low))
    dst_first = bool(re.search(r"我|我们|咱", dst))
    dst_second = bool(re.search(r"你|你们|您", dst))
    if src_first and not src_second and dst_second and not dst_first:
        issues.append("人称从第一方反转成第二方")
    if src_second and not src_first and dst_first and not dst_second:
        issues.append("人称从第二方反转成第一方")

    def _direction(text, language):
        s = text.lower()
        if language == "eng" or re.search(r"[a-z]", s):
            if re.search(r"\b(?:i|we)(?:['’]ll|\s+will)?\s+"
                         r"(?:pay|give|bring|hand)\s+you\b", s):
                return "1_to_2"
            if re.search(r"\byou\s+(?:(?:must|should|need to|have to)\s+)?"
                         r"(?:pay|give|bring|hand)\s+(?:me|us)\b", s):
                return "2_to_1"
            if re.search(r"\b(?:please\s+)?(?:pay|give|bring|hand)\s+"
                         r"(?:me|us)\b", s):
                return "2_to_1"
        if re.search(r"\bя\s+(?:(?:должен|должна|должно)\s+)?"
                     r"(?:заплачу|заплатить|дам|дать|принесу|принести|"
                     r"отдам|отдать)\s+тебе\b", s):
            return "1_to_2"
        if re.search(r"\bты\s+(?:(?:должен|должна|должно|должны)\s+)?"
                     r"(?:заплатишь|заплатить|дашь|дать|принесешь|принести|"
                     r"отдашь|отдать)\s+мне\b", s):
            return "2_to_1"
        if re.search(r"\b(?:отнеси|принеси|отдай)\s+мне\b", s):
            return "2_to_1"
        if re.search(r"我(?:会|将|要)?(?:付给|交给|带给)你", text):
            return "1_to_2"
        if re.search(r"你(?:必须|需要|要)?(?:付给我|交给我|带给我)", text):
            return "2_to_1"
        if re.search(r"(?:付给我|交给我|带给我)", text):
            return "2_to_1"
        return ""

    src_dir = _direction(src, lang)
    dst_dir = _direction(dst, "zh")
    if src_dir and dst_dir and src_dir != dst_dir:
        issues.append("交接/付款方向反转")
    return issues


_GLUE_STRIP_RE = re.compile(
    r"\$\$R\d+\$\$|%c\[[^\]]*\]|\$clr_\d+|\$[A-Za-z_][A-Za-z0-9_]*"
    r"|%[-+ #0-9.*$]*[A-Za-z]|\\n")


_GLUE_RE = re.compile(
    r"[\u4e00-\u9fff][A-Za-z][a-z]+|[A-Za-z][a-z]+[\u4e00-\u9fff]")


def find_glue_residue(text):
    """返回译文里的粘连残词样例（最多 3 个）；无粘连返回 []。

    数字保留在探针里：单位记法（45mm弹壳、125g药剂）的字母串前面
    必然是数字，按"字母串紧邻数字"跳过——这是度量单位不是粘连。
    """
    if not text:
        return []
    probe = _GLUE_STRIP_RE.sub(" ", text)
    hits = []
    for mm in _GLUE_RE.finditer(probe):
        pre = probe[:mm.start()]
        if re.search(r"[\d.]\s*$", pre):
            continue
        hits.append(mm.group(0))
    return hits[:3]


_BULLET = "\u2022"


def clean_injected_bullets(src, out):
    """源文无圆点而译文有 → 判定为模型注入，删除所有圆点并整理空格。

    保留 round-trip 安全性：只删圆点本身与其后紧邻的一个空格，
    不触碰颜色码/占位符/换行，返回 (清洗后文本, 是否改动)。
    """
    if not out or _BULLET not in out:
        return out, False
    if _BULLET in (src or ""):
        return out, False   # 源文本来就有圆点，可能是合法列表，不动
    cleaned = out
    # "• " / "•" 连同其后紧邻空白一起删（颜色码后常见 %c[...]• %c[...]）
    cleaned = re.sub(re.escape(_BULLET) + r"[ \t]?", "", cleaned)
    # 删完后若颜色码之间留下双空格，收敛为一个（源文该处本无空格）
    cleaned = re.sub(r"(%c\[[^\]]*\])([ \t]{2,})", r"\1 ", cleaned)
    return cleaned, cleaned != out


_LEADING_ARTICLE_RE = re.compile(
    r"(%c\[[^\]]*\]|^)(?:An|A|The)(?=[\u4e00-\u9fff])", re.I)


def clean_stuck_article(out):
    """剥离紧贴中文译文的英文冠词（行首/颜色码后），返回 (文本, 是否改动)。"""
    if not out:
        return out, False
    cleaned = _LEADING_ARTICLE_RE.sub(lambda m: m.group(1) or "", out,
                                      count=1)
    return cleaned, cleaned != out


def unescape_newlines(text):
    """送翻前把字面 \\n/\\t 变成真实换行，让模型看到段落结构。"""
    return (text or "").replace("\\r\\n", "\n").replace("\\n", "\n") \
        .replace("\\t", "\t")


def escape_newlines(text):
    """译后统一写回字面 \\n/\\t，保持 X-Ray 文本表的存储约定。"""
    s = (text or "").replace("\r\n", "\n")
    return s.replace("\n", "\\n").replace("\t", "\\t")


def restore_linebreaks(text, count):
    """模型改动换行数时的确定性兜底：把译文的换行数对齐到原文。

    ★ 2.0.13：旧版只在译文**一个换行都没有**时才补，实测大量
    “原文 10 行 → 译文 8 行”的部分丢失被当成 kept_first 原样写回，
    游戏里表现为整段错行（用户实测百科/描述条目）。新逻辑同时处理
    换行不足（按句末/从句标点补）和换行过多（把多余换行并回相邻行），
    且只在缺/多的量不大（≤ 原文行数）时动手，避免把模型正常分行打乱。
    """
    if count <= 0 or not text:
        return text
    cur = text.count("\n")
    if cur == count:
        return text
    # 差异过大时不动手：原文 3 行译文 30 行属于模型自由分段，
    # 强行对齐只会破坏本就可读的译文。
    if abs(cur - count) > max(count, 1):
        return text

    if cur < count:
        # 缺行：优先按句末标点切，不够再退到从句标点。
        parts = [p for p in re.split(r"(?<=[。！？!?…])", text) if p]
        if len(parts) < count + 1:
            parts = [p for p in
                     re.split(r"(?<=[。！？!?…；;，,、])", text) if p]
        if len(parts) < 2:
            return text
        need = min(count, len(parts) - 1)
        out = []
        for i, part in enumerate(parts[:-1]):
            out.append(part + ("\n" if i < need else ""))
        out.append(parts[-1])
        fixed = "".join(out)
        return fixed if fixed.count("\n") == count else text

    # 多行：把多余的换行符号并回（删除多余 \\n，保留非空行内容）。
    lines = text.split("\n")
    keep = []
    for ln in lines:
        if len(keep) < count + 1:
            keep.append(ln)
        elif ln.strip():
            # 已够行数又有正文：并入最后一行，不丢字。
            keep[-1] = keep[-1].rstrip() + ln
        # 纯空行直接丢弃
    fixed = "\n".join(keep)
    # 行数还多（原文就是空行多）：从尾部合并空行
    while fixed.count("\n") > count:
        idx = fixed.find("\n")
        # 优先合并连续空行，其次合并任意相邻换行
        m = re.search(r"\n\s*\n", fixed)
        if m:
            fixed = fixed[:m.start()] + "\n" + fixed[m.end():]
        else:
            fixed = fixed.replace("\n", "", 1)
    return fixed if fixed.count("\n") == count else text


_PROTECT_FILE_HINTS = ("credits", "ogg", "music", "songs", "enc_weapons",
                       "snames", "fnames", "character_name")


_TITLE_LIKE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 .\-'/]{0,40}[:：]\s*[A-Za-z0-9]")


_TITLE_DASH_RE = re.compile(
    r"^[A-Za-z0-9][^\n.!?！？—–]{0,40}[—–]\s*"
    r"[A-Za-z0-9][^\n.!?！？]{0,40}$")


_MODEL_LIKE_RE = re.compile(
    r'^(?:[A-Z0-9][A-Za-z0-9\-]*[ .\-/]?){1,5}["“][^"”]{1,80}["”]')


_CODE_LIKE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .\-/_]{1,30}$")


_COPYRIGHT_RE = re.compile(r"^(?:©|\(c\)|copyright\b)", re.I)


_DEBUG_LIKE_RE = re.compile(
    r"(?:onItemDrop|npcObj|\bFAILED\b|sect\s*-|obj\s*:|gvid\s*:|"
    r"^(?:~|(?i:\[[a-z0-9_:.\- ]+\]$))|"
    r"\bteleport_[a-z0-9_]+|\bteleport\s*[:=]|"
    r"\b(?:reinit|kamps|npcItems|isVisual\w*)\b|"
    r"content\s*:)")


_DEBUG_PHRASES = {"dead", "none", "null", "true", "false",
                  "avail end", "start here", "reinit timer"}


_TECH_SPEC_RE = re.compile(
    r"^\s*(?:\d+\s*[x×]\s*)?\d+(?:[.,]\d+)?\s*[x×]\s*"
    r"\d+(?:[.,]\d+)?(?:\s*[A-Za-z0-9.+/\-]{0,12})?\s*$", re.I)


_TECH_CALIBER_RE = re.compile(
    r"^\s*\.\d{2,3}\s+[A-Za-z][A-Za-z0-9 .\-/]{0,24}$", re.I)


def looks_like_technical_text(text):
    """口径/弹药/尺寸类整串：只做技术串保护，不当普通正文。

    ★ 2.0.21（全 MOD 重放审计）：含**句末标点**的串是正文（实测 NS7
    'Магазин на 10 патронов калибра 10.3x45мм.'——弹匣描述带口径被整条
    跳过），尾部句号/问号排除。
    """
    s = str(text or "").strip()
    if not s or len(s) > 60 or re.search(r"[。！？!?；;]", s):
        return False
    if re.search(r"[.。！？!?]\s*$", s) and len(s) > 20:
        return False                     # 句末标点 = 句子，非纯规格串
    if _TECH_SPEC_RE.match(s) or _TECH_CALIBER_RE.match(s):
        return True
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?", s))


def looks_like_generated_noise(rel, text):
    """旧的 ___scriptE__ / ___cfgxml__ 生成表：短 ASCII 多是日志/键名。"""
    low = (rel or "").lower().replace("\\", "/")
    if "___scripte__" not in low and "___cfgxml__" not in low:
        return False
    s = str(text or "").strip()
    if not s or len(s) > 80 or re.search(r"[。！？!?；;]", s):
        return False
    if s.isascii():
        return True
    return bool(re.search(
        r"^(?:gulag|ignored|selected|actor\s+(?:start|end)|spawn|id):",
        s, re.I))


def looks_like_model_name(text):
    """纯拉丁武器/物品型号名：会话词表不应把它们当人名/普通物品翻译。"""
    s = str(text or "").strip()
    if not s or not re.search(r"[A-Za-z]", s):
        return False
    if re.search(r"\d", s) or re.search(r"[“”\"'«»/]", s):
        return True
    if re.search(r"[A-Z]{2,}", s):
        return True
    if "-" in s and len(s.split()) >= 2:
        return True
    caps = sum(1 for w in s.split() if w[:1].isupper())
    return s.isascii() and caps >= 2


def _has_translatable_text(text):
    """剔除运行期占位符后仍有 2 个以上连续字母，才值得送翻。"""
    clean = _RUNTIME_TOKEN_RE.sub("", text or "")
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]{2,}", clean))


def detect_text_lang(text):
    """按字母构成判断单条源文是俄语还是英语。

    X-Ray 的 `<rus>` 字段在不少 MOD 里实际放的是英语；只按字段名选源语言
    会把英语提示词成俄语。这里只做粗判：西里尔字母占优→rus，拉丁占优→eng；
    混合专名/缩写不改变主语言；太短（<2 个字母）返回空，交给文件级提示。
    """
    s = text or ""
    cyr = len(re.findall(r"[А-Яа-яЁё]", s))
    lat = len(re.findall(r"[A-Za-z]", s))
    if cyr >= 2 and cyr >= lat:
        return "rus"
    if lat >= 3 and lat > cyr:
        return "eng"
    return ""


def looks_like_code_or_debug(rel, text):
    """结构性噪声：调试日志/路径/URL/代号。**总是**跳过，不占“专名保护”。"""
    s = (text or "").strip()
    if not s or len(s) <= 1:
        return True
    if s.lower() in _DEBUG_PHRASES or _COPYRIGHT_RE.match(s):
        return True
    if _DEBUG_LIKE_RE.search(s):
        # ★ 2.0.11 [Say nothing] / [Issue Command] 是对话选项，不是调试标记。
        # 只在玩家可见标签表豁免，且方括号内必须是可读英文短语（含空格）。
        if not (is_player_facing_label(rel, s)
                and re.fullmatch(r"\[[A-Za-z][A-Za-z ]{2,60}\]", s)):
            return True
    if s.lower().startswith(("http://", "https://", "www.")):
        return True
    # 短代号/弹药规格：只有“全大写且没有任何小写字母”才跳过。
    # 旧规则把“Набор 1 / Патроны 9х18мм ПМ / 30 х 5.45x39мм”都当型号，
    # 实测 459 条正文因此漏译；含小写词的串一律按正文送翻。
    name_file = any(x in (rel or "").lower() for x in (
        "character_name", "snames", "fnames", "bio"))
    zone_file = any(x in (rel or "").lower() for x in (
        "string_table_enc_zone", "string_table_levels", "mapspots"))
    if not (name_file or zone_file):
        if (any(c.isdigit() for c in s)
                and re.fullmatch(
                    r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9 .\-/_]{0,40}", s)):
            return True
        if _CODE_LIKE_RE.match(s) and any(c.isdigit() for c in s):
            return True
    return False


def _is_model_code_form(rel, text):
    """code 过滤里"全大写型号/弹药规格"那一支是否为命中原因。

    ★ 2.0.22（每 MOD 随机文件测试）：全库 398 条被 code 拦掉的条目其实
    词库有整串中文译名，其中 121 条与人工汉化逐字一致（AK-74→AK-74突击步枪、
    AEK-971→AEK-971突击步枪、3D PDA→三维PDA、АН-94→AN-94突击步枪）。
    按 2.0.21 同一原则（词库整串命中 = 明确翻译意图）放行这一支；
    调试词/URL/版权行/单字符（dead、True、None、ч）不是型号形态，仍跳过。
    """
    s = (text or "").strip()
    if len(s) <= 1 or s.lower() in _DEBUG_PHRASES or _COPYRIGHT_RE.match(s):
        return False
    if _DEBUG_LIKE_RE.search(s):
        return False
    if s.lower().startswith(("http://", "https://", "www.")):
        return False
    low = (rel or "").lower()
    if any(x in low for x in ("character_name", "snames", "fnames", "bio",
                              "string_table_enc_zone", "string_table_levels",
                              "mapspots")):
        return False
    return bool(any(c.isdigit() for c in s)
                and re.fullmatch(r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9 .\-/_]{0,40}", s))


def looks_like_identifier_text(text):
    """纯小写 ASCII 的“段落/任务/节名”不是用户正文（如 resque information）。"""
    s = (text or "").strip()
    if not s:
        return True
    if re.fullmatch(r"[a-z0-9_.\- /]{1,90}", s) and not re.search(
            r"[.!?,;:()\[\]{}]", s):
        return True
    # 纯 ASCII、无句末标点、<=6 个词：多为任务/段落/角色/资源名
    # （Courier document / My map task / Gans Wiberg / Karta Name），
    # 不是用户正文；但「Disable Info」这类多个首字母大写的界面标签
    # 是给玩家看的，不能当标识符跳过。
    if (s.isascii() and len(s.split()) <= 6
            and not re.search(r"[.!?。！？]", s)):
        caps = sum(1 for w in s.split() if w[:1].isupper())
        if caps <= 1:
            return True
    return False


_PLAYER_LABEL_DIRS = ("achievement", "game_relations", "binoc",
                      "dialog", "relation", "reputation", "rank",
                      "quick_menu", "companion",
                      # ★ 2.0.21（全 MOD 重放审计）：以下目录的短串是
                      # 玩家可见正文，实测被 identifier 误跳（WOT 武器
                      # 升级名 'Synthetic barrel thickener'、新闻碎片
                      # 'a merc'、UI 'Dismissable'/'March'）
                      "news", "dynamic_news", "pda", "options",
                      "equipment", "quests", "loadscreen", "land_names",
                      "weapons_upgrades", "upgrades", "barter", "cryray",
                      # ★ 2.0.22：藏宝点/储物点名是 PDA 里玩家可见正文，
                      # 实测 679 条被 identifier 跳过（'Backpack under tree'、
                      # 'Chest on the roof'、'Safe under the bush'）。此前被
                      # 软层直取掩盖，软层退役后缺口暴露。
                      "treasure", "secrets")


_UPPER_LABEL_DIRS = ("achievement", "game_relations", "relation",
                     "reputation", "rank", "quick_menu", "companion",
                     "dialog",
                     # ★ 2.0.21：新闻/任务/加载屏的全大写标题是玩家正文
                     # （'WHO TOOK MY STASH?!!!'、'NIMBLE'S HANDBOOK'）
                     "news", "dynamic_news", "quests", "loadscreen")


def is_player_facing_label(rel, text=""):
    low = (rel or "").lower().replace("\\", "/")
    if not any(x in low for x in _PLAYER_LABEL_DIRS):
        return False
    s = (text or "").strip()
    # 真对话长句本来就走正常翻译路径，这里只豁免短标签；
    # 长度上限与 identifier 判定保持一致，避免把整段对话误判成标签。
    return bool(s) and len(s) <= 90


_PLAYER_ID_SUFFIX_RE = re.compile(
    r"(?:_|^)(?:name|descr|description|text|title|hint|info|tips|caption)"
    r"(?:_?\d+)?$",
    re.I)


def _id_player_visible(eid):
    """条目 id 以玩家可见字段后缀结尾 → 不按 identifier/technical 跳过。"""
    return bool(eid) and bool(_PLAYER_ID_SUFFIX_RE.search(str(eid)))


def _is_prose_sentence(s):
    """整句正文（不是标题/型号名）。

    ★ 2.0.22（每 MOD 随机文件测试 + 人工参考对照）：2.0.21 的注释声称
    _TITLE_LIKE/_TITLE_DASH 已"长句收口"，但代码只对 _MODEL_LIKE 生效，
    标题分支从未收口——全库 23 条正文被当标题跳过，人工汉化全部翻译：
      `Connection error: CD-Key already in use.`（GAMMA：连接错误: CD-Key已被使用.）
      `Stash: blocked entrance to the underground, inside an L-shaped pipe.`
      `06:00 \\nЭх... Уже пора вставать...`（时间戳前缀被当标签）
    真标题（`Vano:`、`MP5 — Sanitizer`）短且无句末标点，不受影响。
    """
    n = len(s)
    if n >= 40 and re.search(r"[.!?。！？]\s*[\"”»']?\s*$", s):
        return True
    return n > 90 and len(re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", s)) >= 8


_TRACK_LIST_RE = re.compile(r"(?:ТРЕК|Трек|TRACK|Track|曲目)\s*[:：]?\s*\d+",
                            re.I)


def looks_like_hard_protected_file(rel):
    """制作名单/音乐列表：连核心术语也不替换；武器短名只保护非词库型号。"""
    low = (rel or "").lower().replace("\\", "/")
    return any(x in low for x in ("credits", "ogg", "music", "songs"))


def looks_like_proper_text(rel, text):
    """专名/歌名/型号：只在用户勾选“保护专名”时跳过。"""
    s = (text or "").strip()
    if not s:
        return False
    low = (rel or "").lower().replace("\\", "/")
    # 文件级强保护：制作名单、音乐/歌曲列表整张表都不翻；人物/武器/名字表
    # 只保护短名，长描述仍交给模型（例如武器说明）。
    hint = next((h for h in _PROTECT_FILE_HINTS if h in low), "")
    if looks_like_hard_protected_file(rel):
        # ★ 2.0.22（全 MOD 全文件测试）："ogg/music" 命中的其实是物品表——
        # `string_table_ogg_player.xml` 是 MP3 播放器与音乐闪存盘的名字/描述，
        # 玩家在物品栏就能看到，人工汉化对长描述全部翻译。只放行这类长正文；
        # 制作名单与人物名字表仍整表保护。
        # 但**含曲目列表的条目不放行**：ogg_flash_* 的描述后面跟着
        # `ТРЕК 01: Алиса — Веретено` 这类真实歌名/歌手（全库 54 条），
        # 整串送翻会把歌名译错，人工也只译了前半句描述——宁可整条保留。
        if (hint in ("ogg", "music", "songs") and "credits" not in low
                and _is_prose_sentence(s) and not _TRACK_LIST_RE.search(s)):
            return False
        return True
    if hint == "enc_weapons" and len(s) <= 40 and len(s.split()) <= 6:
        return True
    # ★ 2.0.21（全 MOD 重放审计）：_MODEL_LIKE 只对短串生效——长正文
    # （The "underwear" of an exoskeleton...）引号紧跟 Title 词也会命中
    # 型号形态，实测整段物品描述被跳过。>90 字符或含句末标点不判型号。
    # ★ _TITLE_LIKE/_TITLE_DASH 同口径收口：长句不是标题（'The situation
    # is this: ...' 实测整段漏译）；玩家可见目录（quests/news/dialog 等）
    # 的冒号标题是任务名/新闻标题，送翻不保护。
    _label_dir = any(x in low for x in _PLAYER_LABEL_DIRS)
    if (not _label_dir and not _is_prose_sentence(s)
            and (_TITLE_LIKE_RE.match(s) or _TITLE_DASH_RE.match(s)
                 or (_MODEL_LIKE_RE.match(s) and len(s) <= 90
                     and not re.search(r"[.!?。！？]\s*[\"”»]?\s*$", s)))):
        return True
    if len(s) <= 30 and s.isupper() and any(c.isalpha() for c in s):
        # ★ 2.0.7：只保护拉丁字母的型号/缩写（NVIDIA DLSS、AK-74）。
        # 西里尔全大写短名（БАРМЕН、ПРИЗРАК、ЗВЕРОБОЙ…）在人物表/SMS
        # 名字表里是**普通身份称呼**，旧规则把它们整批原样保留、连模型
        # 都不调用。这里放行为"不自动保护"：调用方先用 termbase_lookup
        # 取库里既有译名，没有的送模型翻译。
        if re.search(r"[A-Za-z]", s):
            # ★ 2.0.12：玩家可见的全大写命令/菜单项（FOLLOW ME、WAIT HERE、
            # DO NOT LOOT CORPSES、S.C.H.O.L.A.R. 成就名）是**正文**，不是
            # 型号缩写。目录白名单命中时放行送翻；NVIDIA DLSS、AK-74 等
            # 真型号不在这些目录，仍受保护。
            low2 = low.lower().replace("\\", "/")
            if any(x in low2 for x in _UPPER_LABEL_DIRS) and len(s) <= 30:
                return False
            return True
        return False
    return False


def looks_like_protected_text(rel, text):
    """兼容旧接口：结构性噪声或专名。"""
    return (looks_like_code_or_debug(rel, text)
            or looks_like_proper_text(rel, text))


_DUP_FUNCTION_RE = re.compile(r"(有有|的的|了了|是是|在在)")


def clean_translation_output(text):
    """清掉模型常见的功能词重复（有有/的的/了了…），不动正常叠词。"""
    if not text:
        return text
    return _DUP_FUNCTION_RE.sub(lambda m: m.group(1)[0], text)


_STYLE_REPLACEMENTS = (
    ("我不得不说", "说实话"),
    ("就目前而言", "现在"),
    ("就目前来看", "现在看"),
    ("并且", "而且"),
    ("此外", "还有"),
    ("然而", "可"),
    ("因此", "所以"),
    ("进行了一次", "做了一次"),
    ("作出了", "做了"),
    ("给予了", "给了"),
)


def style_cleanup_cn(text):
    """对话专用：保守去掉翻译腔词和高频空格，不改术语、数字、代码。"""
    if not text:
        return text
    out = text
    for old, new in _STYLE_REPLACEMENTS:
        out = out.replace(old, new)
    # 中文之间的半角空格；中文与中文标点之间的空格。
    out = re.sub(r"(?<=[一-鿿])[ 	]+(?=[一-鿿])", "", out)
    out = re.sub(r"[ 	]+(?=[，。！？；：、])", "", out)
    out = re.sub(r"(?<=[，。！？；：、])[ 	]+", "", out)
    # 口语里常见的三个点统一成中文省略号（代码/占位符已在上层排除）。
    out = out.replace("...", "……")
    return out


_PUNCT_HALF_TO_FULL = {",": "，", ";": "；", "!": "！", "?": "？", ":": "："}


_REPEATED_BANG_RE = re.compile(r"([！!?？])\1{2,}")


def normalize_cn_punctuation(text):
    """中文语境标点规范化（确定性）：半角句读转全角、叠用标点收敛。

    只在含中文的文本里做；用正则的上下文锚定避免误伤英文缩写、
    小数点和代码里的标点。不改术语/数字/占位符。
    """
    if not text or not has_cjk(text):
        return text, False
    out = text
    # 中文字符后紧跟的半角句读 → 全角（排除小数点：前面是数字时跳过）
    out = re.sub(r"(?<=[\u4e00-\u9fff]),", "，", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff]);", "；", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])!", "！", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])\?", "？", out)
    # 省略号：连续 3 个及以上半角点 → 中文省略号（前面是数字的不动）
    out = re.sub(r"(?<![\d])\.{3,}(?![\d])", "……", out)
    # 半角点跟在中文后（句末）→ 句号
    out = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=$|[^\dA-Za-z])", "。", out)
    # 叠用收敛：！！！→！！、？？？→？？
    prev = out
    out = _REPEATED_BANG_RE.sub(lambda m: m.group(1) * 2, out)
    # 已有全角省略号旁边多余的半角点（……. → ……）
    out = re.sub(r"…\.(?![.\d])", "…", out)
    # ★ 2.0.24（IWP 全量审计 F5 定量）：汉字之间的空格收敛。
    # 长文分段/模型风格会产出「强烈的 禁区 环境中」这类词间空格——
    # 中文没有词间空格，两侧都是汉字即可安全删除；占位符/色码/数字
    # 两侧必有一侧非汉字，不受影响（「12.3 米」「$start $mid」保留）。
    out = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", out)
    return out, out != prev or out != text


def count_literal_newlines(text):
    """统计文本里字面 ``\\n`` 转义序列的个数（不是真实换行符）。"""
    return (text or "").count("\\n")


def reconcile_literal_newlines(src, out):
    """译文字面 \\n 数与源文不符时的确定性兜底：按源文数量补齐/收敛。

    模型常把字面 \n 吞成空格（$clr_1A\\n$clr_2B→空格），或凭空加 \n\\n。
    只做数量对齐：缺则按句末/颜色码边界补回，多则收敛到源数量。
    差异过大（源 0 → 译 >2）交给 context_bleed 闸门处理，这里不动。
    """
    if not src or not out:
        return out, False
    want = count_literal_newlines(src)
    have = count_literal_newlines(out)
    if want == have:
        return out, False
    # 源无字面换行却冒出多个：多为串段，交给上层闸门，不在数量层硬删。
    # ★ 2.0.25（aera 全量审计）：实测 11 条「源 0 → 译 2」漏过串段闸门
    # 原样落盘（dm_anomalies_12 等）——模型平白加的断行在游戏里把整句
    # 拆碎。少量（≤2 个）时确定性收敛为空格；更多仍交串段闸门点名。
    if want == 0:
        if 0 < have <= 2:
            out2 = out.replace("\\n", " ")
            while "  " in out2:
                out2 = out2.replace("  ", " ")
            return out2, True
        return out, False
    # 源有换行、译文全丢或少了：在颜色码切换/句末处补回缺失数量。
    if have < want:
        need = want - have
        # 优先在 $clr_N 颜色码切换处补（动态新闻常是颜色分段）
        def _add_color(m):
            nonlocal need
            if need > 0:
                need -= 1
                return "\\n" + m.group(0)
            return m.group(0)
        out2 = re.sub(r"\$clr_\d+", _add_color, out)
        if need > 0:
            # 其次在句末标点后补
            def _add_sentence(m):
                nonlocal need
                if need > 0:
                    need -= 1
                    return m.group(0) + "\\n"
                return m.group(0)
            out2 = re.sub(r"(?<=[。！？!?…])", _add_sentence, out2)
        if need > 0:
            out2 = "\\n" * need + out2
        return out2, out2 != out
    # have > want：收敛多余的换行（删最靠后的空行/连续换行）。
    # 注意：re.sub 的替换串是模板，"\\n" 会被解释成真实换行——
    # 必须用 lambda 返回字面 \n，否则收敛反而制造真换行。
    out2 = out
    while count_literal_newlines(out2) > want:
        out2 = re.sub(r"\\n\\n", lambda _m: "\\n", out2, count=1)
        if "\\n\\n" not in out2:
            break
    return out2, out2 != out


_MODEL_TOKEN_RE = re.compile(r"[A-Za-z]{2,}-?\d{2,4}[A-Za-z]?")


_YEAR_RANGE_RE = re.compile(r"\b(\d{4})\s*[-‐‑‒–—]\s*(\d{4})\b")


def missing_model_tokens(src, out):
    """返回源文里「明确型号串」在译文中消失的列表（型号被换掉）。

    只取带引号或紧邻武器/物品语义的型号，避免把普通编号当型号。
    译文里以「译名 + 原型号」形式出现时算保留。
    """
    if not src or not out:
        return []
    missing = []
    seen = set()
    # ★ 2.0.21：色码 %c[r,g,b,a] 内数字不是型号，先剥离再扫（同
    # missing_big_numbers 的色码豁免口径）。
    src = re.sub(r"%c\[[^\]]*\]", " ", str(src))
    for m in _MODEL_TOKEN_RE.finditer(src):
        tok = m.group(0).strip()
        if tok in seen or len(tok) < 4:
            continue
        seen.add(tok)
        # 年份区间不算型号
        if re.fullmatch(r"\d+", tok):
            continue
        # ★ 2.0.24（IWP 全量审计 D2）：英文年代短语不是型号——实测
        # "mid-1990s" 被当型号，译文「1990年代中期」判缺失补出注记。
        if re.fullmatch(r"(?:early|mid|late)[-\s]?\d{4}s?", tok, re.I):
            continue
        # 型号需紧邻引号或武器语境词，减少误报。
        # ★ 2.0.18：语境窗口从 ±24 放宽到 ±48 + 全源兜底——实测武器描述
        # （"derivative of the AK-100 series from the AK-74M, chambered
        # for the M43 cartridge"）型号与语境词相距很远，窄窗口漏掉
        # AK-100→AK-74M 的型号替换。
        s, e = m.span()
        near = src[max(0, s - 48):min(len(src), e + 48)]
        if not (any(q in near for q in "\"'«“”")
                or re.search(r"rifle|pistol|gun|shotgun|carbine|weapon|ammo|"
                             r"cartridge|sniper|caliber|series|barrel|"
                             r"караб|пистол| оружи|патрон",
                             near if near else src, re.I)):
            continue
        if tok not in out:
            missing.append(tok)
    return missing


def repair_known_semantic_terms(src, out, lang=""):
    """英文源里两个已知语义错译的定点修复。

    Controller 在 STALKER 里是脑波怪，不是“控制器”；Freedom 是
    自由团，不是“自由派”。只在英文源、且源文确实出现该词时修，
    不改中文里正常的“控制器/自由”用法。
    """
    if not src or not out or lang != "eng":
        return out
    low = src.lower()
    if re.search(r"(?<![A-Za-z])controllers?(?![A-Za-z])", low):
        out = re.sub(r"控制者|控制器", "脑波怪", out)
    if re.search(r"(?<![A-Za-z])Freedom(?![A-Za-z])", src) or "freedom faction" in low:
        out = re.sub(r"自由派(?:团体|势力|派别|阵营|组织)?",
                     "“自由团”", out)
    return out


def entry_style(rel, src):
    low = (rel or "").lower().replace(os.sep, "/")
    if "dialog" in low:
        return "dialogue"
    if ("/ui/" in low or "ui_" in low or "items" in low):
        return "label"
    s = (src or "").strip()
    if len(s) <= 48 and not re.search(r"[.!?。！？；;:]", s):
        return "label"
    if ("encyclopedia" in low or "news" in low
            or "enc_" in low):
        return "formal"
    return "dialogue"


def scene_context(metas, idx):
    """取同表前后各 2 条源文作场景上下文（最多 240 字符）。"""
    parts = []
    for j in range(idx - 1, max(-1, idx - 3), -1):
        src = (metas[j].get("src") or "").strip()
        if src:
            parts.insert(0, "前一句：" + src)
    for j in range(idx + 1, min(len(metas), idx + 3)):
        src = (metas[j].get("src") or "").strip()
        if src:
            parts.append("后一句：" + src)
    text = "\\n".join(parts)
    return text[:240]


def parse_numbered_batch(out, n):
    found = {}
    for line in (out or "").splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s*\[(\d+)\]\s*(.*)$", line)
        if not m:
            return None
        i = int(m.group(1))
        if i < 1 or i > n or i in found:
            return None
        found[i] = m.group(2).strip()
    if len(found) != n or any(not found.get(i) for i in range(1, n + 1)):
        return None
    return [found[i] for i in range(1, n + 1)]


def temp_for_style(style, dialogue_temp=0.1):
    if style == "label":
        return 0.0
    if style == "formal":
        return 0.1
    return dialogue_temp


def fmt_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0
