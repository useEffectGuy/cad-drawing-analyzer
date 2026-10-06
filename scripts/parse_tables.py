#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用表格提取脚本 —— 工作流步骤⑤工程量提取（PDF 表格通道）
用法:
    # 从 PDF 提取表格（门窗表/材料表/设备表）
    python parse_tables.py <图纸.pdf> --out <输出目录>/tables.json
    # 从 DXF 中提取表格文字（块属性/文字排列）
    python parse_tables.py <图纸.dxf> --out tables.json
功能:
    1. 矢量 PDF：pdfplumber 提取表格，自动识别表头
    2. DXF：从块属性和邻近文字中提取表格式数据
    3. 输出结构化 JSON，供后续工程量汇总
依赖: pdfplumber（PDF），ezdxf（DXF），openpyxl（导出 Excel）
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        print("[ERROR] 缺少 openpyxl，请执行: pip install openpyxl", file=sys.stderr)
        return
    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True, color="FFFFFF", size=11, name="微软雅黑")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for idx, t in enumerate(tables):
        name = f"{t['type']}_{idx+1}"[:31]
        ws = wb.create_sheet(title=name)
        ws.append(t["headers"])
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border
        for row in t["rows"]:
            ws.append(row)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.border = border
        # 自动列宽
        for col in ws.columns:
            maxlen = 0
            for cell in col:
                if cell.value:
                    w = sum(2 if ord(c) > 127 else 1 for c in str(cell.value))
                    maxlen = max(maxlen, w)
            ws.column_dimensions[col[0].column_letter].width = min(maxlen + 2, 40)
    wb.save(out_path)

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="通用表格提取（工作流步骤⑤）")
    ap.add_argument("input", help="PDF 或 DXF 文件路径")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--excel", help="可选：导出 Excel 路径")
    args = ap.parse_args()

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
    print(f"[OK] 提取到 {len(tables)} 个表格: {args.out}")
    for t in tables:
        print(f"     - 第{t['page']}页 {t['type']}（{t['row_count']}行）")

    if args.excel:
        to_excel(tables, args.excel)
        print(f"[OK] Excel 导出: {args.excel}")

if __name__ == "__main__":
    main()
