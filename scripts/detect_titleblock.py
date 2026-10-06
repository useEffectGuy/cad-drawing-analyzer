#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单张图框识别脚本 —— 工作流步骤③目录识别（无目录兜底）
用法:
    python detect_titleblock.py <图纸.dxf> --out <输出目录>/meta.json
功能:
    定位图框（最大矩形），从图签区域提取：图号、图名、比例、版次、设计/审核/制图
    无图纸目录时，逐张调用本脚本补建目录。
依赖: ezdxf, networkx（复用 cad_space 的图框检测）
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import ezdxf
except ImportError:
    print("[ERROR] 缺少依赖 ezdxf，请先执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)

# 图号正则：建施-01、结施-03、水施-02、JS-01、建施01 等
DRAWING_NO_PATTERN = re.compile(
    r"(建施|结施|水施|暖施|电施|设施|装施|总图|JS|GS|SS|NS|DS|FS|ZS)[\-]?\d{1,3}",
    re.IGNORECASE,
)
# 比例正则：1:100、1:50、1:200
SCALE_PATTERN = re.compile(r"1\s*[:：]\s*\d+")
# 版次/版本：A、B、1.0、Rev.1
REV_PATTERN = re.compile(r"(版次|版本|Rev\.?|Rev)[：:\s]*([A-Z0-9.]+)", re.IGNORECASE)
# 图名关键词
DRAWING_NAME_KEYWORDS = [
    "平面图", "立面图", "剖面图", "大样图", "详图", "节点图", "系统图",
    "总平面图", "流程图", "原理图", "布置图", "示意图",
]

def _dxf_attr(entity, name, default=None):
    try:
        return entity.dxf.get(name, default)
    except Exception:
        return default

def detect_titleblock(dxf_path: str) -> dict:
    """检测图框并提取图签信息。"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # 1. 找最大闭合矩形作为外图框
    rects = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
        except Exception:
            continue
        if len(pts) != 4 or not e.closed:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w <= 0 or h <= 0:
            continue
        rects.append((w * h, min(xs), min(ys), max(xs), max(ys)))

    if not rects:
        # 退化：用所有实体的包围盒
        xs, ys = [], []
        for e in msp:
            try:
                if hasattr(e, "dxf"):
                    for attr in ("insert", "start", "end", "center"):
                        p = _dxf_attr(e, attr)
                        if p is not None:
                            xs.append(p[0]); ys.append(p[1])
            except Exception:
                pass
        if not xs:
            return {"found": False, "drawing_no": None, "drawing_name": None,
                    "scale": None, "revision": None}
        frame = (min(xs), min(ys), max(xs), max(ys))
    else:
        rects.sort(reverse=True)
        _, x0, y0, x1, y1 = rects[0]
        frame = (x0, y0, x1, y1)

    # 2. 图签区域：默认右下角（外图框右下 1/3 宽 × 1/4 高）
    fx0, fy0, fx1, fy1 = frame
    tb_x0 = fx0 + (fx1 - fx0) * 2 / 3
    tb_y0 = fy0
    tb_x1 = fx1
    tb_y1 = fy0 + (fy1 - fy0) / 4

    # 3. 提取图签区域内的文字
    texts = []
    for e in msp:
        if e.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
            continue
        try:
            if e.dxftype() == "MTEXT":
                content = e.text
                ins = e.dxf.insert
            elif e.dxftype() == "ATTRIB":
                content = e.dxf.text
                ins = e.dxf.insert
            else:
                content = e.dxf.text
                ins = e.dxf.insert
        except Exception:
            continue
        if content is None:
            continue
        content = str(content).strip()
        if not content:
            continue
        if tb_x0 <= ins[0] <= tb_x1 and tb_y0 <= ins[1] <= tb_y1:
            texts.append(content)

    # 4. 从文字中解析图号/图名/比例/版次
    info = {"drawing_no": None, "drawing_name": None, "scale": None, "revision": None}
    for t in texts:
        # 图号
        if not info["drawing_no"]:
            m = DRAWING_NO_PATTERN.search(t)
            if m:
                info["drawing_no"] = m.group(0)
        # 比例
        if not info["scale"]:
            m = SCALE_PATTERN.search(t)
            if m:
                info["scale"] = m.group(0).replace(" ", "")
        # 版次
        if not info["revision"]:
            m = REV_PATTERN.search(t)
            if m:
                info["revision"] = m.group(2)
        # 图名
        if not info["drawing_name"]:
            for kw in DRAWING_NAME_KEYWORDS:
                if kw in t:
                    info["drawing_name"] = t
                    break

    info["found"] = bool(info["drawing_no"] or info["drawing_name"])
    info["title_block_texts"] = texts
    info["frame"] = [round(v, 2) for v in frame]
    return info

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="单张图框识别（工作流步骤③兜底）")
    ap.add_argument("drawing", help="DXF/DWG 图纸路径")
    ap.add_argument("--out", required=True, help="输出 meta.json 路径")
    args = ap.parse_args()

    try:
        from convert_dwg import ensure_dxf
        dxf_path = ensure_dxf(args.drawing)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    info = detect_titleblock(dxf_path)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print(f"[OK] 图框识别完成: {args.out}")
    print(f"     图号: {info.get('drawing_no') or '未识别'}")
    print(f"     图名: {info.get('drawing_name') or '未识别'}")
    print(f"     比例: {info.get('scale') or '未识别'}")

if __name__ == "__main__":
    main()
