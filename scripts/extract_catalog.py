#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图纸目录提取脚本 —— 工作流步骤③目录识别
用法:
    # 从整套图纸中提取目录（PDF 优先，DXF 兜底）
    python extract_catalog.py <图纸文件或目录> --out <输出目录>/catalog.json
功能:
    1. 优先检索"图纸目录/图纸索引"表格，结构化解析（序号、图号、图名、规格、备注）
    2. 目录与逐张识别结果互相校验，对不上的标"目录外图纸"
    3. 无目录时，逐张调用 detect_titleblock 补建目录
依赖: pdfplumber, ezdxf, openpyxl
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 图纸目录标题关键词
CATALOG_TITLE_KEYWORDS = ["图纸目录", "图 纸 目 录", "图纸索引", "图 纸 索 引", "目  录"]
# 图号正则（更宽松）
DRAWING_NO_PATTERN = re.compile(
    r"([\u4e00-\u9fa5]{2}|[A-Z]{2})[\-]?\d{1,3}", re.IGNORECASE
)

def _find_catalog_in_pdf(pdf_path: str) -> list | None:
    """在 PDF 中查找图纸目录表格。"""
    try:
        import pdfplumber
    except ImportError:
        return None
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = (page.extract_text() or "")
            if not any(kw in text for kw in CATALOG_TITLE_KEYWORDS):
                continue
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                rows = [[(c or "").strip() for c in row] for row in table]
                rows = [r for r in rows if any(r)]
                if len(rows) < 2:
                    continue
                # 判定是否为目录表：表头含"图号"或首列含图号特征
                header = " ".join(rows[0])
                first_col = " ".join(r[0] for r in rows[1:6] if r)
                if "图号" in header or DRAWING_NO_PATTERN.search(first_col):
                    return _structure_catalog(rows, source=f"PDF第{i+1}页")
    return None

def _find_catalog_in_dxf(dxf_path: str) -> list | None:
    """在 DXF 中查找图纸目录（文字排列成行）。"""
    try:
        import ezdxf
    except ImportError:
        return None
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
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
        return None
    # 检测是否含目录标题
    full_text = " ".join(t["content"] for t in texts)
    if not any(kw in full_text for kw in CATALOG_TITLE_KEYWORDS):
        return None
    # 按 Y 聚类成行
    texts.sort(key=lambda t: -t["y"])
    rows = []
    current = [texts[0]]
    for t in texts[1:]:
        if abs(t["y"] - current[0]["y"]) < 80:
            current.append(t)
        else:
            rows.append(current)
            current = [t]
    rows.append(current)
    for row in rows:
        row.sort(key=lambda t: t["x"])
    table = [[t["content"] for t in row] for row in rows]
    return _structure_catalog(table, source="DXF")

def _structure_catalog(rows: list, source: str) -> list:
    """将原始表格行结构化为目录条目。"""
    catalog = []
    for row in rows[1:]:  # 跳过表头
        # 清理空值
        cells = [c for c in row if c]
        if not cells:
            continue
        # 尝试识别字段：序号、图号、图名、规格、备注
        entry = {"序号": "", "图号": "", "图名": "", "规格": "", "备注": "", "来源": source}
        # 图号优先匹配
        for c in cells:
            if DRAWING_NO_PATTERN.search(c) and not entry["图号"]:
                entry["图号"] = c
                break
        # 图名：含图名关键词的单元格
        name_keywords = ["平面图", "立面图", "剖面图", "大样", "详图", "系统图", "总平面", "说明"]
        for c in cells:
            if any(kw in c for kw in name_keywords) and not entry["图名"]:
                entry["图名"] = c
                break
        # 剩余字段填充
        remaining = [c for c in cells if c not in (entry["图号"], entry["图名"])]
        if remaining:
            entry["序号"] = remaining[0] if remaining and remaining[0].isdigit() else ""
        if not entry["图名"] and len(remaining) >= 2:
            entry["图名"] = remaining[1]
        if not entry["图号"]:
            entry["图号"] = "未识别"
        if not entry["图名"]:
            entry["图名"] = remaining[-1] if remaining else "未识别"
        catalog.append(entry)
    return catalog

def _build_catalog_from_titleblocks(files: list) -> list:
    """无目录时，逐张识别图框补建目录。"""
    catalog = []
    for fp in files:
        if not fp.lower().endswith((".dxf", ".dwg")):
            continue
        try:
            from convert_dwg import ensure_dxf
            from detect_titleblock import detect_titleblock
            dxf = ensure_dxf(fp)
            info = detect_titleblock(dxf)
            catalog.append({
                "序号": str(len(catalog) + 1),
                "图号": info.get("drawing_no") or "未识别",
                "图名": info.get("drawing_name") or "未识别",
                "规格": info.get("scale") or "",
                "备注": "图框识别补建",
                "来源": os.path.basename(fp),
            })
        except Exception as e:
            catalog.append({
                "序号": str(len(catalog) + 1),
                "图号": "未识别",
                "图名": os.path.basename(fp),
                "规格": "",
                "备注": f"识别失败: {e}",
                "来源": os.path.basename(fp),
            })
    return catalog

def extract_catalog(input_path: str) -> dict:
    """提取图纸目录。"""
    files = []
    if os.path.isdir(input_path):
        for root, _, fnames in os.walk(input_path):
            for f in fnames:
                if f.lower().endswith((".pdf", ".dxf", ".dwg")):
                    files.append(os.path.join(root, f))
    else:
        files = [input_path]

    catalog = None
    source = None
    # 优先在 PDF 中找目录
    for fp in files:
        if fp.lower().endswith(".pdf"):
            catalog = _find_catalog_in_pdf(fp)
            if catalog:
                source = fp
                break
    # 再在 DXF 中找
    if not catalog:
        for fp in files:
            if fp.lower().endswith((".dxf", ".dwg")):
                try:
                    from convert_dwg import ensure_dxf
                    dxf = ensure_dxf(fp)
                    catalog = _find_catalog_in_dxf(dxf)
                    if catalog:
                        source = fp
                        break
                except Exception:
                    continue
    # 无目录则逐张图框补建
    if not catalog:
        catalog = _build_catalog_from_titleblocks(files)
        source = "图框识别补建"

    # 目录与逐张文件校验
    file_basenames = {os.path.splitext(os.path.basename(f))[0] for f in files}
    catalog_nos = {e["图号"] for e in catalog if e["图号"] != "未识别"}
    out_of_catalog = [f for f in file_basenames if f not in catalog_nos]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "total_sheets": len(catalog),
        "items": catalog,
        "out_of_catalog_files": out_of_catalog,
        "has_catalog_table": source != "图框识别补建",
    }

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="图纸目录提取（工作流步骤③）")
    ap.add_argument("input", help="图纸文件或目录路径")
    ap.add_argument("--out", required=True, help="输出 catalog.json 路径")
    args = ap.parse_args()

    result = extract_catalog(args.input)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] 目录提取完成: {args.out}")
    print(f"     来源: {result['source']}")
    print(f"     图纸数量: {result['total_sheets']}")
    if result["out_of_catalog_files"]:
        print(f"     目录外文件: {result['out_of_catalog_files']}")

if __name__ == "__main__":
    main()
