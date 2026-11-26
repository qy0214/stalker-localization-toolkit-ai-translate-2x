# -*- coding: utf-8 -*-
"""worker 与插件之间的线路协议（v1）。

传输形态：worker 在 127.0.0.1 起一个仅本机可达的 stdlib HTTP JSON 服务，
插件以 HTTP 客户端身份轮询/提交——不引第三方依赖，不暴露外网。

握手：worker 启动后向 stdout 打印**恰好一行** JSON：
    {"protocol": 1, "service": "stalker_voice_core", "pid": 1234,
     "port": 54321, "version": "0.1.0"}
插件读完这一行即完成接线；此后 stdout 静默（日志走 /health 或 stderr）。

心跳语义：worker 内部心跳线程每 HEARTBEAT_SEC 触一次，任何请求也会刷新；
/health 返回 beat_age_sec，插件侧连续 MISS_LIMIT 次取不到 /health 即判死。

任务语义：POST /job 提交，GET /job/<id> 查询。本骨架版只实现 echo（链路
自证）；tts/extract/scan 为 reserved，worker 收到即回 501，前端显式报错，
不静默吞掉。
"""

import json

PROTOCOL_VERSION = 1
SERVICE_NAME = "stalker_voice_core"

HANDSHAKE_KEYS = ("protocol", "service", "pid", "port", "version")

HEARTBEAT_SEC = 1.0
MISS_LIMIT = 3  # 插件侧连续取不到 /health 的判定阈值


def handshake_line(pid, port, version):
    """构造握手行（含结尾换行）。"""
    return json.dumps({
        "protocol": PROTOCOL_VERSION,
        "service": SERVICE_NAME,
        "pid": int(pid),
        "port": int(port),
        "version": str(version),
    }, ensure_ascii=False) + "\n"


def parse_handshake(line):
    """解析并校验握手行；协议不符抛 ValueError。"""
    data = json.loads(line)
    for key in HANDSHAKE_KEYS:
        if key not in data:
            raise ValueError("握手缺少字段 %r：%r" % (key, line[:200]))
    if data["protocol"] != PROTOCOL_VERSION:
        raise ValueError("协议版本不符：worker=%r 宿主=%r"
                         % (data["protocol"], PROTOCOL_VERSION))
    if data["service"] != SERVICE_NAME:
        raise ValueError("服务名不符：%r" % data["service"])
    return data


# 任务类型注册表：implemented=True 的才会在 worker 里真正执行。
JOB_KINDS = {
    "echo":    {"implemented": True,  "desc": "链路自证：原样回显 payload"},
    "scan":    {"implemented": True,  "desc": "体检：survey+语音枚举+待解包清单（audio.scan_voice_sources）"},
    "extract": {"implemented": True,  "desc": "xrkit 引擎解包 → VOICE_SRC 镜像树（extract.extract_many）"},
    "text":    {"implemented": False, "desc": "三级文本链产出台词表（预留）"},
    "tts":     {"implemented": False, "desc": "Qwen3-TTS 批量合成（预留）"},
    "pack":    {"implemented": False, "desc": "后处理+组包 MODS/ZH_Voice_*（预留）"},
}


def job_supported(kind):
    return kind in JOB_KINDS and JOB_KINDS[kind]["implemented"]
