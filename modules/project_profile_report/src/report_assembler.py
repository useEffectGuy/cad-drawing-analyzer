#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告组装 —— PPR 模块能力二（概况 + 原有工程量 → 完整报告包）。

约束：
- 工程量结果只读复用，不重算、不覆盖；
- 章节顺序与开关来自 config/report_sections.yaml；
- 模块关闭时不创建目录、不写任何文件；
- 报告显式标注“基于图纸自动识别，不替代正式预算、结算和审图结论”。

用法：
    python report_assembler.py <work目录> --out <report_bundle目录>
           [--profile project_profile.json] [--template report_template.md]
接口：
    assemble_project_report(project_profile, existing_extraction_result, template)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from missing_item_checker import check_missing_items  # noqa: E402
from profile_extractor import (  # noqa: E402
    DEFAULT_MIN_CONFIDENCE,
    extract_project_profile,
    is_enabled,
    load_module_config,
    load_sections,
    load_work_sources,
)

BUNDLE_SCHEMA = "tzp.report_bundle/1.0"
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CN_NUM = "一二三四五六七八九十"


# ---------------------------------------------------------------- 通用助手
def _display(item: dict | None, min_conf: float) -> str:
    if not item:
        return "未识别（需复核）"
    value = item.get("value")
    if value in (None, ""):
        return "未识别（需复核）"
    try:
        low = float(item.get("confidence") or 0) < min_conf
    except (TypeError, ValueError):
        low = True
    return f"{value}（需复核）" if low else str(value)


def _cell(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _source_text(item: dict) -> str:
    source = (item or {}).get("source") or {}
    stype = source.get("type") or "—"
    ref = source.get("ref")
    return f"{stype}（{ref}）" if ref else stype


def _confidence_text(item: dict) -> str:
    try:
        return f"{float(item.get('confidence') or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _spec_text(specs: dict) -> str:
    return "；".join(f"{k}×{v}" for k, v in (specs or {}).items() if k != "未标注")


# ---------------------------------------------------------------- 章节构建
def _section_summary(profile, data, ctx):
    common = profile.get("common") or {}
    q = data.get("quantities") or {}
    qs = q.get("summary") or {}
    ms = (ctx["missing"].get("summary") or {})
    mc = ctx["min_conf"]
    lines = [
        f"- **项目名称**：{_display(common.get('project_name'), mc)}",
        f"- **建设地点**：{_display(common.get('project_location'), mc)}",
        f"- **工程类别**：{_display(common.get('project_category'), mc)}",
        f"- **建设规模**：{_display(common.get('construction_scale'), mc)}",
        f"- **图纸张数**：{_display(common.get('drawing_count'), mc)}",
        f"- **涉及专业**：{_display(common.get('discipline_scope'), mc)}",
        f"- **工程量识别**：构件类型 {qs.get('block_types', 0)} 类，"
        f"构件总数 {qs.get('block_total', 0)} 个，"
        f"线长图层 {qs.get('length_layers', 0)} 个，面积图层 {qs.get('area_layers', 0)} 个",
        f"- **缺项与复核**：缺项 {ms.get('missing', 0)} 项，"
        f"低置信度 {ms.get('low_confidence', 0)} 项",
        "",
        "> 本报告由 PPR 模块在现有图纸解析与工程量结果之上组装，"
        "所有概况字段均带来源与置信度，未识别项不做推测。",
    ]
    return lines


def _section_profile(profile, data, ctx):
    mc = ctx["min_conf"]
    lines = ["### 通用概况", "",
             "| 字段 | 值 | 来源 | 置信度 |",
             "|---|---|---|---|"]
    for item in (profile.get("common") or {}).values():
        lines.append(f"| {_cell(item.get('name'))} | {_cell(_display(item, mc))} "
                     f"| {_cell(_source_text(item))} | {_confidence_text(item)} |")

    by_disc = profile.get("by_discipline") or {}
    if not by_disc:
        lines += ["", "> 未识别到可配置专业，仅输出通用概况。"]
        return lines

    for code, block in by_disc.items():
        lines += ["", f"### {block.get('name', code)}专业概况", "",
                  "| 字段 | 值 | 来源 | 置信度 |",
                  "|---|---|---|---|"]
        for item in (block.get("fields") or {}).values():
            lines.append(f"| {_cell(item.get('name'))} | {_cell(_display(item, mc))} "
                         f"| {_cell(_source_text(item))} | {_confidence_text(item)} |")
    return lines


def _section_basis(profile, data, ctx):
    sources = profile.get("sources") or {}
    files = sources.get("files") or []
    lines = [
        "### 数据来源",
        "",
        f"- 工作目录：`{sources.get('work_dir', '—')}`",
        f"- 文本来源：{sources.get('text_count', 0)} 条（图签、总说明、CIO 文本）",
        f"- CIO 文件：{sources.get('cio_count', 0)} 个",
        f"- 来源文件：{len(files)} 个",
        "",
    ]
    if files:
        lines.append("### 来源文件清单")
        lines.append("")
        for name in files[:15]:
            lines.append(f"- `{name}`")
        if len(files) > 15:
            lines.append(f"- 其余 {len(files) - 15} 个文件略")
        lines.append("")

    lines += [
        "### 识别与组装规则",
        "",
        "- 概况字段按图签/总说明中的“标签：值”形式确定性匹配，按配置的别名顺序取值；",
        "- 每个字段记录来源类型与来源文件，来源置信度由 `config/module.yaml` 统一配置；",
        "- 缺失字段填 `null` 并进入缺项清单，禁止依据经验补值；",
        "- 工程量明细直接复用现有 `quantities.json`，本模块不修改、不重算；",
        "- 专业扩展字段由 `profile_fields_by_discipline.yaml` 配置，识别到专业才启用。",
    ]
    return lines


def _section_quantities(profile, data, ctx):
    q = data.get("quantities") or {}
    qs = q.get("summary") or {}
    blocks = q.get("block_table") or []
    lengths = q.get("length_table") or []
    areas = q.get("area_table") or []

    lines = [
        f"- 构件类型数：{qs.get('block_types', 0)}",
        f"- 构件总数：{qs.get('block_total', 0)}",
        f"- 线长图层数：{qs.get('length_layers', 0)}",
        f"- 面积图层数：{qs.get('area_layers', 0)}",
        "",
        "### 构件明细",
        "",
        "| 构件名称 | 数量 | 单位 | 规格 | 所在图层 |",
        "|---|---|---|---|---|",
    ]
    if blocks:
        for b in blocks[:30]:
            lines.append(f"| {_cell(b.get('block_name'))} | {b.get('quantity', 0)} "
                         f"| {_cell(b.get('unit', '个'))} | {_cell(_spec_text(b.get('specs')))} "
                         f"| {_cell('、'.join((b.get('layers') or {}).keys()))} |")
        if len(blocks) > 30:
            lines.append(f"| 其余 {len(blocks) - 30} 类 | — | — | — | — |")
    else:
        lines.append("| 无 | 0 | — | — | — |")

    if lengths:
        lines += ["", "### 线长汇总", "", "| 图层 | 长度(m) |", "|---|---|"]
        for r in lengths:
            lines.append(f"| {_cell(r.get('layer'))} | {r.get('total_length_m', 0)} |")

    if areas:
        lines += ["", "### 面积汇总", "", "| 图层 | 面积(m²) |", "|---|---|"]
        for r in areas:
            lines.append(f"| {_cell(r.get('layer'))} | {r.get('total_area_m2', 0)} |")

    return lines


def _section_materials(profile, data, ctx):
    materials = data.get("materials") or {}
    by_category = materials.get("by_category") or {}
    if not by_category:
        return ["未提供材料识别结果（deep 链未运行或输入中无可用 DXF），本节留空。"]

    lines = [
        f"- 命中总数：{materials.get('total_hits', 0)}",
        f"- 类别数：{materials.get('category_count', len(by_category))}",
        "",
        "| 类别 | 识别数量 | 主要规格 |",
        "|---|---|---|",
    ]
    for name, info in by_category.items():
        specs = info.get("specs") or []
        spec_text = "；".join(f"{s.get('spec')}×{s.get('count')}" for s in specs[:3])
        lines.append(f"| {_cell(name)} | {info.get('count', 0)} | {_cell(spec_text or '—')} |")
    return lines


def _section_indicators(profile, data, ctx):
    q = data.get("quantities") or {}
    qs = q.get("summary") or {}
    catalog = data.get("catalog") or {}
    missing = ctx["missing"]
    ms = missing.get("summary") or {}

    sheets = catalog.get("total_sheets") or 0
    total = qs.get("block_total", 0)
    per_sheet = f"{total / sheets:.1f}" if sheets else "—"

    pending = q.get("pending") or []
    unclassified = q.get("unclassified") or {}
    high_missing = sum(1 for i in missing.get("items") or []
                       if i.get("status") == "MISSING" and i.get("severity") == "high")

    lines = [
        f"- 平均每张图纸构件数：{per_sheet}（构件 {total} 个 / 图纸 {sheets} 张）",
        f"- 线长/面积图层占比：{qs.get('length_layers', 0)} / {qs.get('area_layers', 0)}",
        "",
        "### 异常与待复核提示",
        "",
    ]
    notes = []
    if pending:
        notes.append(f"- 量算存在待定项 {len(pending)} 项，需结合比例校准结果复核。")
    if unclassified.get("entity_count"):
        notes.append(f"- 未识别图层 {len(unclassified.get('layers') or [])} 个、"
                     f"实体 {unclassified.get('entity_count')} 个，未计入工程量。")
    if ms.get("low_confidence"):
        notes.append(f"- 低置信度概况字段 {ms.get('low_confidence')} 项，已在报告中标注“需复核”。")
    if high_missing:
        notes.append(f"- 必填概况字段缺失 {high_missing} 项，建议补充后再出具正式文件。")
    if not notes:
        notes.append("- 未发现显著异常。")
    lines += notes
    lines += ["", "> 本节为轻量指标提示，不构成造价结论或审图意见。"]
    return lines


def _section_missing(profile, data, ctx):
    missing = ctx["missing"]
    items = missing.get("items") or []
    ms = missing.get("summary") or {}
    if not items:
        return ["未发现缺项或低置信度字段。"]

    lines = [
        f"- 缺项：{ms.get('missing', 0)} 项；低置信度：{ms.get('low_confidence', 0)} 项；"
        f"合计需复核：{ms.get('needs_review', 0)} 项",
        "",
        "| 字段 | 范围 | 状态 | 严重度 | 原因 | 置信度 |",
        "|---|---|---|---|---|---|",
    ]
    status_cn = {"MISSING": "缺项", "LOW_CONFIDENCE": "低置信度"}
    severity_cn = {"high": "高", "medium": "中", "low": "低"}
    for item in items:
        lines.append(
            f"| {_cell(item.get('name'))} | {_cell(item.get('scope'))} "
            f"| {_cell(status_cn.get(item.get('status'), item.get('status')))} "
            f"| {_cell(severity_cn.get(item.get('severity'), item.get('severity')))} "
            f"| {_cell(item.get('reason'))} | {_confidence_text(item)} |")
    lines += ["", "> 标注“需复核”的字段不得直接用于正式预算、结算或审图文件。"]
    return lines


def _section_appendix(profile, data, ctx):
    lines = ["### 报告包文件", ""]
    for name, desc in ctx["bundle_files"]:
        lines.append(f"- `{name}`：{desc}")
    lines += [
        "",
        "### 概况数据说明",
        "",
        "- `project_profile.json` 为结构化概况，字段均含 `value / source / confidence`；",
        "- `missing_items.json` 为缺项与低置信度清单，`needs_review=true` 表示需人工确认；",
        "- 工程量附表为 UTF-8-SIG CSV，列：类别、名称、规格、数量、单位、来源；",
        "- 所有文件均由 PPR 模块自动生成，可随报告一并归档。",
    ]
    return lines


SECTION_BUILDERS = {
    "summary": _section_summary,
    "profile": _section_profile,
    "basis": _section_basis,
    "quantities": _section_quantities,
    "materials": _section_materials,
    "indicators": _section_indicators,
    "missing": _section_missing,
    "appendix": _section_appendix,
}


# ---------------------------------------------------------------- 附表
def _write_quantities_csv(quantities: dict, path: str) -> None:
    rows = []
    for b in quantities.get("block_table") or []:
        rows.append([
            "构件", b.get("block_name", ""), _spec_text(b.get("specs")),
            b.get("quantity", 0), b.get("unit", "个"),
            "、".join((b.get("layers") or {}).keys()),
        ])
    for r in quantities.get("length_table") or []:
        rows.append(["线长", r.get("layer", ""), "",
                     r.get("total_length_m", 0), "m", r.get("layer", "")])
    for r in quantities.get("area_table") or []:
        rows.append(["面积", r.get("layer", ""), "",
                     r.get("total_area_m2", 0), "m²", r.get("layer", "")])

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["类别", "名称", "规格", "数量", "单位", "来源"])
        writer.writerows(rows)


# ---------------------------------------------------------------- 模板
def _read_template(template: str | None) -> str:
    path = template or os.path.join(MODULE_DIR, "templates", "report_template.md")
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "# {{project_name}} 项目报告\n\n{{report_body}}\n\n{{disclaimer}}\n"


# ---------------------------------------------------------------- 主流程
def _default_out_dir(profile: dict, data: dict, config: dict) -> str:
    work = ((profile.get("sources") or {}).get("work_dir")
            or data.get("work_dir") or os.getcwd())
    return os.path.join(work, config.get("output_dir_name", "report_bundle"))


def assemble_project_report(project_profile: dict,
                            existing_extraction_result: dict | None = None,
                            template: str | None = None,
                            out_dir: str | None = None,
                            config: dict | None = None) -> dict:
    cfg = config if config is not None else load_module_config()
    if not is_enabled(cfg):
        return {"enabled": False, "out_dir": None, "files": []}

    profile = project_profile or {}
    data = dict(existing_extraction_result or {})
    if not data.get("work_dir"):
        data["work_dir"] = (profile.get("sources") or {}).get("work_dir", "")

    missing = data.get("missing_items") or check_missing_items(profile, cfg)
    out_dir = out_dir or _default_out_dir(profile, data, cfg)
    os.makedirs(out_dir, exist_ok=True)

    names = {
        "report": cfg.get("report_filename", "project_report.md"),
        "profile": cfg.get("profile_filename", "project_profile.json"),
        "missing": cfg.get("missing_filename", "missing_items.json"),
        "csv": cfg.get("quantities_appendix_filename", "engineering_quantities.csv"),
        "manifest": cfg.get("manifest_filename", "bundle_manifest.json"),
    }

    min_conf = float(cfg.get("min_confidence_for_report", DEFAULT_MIN_CONFIDENCE))
    bundle_files = [
        (names["report"], "项目报告（Markdown）"),
        (names["profile"], "项目概况结构化数据"),
        (names["missing"], "缺项与低置信度清单"),
        (names["csv"], "工程量附表（CSV）"),
    ]
    ctx = {
        "missing": missing,
        "min_conf": min_conf,
        "config": cfg,
        "bundle_files": bundle_files,
    }

    chunks = []
    sections = [s for s in load_sections() if s.get("enabled", True)]
    for idx, sec in enumerate(sections):
        builder = SECTION_BUILDERS.get(sec.get("id"))
        if not builder:
            continue
        num = CN_NUM[idx] if idx < len(CN_NUM) else str(idx + 1)
        chunks.append(f"## {num}、{sec.get('title', sec.get('id'))}")
        chunks.append("")
        chunks.extend(builder(profile, data, ctx))
        chunks.append("")
    body = "\n".join(chunks).strip()

    common = profile.get("common") or {}
    project_name = (common.get("project_name") or {}).get("value") or "工程项目"
    sources = profile.get("sources") or {}
    total_sheets = (data.get("catalog") or {}).get("total_sheets") or "—"
    review_count = (missing.get("summary") or {}).get("needs_review", 0)
    status = "完整（可复核）" if not review_count else f"含 {review_count} 项待复核"
    work_dir = sources.get("work_dir") or data.get("work_dir") or ""
    source_scope = (f"{os.path.basename(work_dir) or '—'}："
                    f"{len(sources.get('files') or [])} 个来源文件 / {total_sheets} 张图纸")

    mapping = {
        "project_name": project_name,
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "module_version": str(cfg.get("version", "1.0")),
        "source_scope": source_scope,
        "report_status": status,
        "report_body": body,
        "disclaimer": cfg.get("disclaimer", "").strip(),
    }
    text = _read_template(template)
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", str(value))
    if "{{report_body}}" in text:
        text = text.replace("{{report_body}}", body)
    if body not in text:
        text = text.rstrip() + "\n\n" + body + "\n"

    paths = {
        "report": os.path.join(out_dir, names["report"]),
        "profile": os.path.join(out_dir, names["profile"]),
        "missing": os.path.join(out_dir, names["missing"]),
        "csv": os.path.join(out_dir, names["csv"]),
    }
    with open(paths["report"], "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    with open(paths["profile"], "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    with open(paths["missing"], "w", encoding="utf-8") as f:
        json.dump(missing, f, ensure_ascii=False, indent=2)
    _write_quantities_csv(data.get("quantities") or {}, paths["csv"])

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "module": {
            "name": cfg.get("module_name", "project_profile_report"),
            "version": str(cfg.get("version", "1.0")),
        },
        "enabled": True,
        "work_dir": work_dir,
        "report_status": status,
        "missing_summary": missing.get("summary") or {},
        "files": [
            {"name": os.path.basename(p), "size_bytes": os.path.getsize(p)}
            for p in paths.values()
        ],
    }
    manifest_path = os.path.join(out_dir, names["manifest"])
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    files = list(paths.values()) + [manifest_path]
    return {
        "enabled": True,
        "out_dir": out_dir,
        "report_status": status,
        "files": files,
    }


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="PPR 报告组装")
    ap.add_argument("work_dir", help="work 目录路径（现有 Skill 产物）")
    ap.add_argument("--out", default=None, help="报告包输出目录")
    ap.add_argument("--profile", default=None, help="可选：已有 project_profile.json")
    ap.add_argument("--template", default=None, help="可选：报告模板路径")
    args = ap.parse_args(argv)

    cfg = load_module_config()
    if not is_enabled(cfg):
        print("[--] PPR 模块已关闭（enable_project_profile_report: false），未生成报告包。")
        return 0

    if args.profile:
        with open(args.profile, "r", encoding="utf-8") as f:
            profile = json.load(f)
        sources = load_work_sources(args.work_dir)
    else:
        sources = load_work_sources(args.work_dir)
        profile = extract_project_profile(sources, config=cfg)

    result = assemble_project_report(
        profile, sources, template=args.template, out_dir=args.out, config=cfg)
    print(f"[OK] 报告组装  {result.get('report_status', '')}  → {result['out_dir']}")
    for path in result["files"]:
        print(f"      {os.path.basename(path)}  {os.path.getsize(path)} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
