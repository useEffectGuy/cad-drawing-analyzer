#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 渲染与通道判定脚本 —— 工作流步骤②格式归一 + 扫描件视觉兜底
用法:
    # 渲染 PDF 为 PNG（支持大图纸分片切片）
    python render_pdf.py <图纸.pdf> --out <输出目录> --dpi 200 --tile
    # 仅判定矢量/扫描通道
    python render_pdf.py <图纸.pdf> --detect-channel
功能:
    1. 逐页判定 PDF 类型：矢量（有可提取文字层）/ 扫描（纯图片）
    2. 矢量 PDF：保留文字层供后续 pdfplumber 解析
    3. 扫描 PDF：渲染为高清 PNG，大图纸分片切片（tile）
    4. 混合文件逐张判定，逐张选通道
依赖: pypdfium2（渲染），pdfplumber（文字提取，可选）
"""
from __future__ import annotations
import argparse
import json
import os
import sys

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# 判定阈值：单页可提取文字字符数 > 阈值 视为矢量页
VECTOR_TEXT_THRESHOLD = 20
# 切片策略：单张 PNG 最长边像素上限
TILE_MAX_PIXEL = 8000

def detect_channel(pdf_path: str) -> dict:
    """逐页判定 PDF 是矢量还是扫描。"""
    if pdfplumber is None:
        # 无 pdfplumber 时，退化为用 pypdfium 检测文字层
        return _detect_channel_fallback(pdf_path)
    pages_info = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = (page.extract_text() or "").strip()
                tables = page.find_tables()
                is_vector = len(text) > VECTOR_TEXT_THRESHOLD
                pages_info.append({
                    "page": i + 1,
                    "width": float(page.width),
                    "height": float(page.height),
                    "char_count": len(text),
                    "table_count": len(tables),
                    "channel": "vector" if is_vector else "scanned",
                    "text_preview": text[:100],
                })
    except Exception as e:
        return {"error": str(e), "pages": []}
    vector_count = sum(1 for p in pages_info if p["channel"] == "vector")
    return {
        "file": pdf_path,
        "total_pages": len(pages_info),
        "vector_pages": vector_count,
        "scanned_pages": len(pages_info) - vector_count,
        "is_mixed": 0 < vector_count < len(pages_info),
        "pages": pages_info,
    }

def _detect_channel_fallback(pdf_path: str) -> dict:
    """无 pdfplumber 时的降级判定：用 pypdfium 渲染并检查文字对象。"""
    if pdfium is None:
        return {"error": "需要安装 pypdfium2 或 pdfplumber", "pages": []}
    pages_info = []
    pdf = pdfium.PdfDocument(pdf_path)
    for i in range(len(pdf)):
        page = pdf[i]
        textpage = page.get_textpage()
        text = textpage.get_text_range() or ""
        is_vector = len(text.strip()) > VECTOR_TEXT_THRESHOLD
        pages_info.append({
            "page": i + 1,
            "char_count": len(text.strip()),
            "channel": "vector" if is_vector else "scanned",
        })
    vector_count = sum(1 for p in pages_info if p["channel"] == "vector")
    return {
        "file": pdf_path,
        "total_pages": len(pages_info),
        "vector_pages": vector_count,
        "scanned_pages": len(pages_info) - vector_count,
        "pages": pages_info,
    }

def render_pdf(pdf_path: str, out_dir: str, dpi: int = 200, tile: bool = True,
               pages: list | None = None) -> list:
    """渲染 PDF 为 PNG。大图纸自动分片切片。"""
    if pdfium is None:
        raise RuntimeError("需要 pypdfium2：pip install pypdfium2")
    os.makedirs(out_dir, exist_ok=True)
    pdf = pdfium.PdfDocument(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    rendered = []
    page_indices = pages if pages else range(len(pdf))
    for i in page_indices:
        page = pdf[i]
        # 按 DPI 计算像素尺寸
        bitmap = page.render(scale=dpi / 72)
        pil_image = bitmap.to_pil()
        w, h = pil_image.size
        out_path = os.path.join(out_dir, f"{base}_p{i+1:03d}.png")
        if tile and max(w, h) > TILE_MAX_PIXEL:
            # 分片切片：按网格切
            tiles = _tile_image(pil_image, TILE_MAX_PIXEL)
            for idx, tile_img in enumerate(tiles):
                t_path = os.path.join(out_dir, f"{base}_p{i+1:03d}_t{idx+1:02d}.png")
                tile_img.save(t_path)
                rendered.append(t_path)
        else:
            pil_image.save(out_path)
            rendered.append(out_path)
    return rendered

def _tile_image(img, max_pixel: int) -> list:
    """将大图切成不超过 max_pixel 的网格片。"""
    w, h = img.size
    cols = (w + max_pixel - 1) // max_pixel
    rows = (h + max_pixel - 1) // max_pixel
    tiles = []
    for r in range(rows):
        for c in range(cols):
            left = c * max_pixel
            upper = r * max_pixel
            right = min(left + max_pixel, w)
            lower = min(upper + max_pixel, h)
            tiles.append(img.crop((left, upper, right, lower)))
    return tiles

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="PDF 渲染与通道判定（工作流步骤②）")
    ap.add_argument("pdf", help="PDF 文件路径")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--dpi", type=int, default=200, help="渲染 DPI（默认 200）")
    ap.add_argument("--tile", action="store_true", help="大图纸自动分片切片")
    ap.add_argument("--detect-channel", action="store_true", help="仅判定矢量/扫描通道")
    ap.add_argument("--pages", nargs="*", type=int, help="指定页码（从 1 开始）")
    args = ap.parse_args()

    if args.detect_channel:
        info = detect_channel(args.pdf)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if not args.out:
        ap.error("渲染模式需要 --out 指定输出目录")
    pages = [p - 1 for p in args.pages] if args.pages else None
    try:
        files = render_pdf(args.pdf, args.out, args.dpi, args.tile, pages)
        print(f"[OK] 渲染完成：{len(files)} 个文件")
        for f in files[:10]:
            print(f"     {f}")
        if len(files) > 10:
            print(f"     ... 共 {len(files)} 个")
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
