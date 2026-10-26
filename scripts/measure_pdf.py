#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 矢量几何量算脚本 —— 工作流步骤⑤工程量提取（PDF 几何通道）

用法:
    # 单张 / 多张 PDF 量算
    python measure_pdf.py <图纸.pdf> --out work/pdf_quantities.json --scale 100
    # 与表格提取同时产出几何量算
    python parse_tables.py <图纸.pdf> --out work/tables.json --geometry work/pdf_quantities.json

功能:
    1. 逐页判定矢量 / 扫描通道；扫描页跳过几何量算并标记
    2. 矢量页读取 page.lines / page.rects / page.curves，统计数量、总长度、总面积
    3. 按线宽分组统计（近似区分图层/线型）
    4. 按图纸比例换算实际尺寸（--scale，1:100 传 100；默认 1:1）
输出:
    pdf_quantities.json，schema = tzp.pdf.quantities/1.0
依赖: pdfplumber
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_common import Out, add_output_args  # noqa: E402

SCHEMA = "tzp.pdf.quantities/1.0"
# PDF 用户单位：1 pt = 1/72 inch
POINT_TO_MM = 25.4 / 72.0
# 与 render_pdf.py 的矢量判定阈值保持一致
VECTOR_TEXT_THRESHOLD = 20
# 每页最多保留的面积/线宽明细条数（完整聚合数字在 totals）
TOP_N = 10

TOTAL_KEYS = [
    "pages", "vector_pages", "scanned_pages", "text_char_count",
    "line_count", "line_length_m",
    "rect_count", "rect_perimeter_m", "rect_area_m2",
    "curve_count", "curve_length_m", "curve_area_m2",
]


def _zero_totals() -> dict:
    return {k: 0 for k in TOTAL_KEYS}


def _pt_to_m(pt: float, scale: float) -> float:
    """图纸上的 pt 换算为实际长度（米）。"""
    return pt * POINT_TO_MM * scale / 1000.0


def _pt2_to_m2(pt2: float, scale: float) -> float:
    """图纸上的 pt² 换算为实际面积（平方米）。"""
    mm = POINT_TO_MM * scale
    return pt2 * mm * mm / 1e6


def _seg_len(line: dict) -> float:
    try:
        return math.hypot(float(line["x1"]) - float(line["x0"]),
                          float(line["y1"]) - float(line["y0"]))
    except Exception:
        return 0.0


def _polyline_len(pts) -> float:
    try:
        points = [(float(p[0]), float(p[1])) for p in pts or []]
    except Exception:
        return 0.0
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _shoelace(pts) -> float:
    """多边形面积（绝对值），点序不要求显式闭合。"""
    try:
        points = [(float(p[0]), float(p[1])) for p in pts or []]
    except Exception:
        return 0.0
    if len(points) < 3:
        return 0.0
    s = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _measure_geometry(page, scale: float) -> dict:
    """量算单页矢量几何：线条、矩形、曲线。"""
    lines = page.lines or []
    rects = page.rects or []
    curves = page.curves or []

    line_len_pt = 0.0
    width_stats = defaultdict(lambda: [0, 0.0])  # width_pt -> [count, length_pt]
    for line in lines:
        seg = _seg_len(line)
        line_len_pt += seg
        try:
            w = round(float(line.get("linewidth") or 0.0), 3)
        except Exception:
            w = 0.0
        width_stats[w][0] += 1
        width_stats[w][1] += seg

    rect_area_pt2 = 0.0
    rect_perim_pt = 0.0
    rect_items = []
    for rect in rects:
        try:
            w = abs(float(rect["x1"]) - float(rect["x0"]))
            h = abs(float(rect["y1"]) - float(rect["y0"]))
        except Exception:
            continue
        area = w * h
        rect_area_pt2 += area
        rect_perim_pt += 2 * (w + h)
        rect_items.append((area, [round(float(rect["x0"]), 3), round(float(rect["y0"]), 3),
                                  round(float(rect["x1"]), 3), round(float(rect["y1"]), 3)]))

    curve_len_pt = 0.0
    curve_area_pt2 = 0.0
    for curve in curves:
        pts = curve.get("pts") or []
        curve_len_pt += _polyline_len(pts)
        if curve.get("fill") and pts:
            curve_area_pt2 += _shoelace(pts)

    width_rows = sorted(
        (
            {
                "width_pt": w,
                "count": v[0],
                "length_m": round(_pt_to_m(v[1], scale), 3),
            }
            for w, v in width_stats.items()
        ),
        key=lambda r: -r["length_m"],
    )[:TOP_N]

    rect_items.sort(key=lambda x: -x[0])
    top_areas = [
        {"bbox_pt": bbox, "area_m2": round(_pt2_to_m2(area, scale), 3)}
        for area, bbox in rect_items[:TOP_N] if area > 0
    ]

    return {
        "lines": {
            "count": len(lines),
            "total_length_pt": round(line_len_pt, 3),
            "total_length_m": round(_pt_to_m(line_len_pt, scale), 3),
        },
        "rects": {
            "count": len(rects),
            "total_perimeter_pt": round(rect_perim_pt, 3),
            "total_perimeter_m": round(_pt_to_m(rect_perim_pt, scale), 3),
            "total_area_pt2": round(rect_area_pt2, 3),
            "total_area_m2": round(_pt2_to_m2(rect_area_pt2, scale), 3),
        },
        "curves": {
            "count": len(curves),
            "total_length_pt": round(curve_len_pt, 3),
            "total_length_m": round(_pt_to_m(curve_len_pt, scale), 3),
            "filled_area_pt2": round(curve_area_pt2, 3),
            "filled_area_m2": round(_pt2_to_m2(curve_area_pt2, scale), 3),
        },
        "by_line_width": width_rows,
        "top_areas": top_areas,
    }


def _page_totals(entry: dict) -> dict:
    """把单页结果折算成可累加的计数。"""
    t = _zero_totals()
    t["pages"] = 1
    t["text_char_count"] = entry.get("char_count", 0)
    if entry.get("channel") == "vector":
        t["vector_pages"] = 1
    else:
        t["scanned_pages"] = 1
    geo = entry.get("geometry") or {}
    lines = geo.get("lines") or {}
    rects = geo.get("rects") or {}
    curves = geo.get("curves") or {}
    t["line_count"] = lines.get("count", 0)
    t["line_length_m"] = lines.get("total_length_m", 0.0)
    t["rect_count"] = rects.get("count", 0)
    t["rect_perimeter_m"] = rects.get("total_perimeter_m", 0.0)
    t["rect_area_m2"] = rects.get("total_area_m2", 0.0)
    t["curve_count"] = curves.get("count", 0)
    t["curve_length_m"] = curves.get("total_length_m", 0.0)
    t["curve_area_m2"] = curves.get("filled_area_m2", 0.0)
    return t


def _sum_totals(entries: list) -> dict:
    total = _zero_totals()
    for t in entries:
        for k in TOTAL_KEYS:
            total[k] += t.get(k, 0)
    for k in TOTAL_KEYS:
        if isinstance(total[k], float):
            total[k] = round(total[k], 3)
    return total


def measure_pdf(pdf_path: str, scale: float = 1.0) -> dict:
    """量算单个 PDF 的矢量几何。"""
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("缺少 pdfplumber，请执行: pip install pdfplumber") from e

    scale = float(scale or 1.0)
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            char_count = len(text.strip())
            channel = "vector" if char_count > VECTOR_TEXT_THRESHOLD else "scanned"
            entry = {
                "page": i + 1,
                "width": round(float(page.width), 3),
                "height": round(float(page.height), 3),
                "char_count": char_count,
                "channel": channel,
            }
            if channel == "vector":
                try:
                    entry["geometry"] = _measure_geometry(page, scale)
                except Exception as e:
                    entry["geometry"] = None
                    entry["geometry_error"] = str(e)[:200]
            else:
                entry["geometry"] = None
            pages.append(entry)

    totals = _sum_totals([_page_totals(p) for p in pages])
    return {
        "file": os.path.abspath(pdf_path),
        "scale": scale,
        "total_pages": len(pages),
        "vector_pages": totals["vector_pages"],
        "scanned_pages": totals["scanned_pages"],
        "pages": pages,
        "totals": totals,
    }


def measure_pdfs(pdf_paths: list, scale: float = 1.0) -> dict:
    """量算多个 PDF 并汇总。单个文件失败不影响其他文件。"""
    scale = float(scale or 1.0)
    files, totals, errors = [], [], []
    for path in pdf_paths:
        try:
            result = measure_pdf(path, scale)
        except Exception as e:
            msg = f"{os.path.basename(path)}: {e}"
            errors.append(msg[:200])
            files.append({
                "file": os.path.abspath(path),
                "scale": scale,
                "error": msg[:300],
                "pages": [],
                "totals": _zero_totals(),
            })
            continue
        files.append(result)
        totals.append(result["totals"])

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scale": scale,
        "unit": "m",
        "files": files,
        "totals": _sum_totals(totals),
        "errors": errors,
    }


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="PDF 矢量几何量算（长度/面积/计数）")
    ap.add_argument("pdf", nargs="+", help="一个或多个 PDF 文件路径")
    ap.add_argument("--out", required=True, help="输出 pdf_quantities.json 路径")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="图纸比例分母，如 1:100 传 100（默认 1:1）")
    add_output_args(ap)
    args = ap.parse_args()
    out = Out.from_args(args)

    result = measure_pdfs(args.pdf, args.scale)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    t = result["totals"]
    out.ok(
        "PDF量算",
        args.out,
        f"{len(result['files'])} 个文件",
        f"{t['vector_pages']}/{t['pages']} 矢量页",
        f"线条 {t['line_count']} 条 / {t['line_length_m']} m",
        f"矩形 {t['rect_count']} 个 / {t['rect_area_m2']} m²",
    )
    for err in result["errors"]:
        out.detail(f"     [WARN] {err}")


if __name__ == "__main__":
    main()
