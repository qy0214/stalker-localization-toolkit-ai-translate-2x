# -*- coding: utf-8 -*-
"""OGSR/X-Ray .db 归档索引解析器（按 xrCore/arc_db.cpp 格式）。

布局: 连续 chunk [u32 chunkID|压缩标记][u32 size][data]
chunk 1 = 索引（明文记录）:
    u16 rec_size, 然后 rec_size 字节:
    u32 size_real, u32 size_compr, u32 crc, name[rec_size-16], u32 ptr
文件数据在绝对偏移 ptr 处, 长度 size_compr,
size_real==size_compr 为明文, 否则 LZO1X 压缩。
"""
import struct
import sys


def parse_index(path):
    raw = open(path, "rb").read()
    pos = 0
    entries = []
    index_data = None
    while pos + 8 <= len(raw):
        cid, size = struct.unpack_from("<II", raw, pos)
        comp = bool(cid & 0x80000000)
        cid &= 0x7FFFFFFF
        body = raw[pos + 8:pos + 8 + size]
        pos += 8 + size
        if cid == 1:
            index_data = (body, comp)
            break
    if index_data is None:
        raise RuntimeError("index chunk not found")
    body, comp = index_data
    if comp:
        raise RuntimeError("index chunk is compressed — needs LZHUF decode")
    p = 0
    while p + 2 <= len(body):
        rec_size = struct.unpack_from("<H", body, p)[0]
        p += 2
        rec = body[p:p + rec_size]
        p += rec_size
        if len(rec) < 16:
            break
        size_real, size_compr, crc = struct.unpack_from("<III", rec, 0)
        name = rec[12:rec_size - 4].decode("cp1251", "replace").rstrip("\x00")
        (ptr,) = struct.unpack_from("<I", rec, rec_size - 4)
        entries.append((name, ptr, size_real, size_compr, crc))
    return raw, entries


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\game\OGSR_GA\OGSR Gunslinger Addon\gamedata\configs.db"
    raw, entries = parse_index(db)
    print("total files:", len(entries))
    texts = [e for e in entries if "text" in e[0].lower()]
    print("text files:", len(texts))
    comp = sum(1 for e in entries if e[2] != e[3])
    print("compressed files:", comp)
    for e in texts[:8]:
        print(e)
