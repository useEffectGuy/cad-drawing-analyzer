#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entity_pool —— L0 图元池与图层角色解析（唯一 DXF 图元读取入口）。

所有“按图层角色决定是否进入几何量算”的脚本都从这里取数，避免各脚本各写一套
图层判断。角色只决定是否量算，不改变图元坐标。

设计约束:
- knowledge/layer_standards/base.yaml 是图层角色唯一真相源，缺失即报错；
- UNKNOWN 图层图元不得静默丢弃，必须由调用方记入 unclassified；
- 本模块不 import 任何业务脚本。

CLI:
    python entity_pool.py <drawing.dxf>     # 打印图层角色与图元统计
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from functools import lru_cache

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
STANDARDS_PATH = os.path.join(ROOT_DIR, "knowledge", "layer_standards", "base.yaml")

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from cad_common import dxf_attr, normalize, round_num  # noqa: E402

ROLES = ("MEASURABLE", "ANNOTATION", "FRAME", "AXIS", "AUXILIARY", "FILL", "UNKNOWN")
# 只有 MEASURABLE 进入几何量算；UNKNOWN 单独归集，不静默丢弃
EXCLUDED_ROLES = ("FRAME", "AXIS", "ANNOTATION", "FILL", "AUXILIARY")


@lru_cache(maxsize=1)
def load_standards() -> dict:
    """加载图层角色库；缺失或结构错误直接抛错，不做内置兜底。"""
    if not os.path.isfile(STANDARDS_PATH):
        raise RuntimeError(f"图层角色库缺失：{STANDARDS_PATH}")
    import yaml

    with open(STANDARDS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"图层角色库格式错误（顶层应为字典）：{STANDARDS_PATH}")
    for key in ("priority", "roles", "measurable"):
        if key not in data:
            raise RuntimeError(f"图层角色库缺少键 {key}：{STANDARDS_PATH}")
    return data


@lru_cache(maxsize=4096)
def layer_role(layer: str) -> str:
    """返回图层角色：MEASURABLE / ANNOTATION / FRAME / AXIS / AUXILIARY / FILL / UNKNOWN。

    判定顺序：角色关键词优先（避免 A-FRAME 被当作构件），其次构件关键词。
    """
    up = normalize(layer)
    if not up:
        return "UNKNOWN"
    standards = load_standards()
    roles = standards.get("roles") or {}
    for role in standards.get("priority") or []:
        for kw in (roles.get(role) or {}).get("keywords") or []:
            token = normalize(kw)
            if token and token in up:
                return role
    for token in (standards.get("measurable") or {}).get("prefixes") or []:
        word = normalize(token)
        if word and word in up:
            return "MEASURABLE"
    return "UNKNOWN"


# ---------------------------------------------------------------- 包围盒
def _box(points) -> list | None:
    pts = [(float(p[0]), float(p[1])) for p in points if p is not None]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [round_num(min(xs)), round_num(min(ys)), round_num(max(xs)), round_num(max(ys))]


def bbox_of(entity) -> list | None:
    """返回 [xmin, ymin, xmax, ymax]（图纸原始单位）；无法确定时返回 None。"""
    t = entity.dxftype()
    try:
        if t == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            return _box([(s.x, s.y), (e.x, e.y)])
        if t in ("CIRCLE", "ARC"):
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            return _box([(c.x - r, c.y - r), (c.x + r, c.y + r)])
        if t == "LWPOLYLINE":
            return _box([(p[0], p[1]) for p in entity.get_points()])
        if t == "POLYLINE":
            return _box([(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices])
        if t == "INSERT":
            from ezdxf import bbox as ezbbox

            ext = ezbbox.extents([entity], fast=True)
            if ext.has_data:
                return _box([(ext.extmin.x, ext.extmin.y), (ext.extmax.x, ext.extmax.y)])
            ins = dxf_attr(entity, "insert", (0, 0, 0))
            return _box([(ins[0], ins[1])])
        if t in ("TEXT", "MTEXT"):
            ins = dxf_attr(entity, "insert", (0, 0, 0))
            h = float(dxf_attr(entity, "height", dxf_attr(entity, "char_height", 0)) or 0)
            if t == "MTEXT":
                text = entity.plain_text() if hasattr(entity, "plain_text") else ""
            else:
                text = dxf_attr(entity, "text", "") or ""
            w = max(h, len(text) * h * 0.7)
            return _box([(ins[0], ins[1]), (ins[0] + w, ins[1] + h)])
        if t == "DIMENSION":
            p = dxf_attr(entity, "defpoint", (0, 0, 0))
            return _box([(p[0], p[1])])
        if t == "HATCH":
            from ezdxf import bbox as ezbbox

            ext = ezbbox.extents([entity], fast=True)
            if ext.has_data:
                return _box([(ext.extmin.x, ext.extmin.y), (ext.extmax.x, ext.extmax.y)])
    except Exception:
        return None
    return None


def _is_closed_polygon(entity) -> bool:
    t = entity.dxftype()
    if t == "LWPOLYLINE":
        return bool(entity.closed)
    if t == "POLYLINE":
        return bool(getattr(entity, "is_closed", False))
    return False


# ---------------------------------------------------------------- 图元池
class DXFEntityPool:
    """模型空间图元池：一次读取，按角色分组，供量算/校准/CIO 共用。"""

    def __init__(self, source):
        import ezdxf

        if isinstance(source, str):
            self.path = source
            self.doc = ezdxf.readfile(source)
        else:
            self.path = None
            self.doc = source
        self.msp = self.doc.modelspace()
        self.entities = list(self.msp)

    def role(self, entity) -> str:
        return layer_role(dxf_attr(entity, "layer", "0"))

    def by_role(self, role: str) -> list:
        return [e for e in self.entities if self.role(e) == role]

    def layer_roles(self) -> dict:
        layers = sorted({dxf_attr(e, "layer", "0") for e in self.entities})
        return {layer: layer_role(layer) for layer in layers}

    def statistics(self) -> dict:
        counts = Counter()
        layers = defaultdict(set)
        for e in self.entities:
            role = self.role(e)
            counts[role] += 1
            layers[role].add(dxf_attr(e, "layer", "0"))
        return {
            "entity_count": len(self.entities),
            "role_counts": {r: counts[r] for r in ROLES if counts[r]},
            "role_layers": {r: sorted(layers[r]) for r in ROLES if layers[r]},
            "layer_roles": self.layer_roles(),
            "unclassified": {
                "entity_count": counts.get("UNKNOWN", 0),
                "layers": sorted(layers.get("UNKNOWN", ())),
            },
        }

    def frame_bbox(self) -> list | None:
        """图框包围盒：优先取面积最大的闭合多段线，否则取 FRAME 类图元并集。"""
        best, best_area = None, 0.0
        for e in self.entities:
            if self.role(e) != "FRAME" or not _is_closed_polygon(e):
                continue
            b = bbox_of(e)
            if not b:
                continue
            area = (b[2] - b[0]) * (b[3] - b[1])
            if area > best_area:
                best, best_area = b, area
        if best:
            return best
        boxes = [bbox_of(e) for e in self.entities if self.role(e) == "FRAME"]
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return _box([
            (min(b[0] for b in boxes), min(b[1] for b in boxes)),
            (max(b[2] for b in boxes), max(b[3] for b in boxes)),
        ])


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="图元池与图层角色统计")
    parser.add_argument("drawing", help="DXF 图纸路径")
    args = parser.parse_args(argv)
    pool = DXFEntityPool(args.drawing)
    print(json.dumps(pool.statistics(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
