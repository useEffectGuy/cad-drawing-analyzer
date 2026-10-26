#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用表格提取脚本 —— 工作流步骤⑤工程量提取（PDF 表格通道）
用法:
    # 从 PDF 提取表格（门窗表/材料表/设备表）
    python parse_tables.py <图纸.pdf> --out <输出目录>/tables.json
    # 同时产出矢量几何量算（长度/面积/计数）
    python parse_tables.py <图纸.pdf> --out work/tables.json --geometry work/pdf_quantities.json --pdf-scale 100
    # 从 DXF 中提取表格文字（块属性/文字排列）
    python parse_tables.py <图纸.dxf> --out tables.json
功能:
    1. 矢量 PDF：pdfplumber 提取表格，自动识别表头
    2. 可选 --geometry：调用 measure_pdf 统计线条长度、矩形面积、图元数量
    3. DXF：从块属性和邻近文字中提取表格式数据
    4. 输出结构化 JSON，供后续工程量汇总（tables.json 结构保持不变）
依赖: pdfplumber（PDF），ezdxf（DXF），openpyxl（导出 Excel）
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_common import Out, add_output_args  # noqa: E402

# 表格类型识别关键词
TABLE_TYPE_KEYWORDS = {
    "门窗表": ["门窗", "门", "窗", "M0", "C1", "洞口"],
    "材料表": ["材料", "规格", "材质", "数量", "单位"],
    "设备表": ["设备", "型号", "功率", "数量"],
    "图纸目录": ["图号", "图名", "图纸目录", "序号"],
}

def _classify_table(headers: list, rows: list) -> str:
    """根据表头和内容判断表格类型。"""
    text = " ".join([str(h) for h in headers] + [str(c) for r in rows[:3] for c in r])
    for ttype, keywords in TABLE_TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return ttype
    return "通用表"

def parse_pdf_tables(pdf_path: str) -> list:
    """从矢量 PDF 提取所有表格。"""
    try:
        import pdfplumber
    except ImportError:
        print("[ERROR] 缺少 pdfplumber，请执行: pip install pdfplumber", file=sys.stderr)
        return []
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            raw_tables = page.extract_tables()
            for t_idx, table in enumerate(raw_tables):
                if not table or len(table) < 1:
                    continue
                # 清理空行
                rows = [[c.strip() if c else "" for c in row] for row in table]
                rows = [r for r in rows if any(r)]
                if not rows:
                    continue
                headers = rows[0]
                body = rows[1:]
                tables.append({
                    "page": i + 1,
                    "table_index": t_idx,
                    "type": _classify_table(headers, body),
                    "headers": headers,
                    "rows": body,
                    "row_count": len(body),
                })
    return tables

def parse_dxf_tables(dxf_path: str) -> list:
    """从 DXF 中提取表格式数据（块属性 + 文字排列）。"""
    try:
        import ezdxf
    except ImportError:
        print("[ERROR] 缺少 ezdxf，请执行: pip install ezdxf", file=sys.stderr)
        return []
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    tables = []
    # 简单策略：按 Y 坐标聚类文字行，再按 X 坐标分列
    texts = []
    for e in msp:
        if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
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
                texts.append({"content": str(content).strip(), "x": ins[0], "y": ins[1]})
            except Exception:
                continue
    if not texts:
        return tables
    # 按 Y 聚类成行（容差 50 单位）
    texts.sort(key=lambda t: -t["y"])
    rows = []
    current_row = [texts[0]]
    for t in texts[1:]:
        if abs(t["y"] - current_row[0]["y"]) < 50:
            current_row.append(t)
        else:
            rows.append(current_row)
            current_row = [t]
    rows.append(current_row)
    # 每行按 X 排序
    for row in rows:
        row.sort(key=lambda t: t["x"])
    # 取前 20 行作为可能的表格
    table_rows = [[t["content"] for t in row] for row in rows[:20]]
    if table_rows:
        headers = table_rows[0]
        body = table_rows[1:]
        tables.append({
            "page": 1,
            "table_index": 0,
            "type": _classify_table(headers, body),
            "headers": headers,
            "rows": body,
            "row_count": len(body),
        })
    return tables

def to_excel(tables: list, out_path: str):
    """将提取的表格导出为 Excel（每个表一个 sheet）。"""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("[ERROR] 缺少 openpyxl，请执行: pip install openpyxl", file=sys.stderr)
        return
    from cad_common import xlsx_auto_width, xlsx_body, xlsx_header, xlsx_new
    wb = xlsx_new()
    for idx, t in enumerate(tables):
        name = f"{t['type']}_{idx+1}"[:31]
        ws = wb.create_sheet(title=name)
        ws.append(t["headers"])
        xlsx_header(ws, len(t["headers"]))
        for row in t["rows"]:
            ws.append(row)
        xlsx_body(ws)
        # 自动列宽（保持原行为：无最小宽度限制）
        xlsx_auto_width(ws, min_w=0, max_w=40)
    wb.save(out_path)

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="通用表格提取（工作流步骤⑤）")
    ap.add_argument("input", help="PDF 或 DXF 文件路径")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--excel", help="可选：导出 Excel 路径")
    ap.add_argument("--geometry", nargs="?", const="", default=None,
                    help="可选：同时输出 PDF 矢量几何量算 JSON；不指定路径时写到 --out 同目录的 pdf_quantities.json")
    ap.add_argument("--pdf-scale", type=float, default=1.0,
                    help="几何量算的图纸比例分母，如 1:100 传 100（默认 1:1）")
    add_output_args(ap)
    args = ap.parse_args()
    out = Out.from_args(args)

    ext = os.path.splitext(args.input)[1].lower()
    if ext == ".pdf":
        tables = parse_pdf_tables(args.input)
    elif ext in (".dxf", ".dwg"):
        if ext == ".dwg":
            from convert_dwg import ensure_dxf
            args.input = ensure_dxf(args.input)
        tables = parse_dxf_tables(args.input)
    else:
        print(f"[ERROR] 不支持的格式: {ext}", file=sys.stderr)
        sys.exit(2)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2)
    out.ok("表格", args.out, f"{len(tables)} 个表")

    if args.excel:
        to_excel(tables, args.excel)
        out.ok("Excel", args.excel)

    if args.geometry is not None:
        if ext != ".pdf":
            out.detail("     [WARN] --geometry 仅支持矢量 PDF，已忽略")
        else:
            from measure_pdf import measure_pdfs
            geo_path = args.geometry or os.path.join(
                os.path.dirname(os.path.abspath(args.out)) or ".", "pdf_quantities.json")
            geo = measure_pdfs([args.input], args.pdf_scale)
            os.makedirs(os.path.dirname(os.path.abspath(geo_path)) or ".", exist_ok=True)
            with open(geo_path, "w", encoding="utf-8") as f:
                json.dump(geo, f, ensure_ascii=False, indent=2)
            t = geo["totals"]
            out.ok("PDF几何量算", geo_path,
                   f"线条 {t['line_count']} 条 / {t['line_length_m']} m",
                   f"矩形面积 {t['rect_area_m2']} m²")
            for err in geo["errors"]:
                out.detail(f"     [WARN] {err}")

if __name__ == "__main__":
    main()
