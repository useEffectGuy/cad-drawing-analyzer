#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAD 报告生成器 —— 把图纸分析结果一键生成正式 Word 报告 / Excel 清单
用法:
    # 生成完整分析报告（Word）
    python doc_report.py <图纸.dxf> --out 输出目录 --format docx
    # 生成 Word + PDF
    python doc_report.py <图纸.dxf> --out 输出目录 --format docx --pdf
    # 生成工程量清单 Excel
    python doc_report.py <图纸.dxf> --out 输出目录 --format xlsx
功能:
    1. 自动跑完整分析流水线（解析 → CIO → 空间 → 工序 → 材料 → 审图）
    2. 把结果组织成规范的 Word 报告（封面 + 页眉页脚 + 页码 + 表格）
    3. 或导出为多 sheet Excel 清单
    4. 可选同时导出 PDF
依赖: ezdxf, python-docx, openpyxl, pyyaml, networkx
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
def _run(script: str, args: list) -> tuple:
    """运行同目录下的脚本，返回 (成功, stdout, 输出路径)。"""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)
def run_pipeline(dxf: str, work_dir: str) -> dict:
    """跑完整分析流水线，返回各步骤结果路径。"""
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(dxf))[0]
    result = {"base": base, "steps": {}, "data": {}}
    # 1. 基础解析
    ok, out, err = _run("cad_inspect.py", [dxf, "--out", work_dir, "--json"])
    result["steps"]["基础解析"] = ok
    if ok:
        jp = os.path.join(work_dir, f"{base}_分析数据.json")
        if os.path.isfile(jp):
            result["data"]["inspect"] = json.load(open(jp, encoding="utf-8"))
    # 2. CIO 转换
    cio_path = os.path.join(work_dir, f"{base}_CIO.json")
    ok, out, err = _run("cad_cio.py", [dxf, "--out", cio_path])
    result["steps"]["CIO转换"] = ok
    if ok and os.path.isfile(cio_path):
        result["data"]["cio"] = json.load(open(cio_path, encoding="utf-8"))
    # 3. 空间识别
    ok, out, err = _run("cad_space.py", [dxf, "--out", work_dir, "--json"])
    result["steps"]["空间识别"] = ok
    if ok:
        jp = os.path.join(work_dir, f"{base}_空间识别数据.json")
        if os.path.isfile(jp):
            result["data"]["space"] = json.load(open(jp, encoding="utf-8"))
    # 4. 工序识别（需 CIO）
    if os.path.isfile(cio_path):
        ok, out, err = _run("cad_process.py", [cio_path, "--out", work_dir, "--json"])
        result["steps"]["工序识别"] = ok
        if ok:
            jp = os.path.join(work_dir, f"{base}_CIO_工序识别数据.json")
            if os.path.isfile(jp):
                result["data"]["process"] = json.load(open(jp, encoding="utf-8"))
    # 5. 材料识别
    if os.path.isfile(cio_path):
        ok, out, err = _run("cad_material.py", [cio_path, "--out", work_dir, "--json"])
        result["steps"]["材料识别"] = ok
        if ok:
            jp = os.path.join(work_dir, f"{base}_CIO_材料识别数据.json")
            if os.path.isfile(jp):
                result["data"]["material"] = json.load(open(jp, encoding="utf-8"))
    # 6. 规则校验
    if os.path.isfile(cio_path):
        ok, out, err = _run("cad_rules.py", [cio_path, "--out", work_dir, "--json"])
        result["steps"]["规则校验"] = ok
        if ok:
            jp = os.path.join(work_dir, f"{base}_CIO_审图结果.json")
            if os.path.isfile(jp):
                result["data"]["rules"] = json.load(open(jp, encoding="utf-8"))
    return result
# ---------------------------------------------------------------- Word 报告
def build_word_report(pipeline: dict, out_path: str) -> str:
    """把流水线结果组织成规范 Word 报告。"""
    sys.path.insert(0, SCRIPT_DIR)
    from doc_word import create_doc
    d = pipeline["data"]
    base = pipeline["base"]
    sections = []
    tables = []
    # ---- 一、图纸概览 ----
    ov = d.get("inspect", {}).get("overview", {})
    if ov:
        sections.append(("一、图纸概览", f"本报告基于 DXF 图纸「{ov.get('file_name', base)}」自动解析生成，"
                                       f"分析时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}。"))
        tables.append(("表 1-1 图纸基本信息", ["项目", "值"], [
            ["文件大小", f"{ov.get('file_size_kb', '-')} KB"],
            ["DXF 版本", ov.get("dxf_version", "-")],
            ["图形单位", ov.get("units_name", "-")],
            ["模型空间实体数", ov.get("modelspace_entities", "-")],
            ["图层数", ov.get("layer_count", "-")],
            ["用户块定义数", ov.get("block_definitions", "-")],
        ]))
    # ---- 二、专业识别 ----
    cio = d.get("cio", {})
    pm = cio.get("project_meta", {})
    if pm:
        disc_map = {"ARCH": "建筑", "STR": "结构", "PLUMB": "给排水", "ELEC": "电气",
                    "HVAC": "暖通", "SITE": "总图", "DECO": "装饰", "MULTI": "多专业", "UNKNOWN": "未识别"}
        dtype_map = {"FLOOR_PLAN": "平面图", "ELEVATION": "立面图", "SECTION": "剖面图",
                     "DETAIL": "详图", "SYSTEM_DIAGRAM": "系统图", "SITE_PLAN": "总平面图",
                     "SCHEDULE": "表格", "UNKNOWN": "未识别"}
        disc_cn = disc_map.get(pm.get("discipline", ""), pm.get("discipline", ""))
        dtype_cn = dtype_map.get(pm.get("drawing_type", ""), pm.get("drawing_type", ""))
        conf = pm.get("discipline_confidence", 0)
        sections.append(("二、专业识别", f"经图层前缀、图层名、图块名、文字内容四重加权投票，"
                                       f"判定本图为「{disc_cn}」专业{dtype_cn}，置信度 {conf}%。"))
    # ---- 三、图框与图签 ----
    sp = d.get("space", {})
    fr = sp.get("frame", {})
    if fr.get("found"):
        of = fr.get("outer_frame", {})
        tp = fr.get("title_block", {}).get("parsed", {})
        sections.append(("三、图框与图签", f"检测到图框，图幅规格 {of.get('paper_size', '-')}，"
                                       f"尺寸 {of.get('width', '-')} × {of.get('height', '-')}。"))
        rows = []
        if tp.get("drawing_no"):
            rows.append(["图号", tp["drawing_no"]])
        if tp.get("drawing_name"):
            rows.append(["图名", tp["drawing_name"]])
        if tp.get("scale"):
            rows.append(["比例", tp["scale"]])
        if rows:
            tables.append(("表 3-1 图签信息", ["字段", "值"], rows))
    # ---- 四、部位/空间 ----
    if sp.get("space", {}).get("found"):
        spaces = sp["space"]["spaces"]
        total_m2 = round(sum(s.get("area_m2", s["area"]) for s in spaces), 2)
        sections.append(("四、部位/空间识别", f"通过墙线围合的封闭区域检测，识别出 {len(spaces)} 个空间，"
                                           f"总面积约 {total_m2} ㎡（按毫米单位换算）。"))
        rows = [[s["space_id"], s.get("name") or "（未命名）",
                 s.get("area_m2", s["area"]), s["vertex_count"]]
                for s in spaces[:20]]
        tables.append(("表 4-1 封闭区域清单", ["编号", "名称", "面积(㎡)", "顶点数"], rows))
    # ---- 五、工程量 ----
    bl = d.get("inspect", {}).get("blocks", {})
    if bl.get("usage"):
        sections.append(("五、工程量统计", f"图纸共插入 {bl.get('insert_total', 0)} 个块参照，"
                                         f"涉及 {len(bl['usage'])} 种图块。"))
        rows = [[u["block_name"], u["insert_count"], "、".join(u["layers"].keys())]
                for u in bl["usage"][:30]]
        tables.append(("表 5-1 图块工程量", ["图块名称", "数量", "所在图层"], rows))
    # ---- 六、工序识别 ----
    pr = d.get("process", {})
    if pr.get("processes"):
        sections.append(("六、工序识别", f"基于构件类型与属性特征，匹配到 {pr.get('matched_entities', 0)} 个构件，"
                                       f"涉及 {pr.get('process_count', 0)} 种工序。"))
        rows = [[i, p["process"], p["occurrence"], p.get("stage", "")]
                for i, p in enumerate(pr["processes"][:25], 1)]
        tables.append(("表 6-1 工序清单（按施工顺序）", ["序号", "工序", "涉及构件数", "施工阶段"], rows))
    # ---- 七、材料清单 ----
    mt = d.get("material", {})
    if mt.get("by_category"):
        sections.append(("七、材料清单", f"从图纸文字标注中提取到 {mt.get('total_hits', 0)} 处材料信息，"
                                       f"涉及 {mt.get('category_count', 0)} 个类别。"))
        rows = []
        for cat, info in sorted(mt["by_category"].items(), key=lambda x: -x[1]["count"]):
            for s in info["specs"][:10]:
                rows.append([cat, s["spec"], s["count"]])
        tables.append(("表 7-1 材料清单", ["材料类别", "规格/型号", "出现次数"], rows[:40]))
    # ---- 八、审图校验 ----
    rl = d.get("rules", {})
    if rl.get("summary"):
        s = rl["summary"]
        sections.append(("八、审图校验", f"执行 {s.get('rules_executed', 0)} 条规则，"
                                       f"通过 {s.get('rules_passed', 0)} 条，"
                                       f"发现错误 {s.get('total_errors', 0)} 项、"
                                       f"警告 {s.get('total_warnings', 0)} 项、"
                                       f"提示 {s.get('total_infos', 0)} 项。"))
        fails = [r for r in rl.get("results", []) if r.get("status") == "fail"]
        if fails:
            rows = [[r["rule_id"], r["name"], r["severity"], r.get("count", "-"), r.get("message", "")[:40]]
                    for r in fails[:20]]
            tables.append(("表 8-1 问题清单", ["规则ID", "规则名称", "级别", "命中数", "说明"], rows))
        else:
            sections.append(("", "所有规则均通过，未发现规范性问题。"))
    # ---- 九、结论 ----
    conclusion = []
    if pm:
        conclusion.append(f"本图判定为「{disc_map.get(pm.get('discipline',''), '')}」专业。")
    if sp.get("space", {}).get("found"):
        conclusion.append(f"识别出 {len(sp['space']['spaces'])} 个封闭空间。")
    if bl.get("usage"):
        conclusion.append(f"主要构件为 {bl['usage'][0]['block_name']}（{bl['usage'][0]['insert_count']} 个）。")
    if mt.get("by_category"):
        top_cat = max(mt["by_category"].items(), key=lambda x: x[1]["count"])
        conclusion.append(f"材料以「{top_cat[0]}」为主（{top_cat[1]['count']} 处）。")
    if conclusion:
        sections.append(("九、结论", "".join(conclusion)))
    sections.append(("", "本报告由 CAD 图纸智能分析技能自动生成，所有数据来自 DXF 文件实际解析，未做推测填充。"))
    return create_doc(out_path, title=f"{base} 图纸分析报告", sections=sections,
                      tables=tables, header_text=f"{base} 图纸分析报告",
                      author="CAD 图纸智能分析系统", cover=True)
# ---------------------------------------------------------------- Excel 清单
def build_excel_report(pipeline: dict, out_path: str) -> str:
    """生成多 sheet Excel 清单。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    d = pipeline["data"]
    wb = Workbook()
    hf = Font(bold=True, color="FFFFFF", size=11)
    hfill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BFBFBF")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    def style(ws, ncol):
        for c in range(1, ncol + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = hf
            cell.fill = hfill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = bd
        ws.freeze_panes = "A2"
        for r in range(2, ws.max_row + 1):
            for c in range(1, ncol + 1):
                ws.cell(row=r, column=c).border = bd
        for col in ws.columns:
            letter = get_column_letter(col[0].column)
            ml = max((sum(2 if ord(ch) > 127 else 1 for ch in str(c.value)) for c in col if c.value), default=8)
            ws.column_dimensions[letter].width = min(max(ml + 2, 8), 40)
    # Sheet1 图块工程量
    ws1 = wb.active
    ws1.title = "图块工程量"
    ws1.append(["序号", "图块名称", "数量", "所在图层"])
    bl = d.get("inspect", {}).get("blocks", {})
    for i, u in enumerate(bl.get("usage", []), 1):
        ws1.append([i, u["block_name"], u["insert_count"], "、".join(u["layers"].keys())])
    style(ws1, 4)
    # Sheet2 空间清单
    ws2 = wb.create_sheet("空间清单")
    ws2.append(["编号", "名称", "面积", "顶点数"])
    for s in d.get("space", {}).get("space", {}).get("spaces", []):
        ws2.append([s["space_id"], s.get("name") or "", s["area"], s["vertex_count"]])
    style(ws2, 4)
    # Sheet3 工序清单
    ws3 = wb.create_sheet("工序清单")
    ws3.append(["序号", "工序", "涉及构件数", "施工阶段"])
    for i, p in enumerate(d.get("process", {}).get("processes", []), 1):
        ws3.append([i, p["process"], p["occurrence"], p.get("stage", "")])
    style(ws3, 4)
    # Sheet4 材料清单
    ws4 = wb.create_sheet("材料清单")
    ws4.append(["材料类别", "规格/型号", "出现次数"])
    mt = d.get("material", {})
    for cat, info in sorted(mt.get("by_category", {}).items(), key=lambda x: -x[1]["count"]):
        for s in info["specs"]:
            ws4.append([cat, s["spec"], s["count"]])
    style(ws4, 3)
    # Sheet5 审图问题
    ws5 = wb.create_sheet("审图问题")
    ws5.append(["规则ID", "规则名称", "级别", "命中数", "说明"])
    for r in d.get("rules", {}).get("results", []):
        if r.get("status") == "fail":
            ws5.append([r["rule_id"], r["name"], r["severity"], r.get("count", ""), r.get("message", "")])
    style(ws5, 5)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
    return out_path
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="CAD 报告生成器")
    ap.add_argument("dxf", help="DXF 图纸路径")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--format", default="docx", choices=["docx", "xlsx", "both"],
                    help="输出格式")
    ap.add_argument("--pdf", action="store_true", help="同时导出 PDF（仅 docx 格式）")
    ap.add_argument("--work", default=None, help="中间文件目录（默认输出目录下的 _work）")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    work = args.work or os.path.join(args.out, "_work")
    base = os.path.splitext(os.path.basename(args.dxf))[0]
    print("[INFO] 正在跑分析流水线...")
    pipeline = run_pipeline(args.dxf, work)
    print("\n===== 流水线执行情况 =====")
    for step, ok in pipeline["steps"].items():
        print(f"  {'✅' if ok else '❌'} {step}")
    outputs = []
    if args.format in ("docx", "both"):
        docx_path = os.path.join(args.out, f"{base}_分析报告.docx")
        build_word_report(pipeline, docx_path)
        outputs.append(docx_path)
        print(f"\n[OK] Word 报告: {docx_path}")
        if args.pdf:
            try:
                sys.path.insert(0, SCRIPT_DIR)
                from doc_word import doc_to_pdf
                pdf_path = os.path.join(args.out, f"{base}_分析报告.pdf")
                doc_to_pdf(docx_path, pdf_path)
                outputs.append(pdf_path)
                print(f"[OK] PDF 报告: {pdf_path}")
            except Exception as e:
                print(f"[WARN] PDF 导出失败: {e}", file=sys.stderr)
    if args.format in ("xlsx", "both"):
        xlsx_path = os.path.join(args.out, f"{base}_分析清单.xlsx")
        build_excel_report(pipeline, xlsx_path)
        outputs.append(xlsx_path)
        print(f"[OK] Excel 清单: {xlsx_path}")
    print(f"\n共生成 {len(outputs)} 个交付文件")
if __name__ == "__main__":
    main()