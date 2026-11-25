# -*- coding: utf-8 -*-
"""从随包 xray_pkg.zip 加载共享 X-Ray 解析器。

来源（Source）: wzyddg/stalker_auto-trans-tool_remake
上游仓库: https://github.com/wzyddg/stalker_auto-trans-tool_remake (作者: wzyddg)
上游未声明许可证(license: null), 版权归原作者所有; 本仓库署名分发,
收录/修改明细见 THIRD_PARTY_NOTICES.md 第 1 节。
"""
from __future__ import annotations

import os
import sys
import threading
import types
import zipfile

_CACHE: dict[str, object] = {}
_LOCK = threading.Lock()


def load_vendor(zip_path: str):
    path = os.path.abspath(zip_path)
    with _LOCK:
        cached = _CACHE.get(path)
        if cached is not None:
            return cached
        if not os.path.isfile(path):
            raise FileNotFoundError("缺少解析器包：%s" % path)
        # xRayXmlParser: vendored from wzyddg/stalker_auto-trans-tool_remake
        # (https://github.com/wzyddg/stalker_auto-trans-tool_remake, license: null,
        #  见 THIRD_PARTY_NOTICES.md 第 1 节)
        package_name = "xRayXmlParser"
        package = types.ModuleType(package_name)
        package.__path__ = []
        package.__package__ = package_name
        sys.modules[package_name] = package
        order = ["entityDefinition", "xRayTextAnalyzer",
                 "xmlFileGenerator", "xmlxReader"]
        with zipfile.ZipFile(path) as archive:
            sources = {
                name: archive.read("%s/%s.py" % (package_name, name)).decode("utf-8")
                for name in order
            }
            init_source = archive.read("%s/__init__.py" % package_name).decode("utf-8")
        for name in order:
            module = types.ModuleType("%s.%s" % (package_name, name))
            module.__package__ = package_name
            sys.modules["%s.%s" % (package_name, name)] = module
            code = compile(sources[name], "%s!%s.py" % (path, name), "exec")
            exec(code, module.__dict__)
            setattr(package, name, module)
        code = compile(init_source, "%s!__init__.py" % path, "exec")
        exec(code, package.__dict__)
        _CACHE[path] = package
        return package
