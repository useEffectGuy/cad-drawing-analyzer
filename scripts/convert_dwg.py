#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DWG → DXF 转换脚本（工作流步骤②：格式归一）—— 兼容薄封装

历史版本在本文件内独立实现了一套转换逻辑，与 cad_convert.py 完全重复。
现统一由 cad_convert.py 提供唯一实现，本文件仅保留原有 CLI 与导出符号，
保证 `from convert_dwg import ensure_dxf` 等既有调用方式继续可用。

用法:
    python convert_dwg.py --detect
    python convert_dwg.py <drawing.dwg> --out output.dxf
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cad_convert import (  # noqa: F401
    _find_libredwg,
    _find_oda_converter,
    convert_with_libredwg,
    convert_with_oda,
    detect_converter,
    ensure_dxf,
    main,
)

if __name__ == "__main__":
    main()
