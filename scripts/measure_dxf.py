#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工程量量算脚本 —— 工作流步骤⑤工程量提取（DXF 几何量算通道）
用法:
    python measure_dxf.py <图纸.dxf> --out <输出目录>/quantities.json
功能:
    1. 封闭区域面积（闭合 LWPOLYLINE / HATCH）
    2. 线长统计（LINE / LWPOLYLINE / ARC / CIRCLE，按图层）
    3. 构件计数（INSERT 块参照，带属性块提取型号规格）
    4. 输出 quantities.json，供后续分部分项映射与汇总
设计说明:
    型号规格提取优先级：INSERT 的 ATTRIB(tag如MODEL/SPEC/NAME) > 块内TEXT/MTEXT > 图层名/块名关键字。
依赖: ezdxf, openpyxl
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from functools import partial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cad_common import Out, add_output_args  # noqa: E402
from cad_common import dxf_attr as _dxf_attr, round_num  # noqa: E402
from entity_pool import DXFEntityPool, EXCLUDED_ROLES  # noqa: E402
from unit_scale import calibrate  # noqa: E402

# measure_dxf 历史上保留 2 位小数，其余脚本为 3 位；此处显式固定，避免行为漂移
_round = partial(round_num, n=2)

try:
    import ezdxf
except ImportError:
    print("[ERROR] 缺少依赖 ezdxf，请先执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)

# 规格提取正则
SPEC_PATTERNS = {
    "diameter_dn": re.compile(r"DN\d+", re.IGNORECASE),
    "diameter_phi": re.compile(r"[ΦØ]\d+", re.IGNORECASE),
    "cable": re.compile(r"(YJV|BV|VV|KVV|ZR|NH)[\w\-*×/]*", re.IGNORECASE),
    "rebar": re.compile(r"[ΦØ]\d+[@]\d+", re.IGNORECASE),
}

def _extract_spec_from_attrib(insert) -> str:
    """从 INSERT 块属性中提取型号规格。"""
    spec_parts = []
    try:
        if hasattr(insert, "attribs") and insert.attribs:
            for a in insert.attribs:
                tag = (a.dxf.tag or "").upper()
                text = (a.dxf.text or "").strip()
                if not text:
                    continue
                if any(k in tag for k in ("MODEL", "SPEC", "NAME", "型号", "规格", "编号")):
                    spec_parts.append(text)
                # 检查文本是否含规格特征
                for pat in SPEC_PATTERNS.values():
                    if pat.search(text):
                        spec_parts.append(text)
                        break
    except Exception:
        pass
    return " / ".join(dict.fromkeys(spec_parts)) if spec_parts else ""

def _extract_spec_from_layer(layer: str, block_name: str) -> str:
    """从图层名/块名中推断规格。"""
    text = f"{layer} {block_name}"
    for pat in SPEC_PATTERNS.values():
        m = pat.search(text)
        if m:
            return m.group(0)
    return ""

SCHEMA = "tzp.quantities/1.0"


def _shoelace(pts) -> float:
    """鞋带公式求多边形面积（绝对值）。点序不要求显式闭合。"""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def entity_area(e) -> float:
    """确定性求闭合图元面积。

    ezdxf 1.x 的 LWPOLYLINE / HATCH 均无 .area() 方法，旧实现靠 try/except 会
    静默归零；此处改为读取顶点后鞋带公式计算，避免面积通道整体失效。
    """
    t = e.dxftype()
    try:
        if t == "LWPOLYLINE":
            if not e.closed:
                return 0.0
            return _shoelace([(p[0], p[1]) for p in e.get_points()])
        if t == "POLYLINE":
            if not getattr(e, "is_closed", False):
                return 0.0
            return _shoelace([(v.dxf.location.x, v.dxf.location.y) for v in e.vertices])
        if t == "HATCH":
            from ezdxf import path as ezpath
            total = 0.0
            for p in ezpath.from_hatch(e):
                total += _shoelace([(v.x, v.y) for v in p.flattening(0.5)])
            return total
    except Exception:
        return 0.0
    return 0.0


def measure_dxf(dxf_path: str) -> dict:
    """量算 DXF 工程量：面积、长度、计数。

    几何量算只统计 MEASURABLE 图层；FRAME/AXIS/ANNOTATION/FILL/AUXILIARY 进
    excluded，UNKNOWN 进 unclassified（不得静默丢弃）。所有数值按比例校准结果
    factor_to_mm 换算为真实毫米。
    """
    pool = DXFEntityPool(dxf_path)
    msp = pool.msp
    scale = calibrate(pool)
    factor = float(scale["factor_to_mm"])
    stats = pool.statistics()

    # 1. 构件计数（INSERT）+ 型号规格提取
    block_qty = Counter()
    block_specs = defaultdict(Counter)  # block_name -> spec -> count
    block_layers = defaultdict(Counter)
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        if pool.role(e) in EXCLUDED_ROLES:
            continue
        name = _dxf_attr(e, "name", "UNKNOWN")
        layer = _dxf_attr(e, "layer", "0")
        block_qty[name] += 1
        block_layers[name][layer] += 1
        # 规格提取
        spec = _extract_spec_from_attrib(e)
        if not spec:
            spec = _extract_spec_from_layer(layer, name)
        if not spec:
            spec = "未标注"
        block_specs[name][spec] += 1

    block_table = []
    for name, qty in block_qty.most_common():
        specs = dict(block_specs[name])
        block_table.append({
            "block_name": name,
            "quantity": qty,
            "unit": "个",
            "layers": dict(block_layers[name]),
            "specs": specs,
        })

    # 2. 线长统计（按图层，仅 MEASURABLE）
    layer_length = defaultdict(float)
    for e in msp:
        t = e.dxftype()
        if t not in ("LINE", "LWPOLYLINE", "ARC", "CIRCLE", "SPLINE"):
            continue
        if pool.role(e) != "MEASURABLE":
            continue
        layer = _dxf_attr(e, "layer", "0")
        try:
            if t == "LINE":
                s, en = e.dxf.start, e.dxf.end
                layer_length[layer] += math.dist((s.x, s.y), (en.x, en.y))
            elif t == "LWPOLYLINE":
                try:
                    layer_length[layer] += e.length()
                except Exception:
                    pts = [(p[0], p[1]) for p in e.get_points()]
                    seg = 0.0
                    for i in range(len(pts) - 1):
                        seg += math.dist(pts[i], pts[i + 1])
                    if e.closed and len(pts) > 2:
                        seg += math.dist(pts[-1], pts[0])
                    layer_length[layer] += seg
            elif t == "ARC":
                span = (e.dxf.end_angle - e.dxf.start_angle) % 360
                layer_length[layer] += math.radians(span) * e.dxf.radius
            elif t == "CIRCLE":
                layer_length[layer] += 2 * math.pi * e.dxf.radius
            elif t == "SPLINE":
                try:
                    layer_length[layer] += e.length()
                except Exception:
                    pass
        except Exception:
            continue

    length_table = [
        {
            "layer": k,
            "total_length_mm": _round(v * factor),
            "total_length_m": _round(v * factor / 1000),
            "unit": "m",
        }
        for k, v in sorted(layer_length.items(), key=lambda x: -x[1]) if v > 0
    ]

    # 3. 闭合区域面积（按图层，仅 MEASURABLE）
    layer_area = defaultdict(float)
    for e in msp:
        if e.dxftype() not in ("LWPOLYLINE", "POLYLINE", "HATCH"):
            continue
        if pool.role(e) != "MEASURABLE":
            continue
        a = entity_area(e)
        if a > 0:
            layer_area[_dxf_attr(e, "layer", "0")] += a

    area_table = [
        {
            "layer": k,
            "total_area_mm2": _round(v * factor * factor),
            "total_area_m2": _round(v * factor * factor / 1e6),
            "unit": "m²",
        }
        for k, v in sorted(layer_area.items(), key=lambda x: -x[1]) if v > 0
    ]

    excluded = {}
    for role in EXCLUDED_ROLES:
        count = stats["role_counts"].get(role, 0)
        if count:
            excluded[role] = {
                "entity_count": count,
                "layers": stats["role_layers"].get(role, []),
            }
    unclassified = stats["unclassified"]
    pending = list(scale.get("pending") or [])
    if unclassified["entity_count"]:
        pending.append(
            f"{unclassified['entity_count']} 个图元位于未识别图层 "
            f"{unclassified['layers']}，未计入几何量算"
        )

    return {
        "schema": SCHEMA,
        "file": os.path.basename(dxf_path),
        "scale": scale,
        "layer_roles": stats["layer_roles"],
        "block_table": block_table,
        "length_table": length_table,
        "area_table": area_table,
        "excluded": excluded,
        "unclassified": unclassified,
        "pending": pending,
        "summary": {
            "block_types": len(block_table),
            "block_total": sum(b["quantity"] for b in block_table),
            "length_layers": len(length_table),
            "area_layers": len(area_table),
        },
    }

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="工程量量算（工作流步骤⑤）")
    ap.add_argument("drawing", help="DXF/DWG 图纸路径")
    ap.add_argument("--out", required=True, help="输出 quantities.json 路径")
    add_output_args(ap)
    args = ap.parse_args()
    out = Out.from_args(args)

    try:
        from convert_dwg import ensure_dxf
        dxf_path = ensure_dxf(args.drawing)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    result = measure_dxf(dxf_path)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    s = result["summary"]
    out.ok(
        "量算",
        args.out,
        f"{s['block_types']} 类构件 / {s['block_total']} 个",
        f"线长图层 {s['length_layers']}",
        f"面积图层 {s['area_layers']}",
    )

if __name__ == "__main__":
    main()
