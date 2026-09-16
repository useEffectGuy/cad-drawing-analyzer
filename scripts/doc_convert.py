#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
格式互转模块 —— Excel ↔ Word ↔ Markdown ↔ CSV ↔ JSON
用法:
    python doc_convert.py <源文件> --to <目标格式> [--out 输出路径]
支持转换:
    xlsx/csv  →  md / docx / json / csv / xlsx
    docx      →  md / json / pdf
    md        →  docx / xlsx / csv / json
    json      →  md / docx / xlsx / csv
功能:
    1. 自动识别源格式
    2. 转为目标格式，保持表格结构与基本排版
依赖: openpyxl, python-docx
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
def _ensure_dir(path: str):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
# ---------------------------------------------------------------- 读取各格式
def read_any(path: str) -> dict:
    """统一读取，返回 {type, title, headers, rows, paragraphs}"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        ws = wb.active
        data = [[getattr(c, "value", None) for c in row] for row in ws.iter_rows()]
        data = [r for r in data if any(v is not None and str(v).strip() for v in r)]
        headers, rows = [], []
        if data:
            first = data[0]
            ne = [v for v in first if v is not None and str(v).strip()]
            if len(ne) == 1 and len(first) > 1:
                headers = data[1] if len(data) > 1 else []
                rows = data[2:] if len(data) > 2 else []
            else:
                headers = first
                rows = data[1:]
        return {"type": "table", "title": ws.title,
                "headers": [str(h) if h is not None else "" for h in headers],
                "rows": rows, "paragraphs": []}
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = list(csv.reader(f))
        return {"type": "table", "title": os.path.splitext(os.path.basename(path))[0],
                "headers": reader[0] if reader else [],
                "rows": reader[1:] if len(reader) > 1 else [],
                "paragraphs": []}
    if ext == ".docx":
        from docx import Document
        doc = Document(path)
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        tables = []
        for t in doc.tables:
            rows = [[c.text.strip() for c in row.cells] for row in t.rows]
            if rows:
                tables.append({"headers": rows[0], "rows": rows[1:]})
        return {"type": "doc", "title": os.path.splitext(os.path.basename(path))[0],
                "headers": tables[0]["headers"] if tables else [],
                "rows": tables[0]["rows"] if tables else [],
                "paragraphs": paras, "tables": tables}
    if ext in (".md", ".markdown"):
        with open(path, encoding="utf-8") as f:
            content = f.read()
        paras = [l.strip() for l in content.split("\n") if l.strip()]
        # 解析第一个 Markdown 表格
        headers, rows = [], []
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1]):
                headers = [c.strip() for c in line.strip().strip("|").split("|")]
                j = i + 2
                while j < len(lines) and "|" in lines[j]:
                    rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                    j += 1
                break
        return {"type": "doc", "title": os.path.splitext(os.path.basename(path))[0],
                "headers": headers, "rows": rows, "paragraphs": paras}
    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {"type": "json", "title": os.path.splitext(os.path.basename(path))[0],
                "headers": [], "rows": [], "paragraphs": [], "raw": data}
    raise ValueError(f"不支持的源格式: {ext}")
# ---------------------------------------------------------------- 写出各格式
def write_md(data: dict, out_path: str) -> str:
    L = []
    if data.get("title"):
        L.append(f"# {data['title']}\n")
    for p in data.get("paragraphs", []):
        L.append(p)
    if data.get("headers"):
        L.append("")
        L.append("| " + " | ".join(str(h) for h in data["headers"]) + " |")
        L.append("|" + "|".join(["---"] * len(data["headers"])) + "|")
        for row in data.get("rows", []):
            cells = ["" if v is None else str(v) for v in row[:len(data["headers"])]]
            while len(cells) < len(data["headers"]):
                cells.append("")
            L.append("| " + " | ".join(cells) + " |")
    if data.get("type") == "json" and data.get("raw"):
        L.append("\n```json")
        L.append(json.dumps(data["raw"], ensure_ascii=False, indent=2))
        L.append("```")
    _ensure_dir(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return out_path
def write_csv(data: dict, out_path: str) -> str:
    _ensure_dir(out_path)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if data.get("headers"):
            w.writerow(data["headers"])
        for row in data.get("rows", []):
            w.writerow(["" if v is None else v for v in row])
        if not data.get("headers"):
            for p in data.get("paragraphs", []):
                w.writerow([p])
    return out_path
def write_xlsx(data: dict, out_path: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = (data.get("title") or "Sheet1")[:31]
    r = 1
    if data.get("title"):
        ws.cell(row=1, column=1, value=data["title"])
        r = 2
    if data.get("headers"):
        for c, h in enumerate(data["headers"], 1):
            ws.cell(row=r, column=c, value=h)
        r += 1
        for row in data.get("rows", []):
            for c, v in enumerate(row, 1):
                ws.cell(row=r, column=c, value=v)
            r += 1
    elif data.get("paragraphs"):
        for p in data["paragraphs"]:
            ws.cell(row=r, column=1, value=p)
            r += 1
    # 样式
    hf = Font(bold=True, color="FFFFFF", size=11)
    hfill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BFBFBF")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    hr = 2 if data.get("title") else 1
    if data.get("headers"):
        for c in range(1, len(data["headers"]) + 1):
            cell = ws.cell(row=hr, column=c)
            cell.font = hf
            cell.fill = hfill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = bd
        for rr in range(hr + 1, ws.max_row + 1):
            for c in range(1, len(data["headers"]) + 1):
                ws.cell(row=rr, column=c).border = bd
    for col in ws.columns:
        first = col[0]
        col_idx = getattr(first, "column", None)
        if col_idx is None:
            continue
        letter = get_column_letter(col_idx)
        ml = max((sum(2 if ord(ch) > 127 else 1 for ch in str(getattr(c, "value", "")))
                  for c in col if getattr(c, "value", None)), default=8)
        ws.column_dimensions[letter].width = min(max(ml + 2, 8), 40)
    _ensure_dir(out_path)
    wb.save(out_path)
    return out_path
def write_docx(data: dict, out_path: str) -> str:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from doc_word import create_doc
    sections = []
    for p in data.get("paragraphs", []):
        sections.append(("", p))
    tables = []
    if data.get("headers"):
        tables.append((None, data["headers"], data.get("rows", [])))
    return create_doc(out_path, title=data.get("title"), sections=sections or None,
                      tables=tables or None, header_text=data.get("title"))
def write_json(data: dict, out_path: str) -> str:
    _ensure_dir(out_path)
    payload = data.get("raw") if data.get("type") == "json" and data.get("raw") else {
        "title": data.get("title"),
        "headers": data.get("headers"),
        "rows": [[("" if v is None else v) for v in row] for row in data.get("rows", [])],
        "paragraphs": data.get("paragraphs", []),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return out_path
WRITERS = {
    "md": write_md, "markdown": write_md,
    "csv": write_csv,
    "xlsx": write_xlsx,
    "docx": write_docx,
    "json": write_json,
}
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="文档格式互转")
    ap.add_argument("src", help="源文件路径")
    ap.add_argument("--to", required=True, choices=list(WRITERS.keys()), help="目标格式")
    ap.add_argument("--out", default=None, help="输出路径")
    args = ap.parse_args()
    try:
        data = read_any(args.src)
        base = os.path.splitext(os.path.basename(args.src))[0]
        out = args.out or f"{base}.{args.to}"
        # PDF 特殊处理
        if args.to == "pdf":
            from doc_word import doc_to_pdf
            out = doc_to_pdf(args.src, out)
        else:
            writer = WRITERS[args.to]
            out = writer(data, out)
        print(f"[OK] 已转换: {args.src} → {out}")
        print(f"     源类型: {data.get('type')}，段落 {len(data.get('paragraphs', []))} 段，"
              f"表格 {len(data.get('rows', []))} 行")
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
if __name__ == "__main__":
    main()