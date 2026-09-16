#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档工具箱 —— Word 文档的创建、编辑、排版、PDF 导出
用法:
    # 从零创建文档
    python doc_word.py create --out 报告.docx --title "图纸分析报告" \\
        --sections "一、概述|本报告基于DXF图纸解析||二、图层|共9个图层"
    # 读取现有文档
    python doc_word.py read 报告.docx [--json]
    # 编辑现有文档
    python doc_word.py edit 报告.docx --append "新增段落内容"
    python doc_word.py edit 报告.docx --replace "旧文本=新文本"
    # 导出 PDF
    python doc_word.py to-pdf 报告.docx --out 报告.pdf
功能:
    1. 创建：生成带封面、页眉页脚、页码、目录的规范 Word 文档
    2. 读取：提取文档全部段落、表格内容
    3. 编辑：追加段落、替换文本、插入表格
    4. 导出：Word → PDF
排版规范（遵循 GB/T 44720-2024）:
    - 中文正文：宋体/仿宋，小四号（12pt），1.5 倍行距，首行缩进 2 字符
    - 一级标题：黑体三号（16pt）
    - 二级标题：黑体四号（14pt）
    - 页边距：上下 2.54cm，左右 3.17cm
依赖: python-docx（PDF 导出需 reportlab）
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
try:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.enum.section import WD_SECTION
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
except ImportError:
    print("[ERROR] 缺少依赖 python-docx，请执行: pip install python-docx", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------- 工具
def _ensure_dir(path: str):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
def _set_cjk_font(run, cn_font="宋体", en_font="Times New Roman", size=12,
                  bold=False, color=None):
    """设置中英文字体（中文必须同时设 eastAsia，否则不生效）。"""
    run.font.name = en_font
    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), cn_font)
    rFonts.set(qn("w:ascii"), en_font)
    rFonts.set(qn("w:hAnsi"), en_font)
def _add_page_number(paragraph):
    """插入 PAGE 域代码页码（禁止手写数字）。"""
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = "PAGE"
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
def _setup_page(doc, margin_top=2.54, margin_bottom=2.54,
                margin_left=3.17, margin_right=3.17):
    """设置页边距。"""
    for section in doc.sections:
        section.top_margin = Cm(margin_top)
        section.bottom_margin = Cm(margin_bottom)
        section.left_margin = Cm(margin_left)
        section.right_margin = Cm(margin_right)
def _setup_header_footer(doc, header_text: str = None, page_number: bool = True):
    """设置页眉页脚。"""
    for section in doc.sections:
        # 页眉
        if header_text:
            hp = section.header.paragraphs[0]
            hp.text = ""
            hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = hp.add_run(header_text)
            _set_cjk_font(run, size=9, color=(0x59, 0x59, 0x59))
            # 页眉下划线
            pPr = hp._p.get_or_add_pPr()
            pBdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "BFBFBF")
            pBdr.append(bottom)
            pPr.append(pBdr)
        # 页脚页码
        if page_number:
            fp = section.footer.paragraphs[0]
            fp.text = ""
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r1 = fp.add_run("第 ")
            _set_cjk_font(r1, size=9)
            _add_page_number(fp)
            r2 = fp.add_run(" 页")
            _set_cjk_font(r2, size=9)
def _add_heading(doc, text: str, level: int = 1):
    """添加标题，按级别设置字体。"""
    p = doc.add_paragraph()
    if level == 1:
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run(text)
        _set_cjk_font(run, cn_font="黑体", size=16, bold=True)
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(6)
    elif level == 2:
        run = p.add_run(text)
        _set_cjk_font(run, cn_font="黑体", size=14, bold=True)
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(4)
    else:
        run = p.add_run(text)
        _set_cjk_font(run, cn_font="黑体", size=12, bold=True)
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(3)
    return p
def _add_body(doc, text: str, indent: bool = True):
    """添加正文段落（首行缩进 2 字符，1.5 倍行距）。"""
    p = doc.add_paragraph()
    run = p.add_run(text)
    _set_cjk_font(run, cn_font="宋体", size=12)
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    if indent:
        pf.first_line_indent = Pt(24)   # 2 字符 ≈ 24pt
    pf.space_after = Pt(3)
    return p
def _add_table(doc, headers: list, rows: list, title: str = None):
    """添加带样式的表格。"""
    if title:
        p = doc.add_paragraph()
        run = p.add_run(title)
        _set_cjk_font(run, cn_font="黑体", size=12, bold=True)
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(3)
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    # 表头
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(str(h))
        _set_cjk_font(run, cn_font="黑体", size=10, bold=True, color=(0xFF, 0xFF, 0xFF))
        # 单元格底色
        tcPr = hdr[i]._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "1F4E79")
        tcPr.append(shd)
    # 数据行
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row[:len(headers)]):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            run = p.add_run("" if v is None else str(v))
            _set_cjk_font(run, cn_font="宋体", size=10)
    return t
# ---------------------------------------------------------------- 创建
def create_doc(out_path: str, title: str = None, sections: list = None,
               tables: list = None, header_text: str = None,
               author: str = None, cover: bool = False) -> str:
    """创建规范 Word 文档。
    sections: [(标题, 内容)] 或 [(标题, 内容, 级别)]
    tables:   [(表标题, [表头], [[行数据]])]
    """
    doc = Document()
    _setup_page(doc)
    _setup_header_footer(doc, header_text or title, page_number=True)
    # 封面
    if cover and title:
        for _ in range(6):
            doc.add_paragraph()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(title)
        _set_cjk_font(run, cn_font="黑体", size=26, bold=True, color=(0x1F, 0x4E, 0x79))
        doc.add_paragraph()
        if author:
            p2 = doc.add_paragraph()
            p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r2 = p2.add_run(author)
            _set_cjk_font(r2, cn_font="宋体", size=14)
        p3 = doc.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r3 = p3.add_run(datetime.now().strftime("%Y年%m月%d日"))
        _set_cjk_font(r3, cn_font="宋体", size=12)
        doc.add_page_break()
    # 正文标题
    if title and not cover:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(title)
        _set_cjk_font(run, cn_font="黑体", size=18, bold=True)
        p.paragraph_format.space_after = Pt(12)
    # 章节
    if sections:
        for sec in sections:
            if len(sec) >= 3:
                heading, content, level = sec[0], sec[1], sec[2]
            else:
                heading, content, level = sec[0], sec[1], 1
            if heading:
                _add_heading(doc, heading, level)
            if content:
                # 支持多段落（用 \n 分隔）
                for para in str(content).split("\n"):
                    if para.strip():
                        _add_body(doc, para.strip())
    # 表格
    if tables:
        for tb in tables:
            tb_title = tb[0] if len(tb) > 0 else None
            tb_headers = tb[1] if len(tb) > 1 else []
            tb_rows = tb[2] if len(tb) > 2 else []
            _add_table(doc, tb_headers, tb_rows, tb_title)
            doc.add_paragraph()
    _ensure_dir(out_path)
    doc.save(out_path)
    return out_path
# ---------------------------------------------------------------- 读取
def read_doc(path: str) -> dict:
    """读取 Word 文档全部内容。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    doc = Document(path)
    paragraphs = []
    for p in doc.paragraphs:
        if p.text.strip():
            paragraphs.append({
                "text": p.text.strip(),
                "style": p.style.name if p.style else "",
            })
    tables = []
    for ti, t in enumerate(doc.tables, 1):
        rows = []
        for row in t.rows:
            rows.append([c.text.strip() for c in row.cells])
        tables.append({"table_index": ti, "rows": rows,
                       "row_count": len(rows),
                       "col_count": len(t.columns)})
    return {
        "file": path,
        "paragraph_count": len(paragraphs),
        "paragraphs": paragraphs,
        "table_count": len(tables),
        "tables": tables,
    }
# ---------------------------------------------------------------- 编辑
def edit_doc(path: str, append: str = None, replace: str = None,
             add_heading: str = None, add_table: str = None,
             out_path: str = None) -> str:
    """编辑现有 Word 文档。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    doc = Document(path)
    # 追加内容
    if append:
        for para in append.split("\\n"):
            if para.strip():
                _add_body(doc, para.strip())
    # 追加标题
    if add_heading:
        _add_heading(doc, add_heading, 1)
    # 追加表格（格式：表头1,表头2|值1,值2|值3,值4）
    if add_table:
        parts = add_table.split("|")
        if parts:
            headers = [h.strip() for h in parts[0].split(",")]
            rows = [[c.strip() for c in p.split(",")] for p in parts[1:] if p.strip()]
            _add_table(doc, headers, rows)
    # 替换文本
    if replace:
        if "=" not in replace:
            raise ValueError("--replace 格式应为 旧文本=新文本")
        old, new = replace.split("=", 1)
        count = 0
        for p in doc.paragraphs:
            if old in p.text:
                # 保留第一个 run 的格式，替换文本
                for run in p.runs:
                    if old in run.text:
                        run.text = run.text.replace(old, new)
                        count += 1
                # 跨 run 的情况：合并处理
                if old in p.text and count == 0:
                    full = p.text.replace(old, new)
                    for run in p.runs:
                        run.text = ""
                    if p.runs:
                        p.runs[0].text = full
                    count += 1
        for t in doc.tables:
            for row in t.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            if old in run.text:
                                run.text = run.text.replace(old, new)
                                count += 1
        print(f"[INFO] 替换了 {count} 处")
    out = out_path or path
    _ensure_dir(out)
    doc.save(out)
    return out
# ---------------------------------------------------------------- PDF 导出
def doc_to_pdf(docx_path: str, pdf_path: str):
    """Word → PDF（用 reportlab 重排，保持基本结构）。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle)
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        raise RuntimeError("PDF 导出需要 reportlab，请执行: pip install reportlab")
    # 注册中文字体
    font_name = "Helvetica"
    font_candidates = [
        (r"C:\Windows\Fonts\msyh.ttc", "MSYaHei"),
        (r"C:\Windows\Fonts\simsun.ttc", "SimSun"),
        (r"C:\Windows\Fonts\simhei.ttf", "SimHei"),
        ("/System/Library/Fonts/PingFang.ttc", "PingFang"),
        ("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf", "NotoSansSC"),
    ]
    for fp, name in font_candidates:
        if os.path.isfile(fp):
            try:
                pdfmetrics.registerFont(TTFont(name, fp))
                font_name = name
                break
            except Exception:
                continue
    # 读取 Word 内容
    data = read_doc(docx_path)
    styles = {
        "title": ParagraphStyle("title", fontName=font_name, fontSize=18,
                                leading=26, alignment=1, spaceAfter=14,
                                textColor=colors.HexColor("#1F4E79")),
        "h1": ParagraphStyle("h1", fontName=font_name, fontSize=15,
                             leading=22, spaceBefore=12, spaceAfter=6,
                             textColor=colors.HexColor("#1F4E79")),
        "body": ParagraphStyle("body", fontName=font_name, fontSize=11,
                               leading=18, firstLineIndent=22, spaceAfter=4),
    }
    story = []
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            topMargin=2.54 * cm, bottomMargin=2.54 * cm,
                            leftMargin=3.17 * cm, rightMargin=3.17 * cm,
                            title=os.path.basename(docx_path))
    for item in data["paragraphs"]:
        text = item["text"]
        style_name = item.get("style", "")
        # 简单判断标题级别（按字体大小或样式名）
        if "Heading 1" in style_name or "标题 1" in style_name:
            story.append(Paragraph(text, styles["h1"]))
        elif "Heading" in style_name or "标题" in style_name:
            story.append(Paragraph(text, styles["h1"]))
        elif len(text) < 40 and (text.startswith(("一、", "二、", "三、", "四、", "五、",
                                                  "六、", "七、", "八、", "九、", "十、"))
                                 or text.startswith(("1.", "2.", "3.", "4.", "5."))):
            story.append(Paragraph(text, styles["h1"]))
        else:
            story.append(Paragraph(text, styles["body"]))
    # 表格
    for t in data["tables"]:
        rows = t["rows"]
        if not rows:
            continue
        story.append(Spacer(1, 8))
        tbl = Table(rows, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFBFBF")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
        ]))
        story.append(tbl)
    _ensure_dir(pdf_path)
    doc.build(story)
    return pdf_path
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="Word 文档工具箱")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # create
    p1 = sub.add_parser("create", help="创建新文档")
    p1.add_argument("--out", required=True, help="输出 docx 路径")
    p1.add_argument("--title", default=None, help="文档标题")
    p1.add_argument("--sections", default=None,
                    help="章节，格式 '标题1|内容1||标题2|内容2'（内容内换行用 \\\\n）")
    p1.add_argument("--header", default=None, help="页眉文字")
    p1.add_argument("--author", default=None, help="作者（封面用）")
    p1.add_argument("--cover", action="store_true", help="生成封面")
    # read
    p2 = sub.add_parser("read", help="读取文档")
    p2.add_argument("file", help="docx 路径")
    p2.add_argument("--json", action="store_true", help="输出 JSON")
    # edit
    p3 = sub.add_parser("edit", help="编辑文档")
    p3.add_argument("file", help="docx 路径")
    p3.add_argument("--append", default=None, help="追加段落")
    p3.add_argument("--replace", default=None, help="替换文本，格式 旧=新")
    p3.add_argument("--add-heading", default=None, help="追加标题")
    p3.add_argument("--add-table", default=None, help="追加表格，格式 '表头1,表头2|值1,值2'")
    p3.add_argument("--out", default=None, help="另存为（默认覆盖）")
    # to-pdf
    p4 = sub.add_parser("to-pdf", help="导出 PDF")
    p4.add_argument("docx", help="docx 路径")
    p4.add_argument("--out", required=True, help="输出 pdf 路径")
    args = ap.parse_args()
    try:
        if args.cmd == "create":
            sections = None
            if args.sections:
                sections = []
                for block in args.sections.split("||"):
                    parts = block.split("|", 1)
                    if len(parts) == 2:
                        sections.append((parts[0].strip(), parts[1].replace("\\n", "\n")))
                    elif parts[0].strip():
                        sections.append((parts[0].strip(), ""))
            out = create_doc(args.out, args.title, sections,
                             header_text=args.header, author=args.author, cover=args.cover)
            print(f"[OK] 文档已创建: {out}")
        elif args.cmd == "read":
            data = read_doc(args.file)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"===== {os.path.basename(args.file)} =====")
                print(f"段落数: {data['paragraph_count']}  表格数: {data['table_count']}\n")
                for p in data["paragraphs"][:50]:
                    print(f"  {p['text']}")
                if data["paragraph_count"] > 50:
                    print(f"  ... 共 {data['paragraph_count']} 段")
                for t in data["tables"]:
                    print(f"\n--- 表格 {t['table_index']}（{t['row_count']}行×{t['col_count']}列）---")
                    for row in t["rows"][:10]:
                        print("   ", " | ".join(row))
        elif args.cmd == "edit":
            out = edit_doc(args.file, args.append, args.replace,
                           args.add_heading, args.add_table, args.out)
            print(f"[OK] 文档已更新: {out}")
        elif args.cmd == "to-pdf":
            out = doc_to_pdf(args.docx, args.out)
            print(f"[OK] PDF 已导出: {out}")
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
if __name__ == "__main__":
    main()