#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cad_common —— 跨脚本公共基础设施。

只放无业务语义的代码：DXF 属性读取、数值/字符串归一、目录创建、DWG 输入归一、
规则文件定位与 YAML 加载、Excel 样式助手、stdout 输出分级。

设计约束：
- 本模块可被 scripts/ 下任意脚本 import；
- 不 import 任何业务脚本（ensure_dxf_input 内部延迟 import cad_convert，避免循环导入）；
- 所有函数保持与迁移前逐字等价的行为，默认值不得擅自更改。
"""
from __future__ import annotations

import argparse
import os
import sys
from functools import lru_cache

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)


# ---------------------------------------------------------------- DXF 工具
def dxf_attr(entity, name, default=None):
    """安全读取 entity.dxf.<name>。"""
    try:
        return entity.dxf.get(name, default)
    except Exception:
        return default


def round_num(v, n=3):
    """安全四舍五入。注意：measure_dxf 历史默认是 2，调用点需显式传 n=2。"""
    try:
        return round(float(v), n)
    except Exception:
        return v


def normalize(s) -> str:
    """去空白并转大写，用于关键词比对。"""
    return (s or "").strip().upper()


# ---------------------------------------------------------------- 路径 / 输入
def ensure_dir(file_path: str) -> None:
    """创建 file_path 的父目录（不存在则递归创建）。"""
    d = os.path.dirname(os.path.abspath(file_path))
    if d:
        os.makedirs(d, exist_ok=True)


def ensure_dxf_input(path: str, quiet: bool = False) -> str:
    """若输入是 DWG，自动转换为 DXF；转换失败直接退出（退出码 2）。"""
    if not path.lower().endswith(".dwg"):
        return path
    try:
        if SCRIPT_DIR not in sys.path:
            sys.path.insert(0, SCRIPT_DIR)
        from cad_convert import ensure_dxf
        if not quiet:
            print("[INFO] 检测到 DWG 文件，正在自动转换为 DXF...")
        dxf = ensure_dxf(path)
        if not quiet:
            print(f"[INFO] 转换完成: {dxf}")
        return dxf
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------- 规则文件
def rules_path(name: str) -> str:
    """返回 rules/ 下规则文件的绝对路径。"""
    return os.path.join(ROOT_DIR, "rules", name)


def load_yaml(path_or_name: str, required: bool = True) -> dict:
    """加载 YAML 规则文件；required=True 时缺失/损坏直接抛错，不做内置兜底。"""
    path = path_or_name if os.path.isabs(path_or_name) else rules_path(path_or_name)
    if not os.path.isfile(path):
        if required:
            raise RuntimeError(f"规则文件缺失：{path}")
        return {}
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError(f"缺少依赖 PyYAML，无法读取规则文件：{path}") from e
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        raise RuntimeError(f"规则文件损坏：{path}（{e}）") from e
    if required and not isinstance(data, dict):
        raise RuntimeError(f"规则文件格式错误（顶层应为字典）：{path}")
    return data or {}


# ---------------------------------------------------------------- 专业词典
class DisciplineDict:
    """专业识别词典访问器。数据唯一来源：rules/discipline_dict.yaml。"""

    def __init__(self, data: dict):
        self.data = data
        self.order = list(data.get("order", []))
        self.drawing_no_prefix = dict(data.get("drawing_no_prefix", {}))
        self.name_keywords = dict(data.get("name_keywords", {}))
        self._content_weights = dict(data.get("content_weights", {}))
        self.layer_keywords = dict(data.get("layer_keywords", {}))

    # 图号前缀（最长前缀优先，与改造前逐字一致）
    def by_drawing_no(self, drawing_no: str):
        if not drawing_no or drawing_no == "未识别":
            return None
        for prefix in sorted(self.drawing_no_prefix.keys(), key=len, reverse=True):
            if drawing_no.upper().startswith(prefix.upper()):
                return self.drawing_no_prefix[prefix]
        return None

    # 图名关键词（命中数最多者胜，平手取先出现者）
    def by_name(self, drawing_name: str):
        if not drawing_name:
            return None
        scores = {}
        for disc, keywords in self.name_keywords.items():
            score = sum(1 for kw in keywords if kw in drawing_name)
            if score > 0:
                scores[disc] = score
        if scores:
            return max(scores, key=scores.get)
        return None

    # 图层名弱判定（顺序即优先级，命中即返回）
    def by_layer(self, layer_name: str) -> str:
        up = normalize(layer_name)
        for disc, keywords in self.layer_keywords.items():
            if any(normalize(k) in up for k in keywords):
                return disc
        return ""

    # 内容加权投票词典（供 cad_analyze 使用）
    def weights(self) -> dict:
        return self._content_weights

    def order_index(self, discipline: str) -> int:
        try:
            return self.order.index(discipline)
        except ValueError:
            return len(self.order)


@lru_cache(maxsize=1)
def load_discipline_dict() -> DisciplineDict:
    """加载专业词典（缺失或损坏直接抛错，不做内置兜底）。"""
    return DisciplineDict(load_yaml("discipline_dict.yaml"))


@lru_cache(maxsize=1)
def load_gb50300_map() -> dict:
    """加载 WBS 映射（缺失或损坏直接抛错，不做内置兜底）。"""
    return load_yaml("gb50300_map.yaml")


def classify_layer(name: str) -> str:
    """按图层名弱判定专业；无命中返回空字符串。"""
    return load_discipline_dict().by_layer(name)


# ---------------------------------------------------------------- Excel 样式
HEADER_FILL_COLOR = "1F4E79"
BORDER_COLOR = "BFBFBF"
FONT_NAME = "微软雅黑"


def xlsx_new():
    """新建工作簿并移除默认 sheet。"""
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def xlsx_header(ws, ncol: int, wrap: bool = False):
    """美化表头（第 1 行）。"""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    header_font = Font(bold=True, color="FFFFFF", size=11, name=FONT_NAME)
    header_fill = PatternFill("solid", fgColor=HEADER_FILL_COLOR)
    thin = Side(style="thin", color=BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(1, ncol + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
        cell.border = border


def xlsx_body(ws, ncol: int = None, wrap: bool = False):
    """美化正文（第 2 行起）：加边框、垂直居中。"""
    from openpyxl.styles import Alignment, Border, Side
    thin = Side(style="thin", color=BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    body_align = Alignment(vertical="center", wrap_text=wrap)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
            cell.alignment = body_align


def xlsx_auto_width(ws, min_w: int = 8, max_w: int = 40):
    """按内容自动列宽（中文按 2 个字符宽度计）。"""
    from openpyxl.utils import get_column_letter
    for col in ws.columns:
        first = col[0]
        col_idx = getattr(first, "column", None)
        if col_idx is None:
            continue
        letter = get_column_letter(col_idx)
        maxlen = 0
        for cell in col:
            v = getattr(cell, "value", None)
            if v is not None:
                s = str(v)
                w = sum(2 if ord(c) > 127 else 1 for c in s)
                maxlen = max(maxlen, w)
        ws.column_dimensions[letter].width = min(max(maxlen + 2, min_w), max_w)


# ---------------------------------------------------------------- 输出分级
class Out:
    """stdout 分级输出。

    默认模式：`[OK] 步骤 摘要 → 产出路径` 一行；
    --quiet  ：仅输出产出路径；
    --verbose：额外输出明细（保持改造前行为）。
    """

    def __init__(self, quiet: bool = False, verbose: bool = False):
        self.quiet = quiet
        self.verbose = verbose

    @classmethod
    def from_args(cls, args) -> "Out":
        return cls(getattr(args, "quiet", False), getattr(args, "verbose", False))

    def ok(self, step: str, path: str = None, *summary):
        if self.quiet:
            if path:
                print(path)
            return
        line = f"[OK] {step}"
        if summary:
            line += "  " + "  ".join(str(s) for s in summary)
        if path:
            line += f"  → {path}"
        print(line)

    def detail(self, *lines):
        if not self.verbose:
            return
        for line in lines:
            print(line)

    def path_only(self, path: str):
        print(path)


def add_output_args(ap: argparse.ArgumentParser):
    """为 ArgumentParser 添加互斥的 --quiet / --verbose。"""
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quiet", action="store_true", help="仅输出产出文件路径")
    g.add_argument("--verbose", action="store_true", help="输出完整明细")
    return ap
