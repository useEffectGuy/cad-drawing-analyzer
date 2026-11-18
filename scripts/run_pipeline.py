#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一流水线调度器 —— 图智通唯一编排入口
用法:
    python run_pipeline.py <文件或目录> --work <work目录>
           [--profile standard|deep|all]      # 默认 standard
           [--resume] [--force]               # 断点续跑 / 强制重算
           [--only catalog,wbs] [--skip tables]
           [--with-report] [--snap 50] [--pdf-scale 100] [--quiet | --verbose]
功能:
    1. standard：输入清点 → 格式归一 → 图纸目录 → 专业分类 → 工程量 → 表格 → WBS → 汇总
    2. deep    ：输入清点 → 格式归一 → 体检 → CIO → 空间 → 工序 → 材料 → 审图
    3. 断点续跑：work/.pipeline/manifest.json 记录输入与脚本指纹，命中即跳过
    4. 汇总：work/summary.json（LLM 只读这一个文件）
退出码:
    0 全部 ok/degraded；1 有 stage failed；2 致命（输入不存在、无 DWG 转换器等）
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_common import Out, ROOT_DIR, SCRIPT_DIR, add_output_args  # noqa: E402

SUMMARY_SCHEMA = "tzp.pipeline.summary/1.0"
MANIFEST_SCHEMA = "tzp.pipeline.manifest/1.0"
MAX_WARNINGS = 20
WARN_MAXLEN = 60

STANDARD_STAGES = [
    "inventory", "normalize", "catalog", "classify",
    "measure", "tables", "wbs", "aggregate",
]
DEEP_STAGES = [
    "inventory", "normalize", "inspect", "cio",
    "space", "process", "material", "rules",
]
# 每个 stage 依赖的脚本（用于指纹：脚本自身改动也会失效缓存）
STAGE_SCRIPTS = {
    "inventory": ["inspect_dxf.py"],
    "normalize": ["cad_convert.py"],
    "catalog": ["extract_catalog.py"],
    "classify": ["classify_discipline.py"],
    "measure": ["measure_dxf.py"],
    "tables": ["parse_tables.py", "measure_pdf.py"],
    "wbs": ["map_wbs.py"],
    "aggregate": ["aggregate.py"],
    "inspect": ["cad_inspect.py"],
    "cio": ["cad_cio.py"],
    "space": ["cad_space.py"],
    "process": ["cad_process.py"],
    "material": ["cad_material.py"],
    "rules": ["cad_rules.py"],
}


class Fatal(Exception):
    """致命错误：终止流水线，退出码 2。"""


class SkipStage(Exception):
    """当前 stage 不适用（如输入无 DXF）。"""


# ---------------------------------------------------------------- 基础工具
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _warn(msg) -> str:
    return str(msg).replace("\n", " ")[:WARN_MAXLEN]


def _sig(path: str) -> list:
    """文件指纹（绝对路径 + mtime_ns + size）。"""
    try:
        st = os.stat(path)
        return [os.path.abspath(path), int(st.st_mtime_ns), int(st.st_size)]
    except OSError:
        return [os.path.abspath(path), 0, 0]


def _load_json(path: str):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(obj, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


SUMMARY_MAX_BYTES = 8192


def _truncate_summary(summary: dict, max_bytes: int = SUMMARY_MAX_BYTES) -> dict:
    """保证 summary.json 落盘后不超过 max_bytes。先裁 warnings，再截 stage summary 文本。"""
    import io as _io
    def size_of(obj):
        return len(json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))
    if size_of(summary) <= max_bytes:
        return summary
    # 1) 先裁 warnings
    summary = dict(summary)
    if summary.get("warnings"):
        summary["warnings"] = summary["warnings"][:10]
    # 2) 再把每个 stage 的 summary 截断到 40 字符
    for st in summary.get("stages", []):
        if st.get("summary") and len(st["summary"]) > 40:
            st["summary"] = st["summary"][:40] + "…"
    # 3) 把 deliverables/counts 中的长路径裁剪
    for key in ("deliverables",):
        d = summary.get(key)
        if isinstance(d, dict):
            summary[key] = {k: os.path.basename(v) if isinstance(v, str) else v for k, v in d.items()}
    return summary


def _script(name: str) -> str:
    return os.path.join(SCRIPT_DIR, name)


def _rule(name: str) -> str:
    return os.path.join(ROOT_DIR, "rules", name)


class Ctx:
    """流水线上下文。"""

    def __init__(self, input_path: str, work: str, snap: float = 50.0,
                 pdf_scale: float = 1.0, dpi: int = 200, tolerance: int = 3000):
        self.input = input_path
        self.work = work
        self.snap = snap
        self.pdf_scale = pdf_scale
        self.dpi = dpi
        self.tolerance = tolerance
        self.inv = {}
        self.input_sig = []

    def dxf_files(self) -> list:
        out = []
        for it in self.inv.get("items", []):
            if it.get("format") == "dxf":
                out.append(it["path"])
        norm = os.path.join(self.work, "normalized")
        if os.path.isdir(norm):
            for f in sorted(os.listdir(norm)):
                if f.lower().endswith(".dxf"):
                    out.append(os.path.join(norm, f))
        return list(dict.fromkeys(out))

    def pdf_files(self) -> list:
        return [it["path"] for it in self.inv.get("items", []) if it.get("format") == "pdf"]

    def primary_dxf(self):
        files = self.dxf_files()
        return files[0] if files else None

    def base_of(self, path: str) -> str:
        return os.path.splitext(os.path.basename(path))[0]

    def cio_path(self):
        dxf = self.primary_dxf()
        if not dxf:
            return None
        return os.path.join(self.work, f"{self.base_of(dxf)}_CIO.json")


def _result(status: str, output=None, summary: str = "", warnings=None,
            outputs=None, data=None) -> dict:
    return {
        "status": status,
        "output": output,
        "outputs": outputs or ([output] if output else []),
        "summary": summary,
        "warnings": warnings or [],
        "data": data,
    }


# ---------------------------------------------------------------- standard 链
def stage_inventory(ctx: Ctx) -> dict:
    from inspect_dxf import inventory

    inv = inventory([ctx.input])
    path = os.path.join(ctx.work, "inventory.json")
    _dump_json(inv, path)
    ctx.inv = inv
    ctx.input_sig = [_sig(it["path"]) for it in inv.get("items", [])]
    fmts = inv.get("format_counts", {})
    detail = " ".join(f"{k} {v}" for k, v in sorted(fmts.items()))
    return _result("ok", path, f"{inv['total_files']} 个文件 ({detail})")


def stage_normalize(ctx: Ctx) -> dict:
    from cad_convert import detect_converter, ensure_dxf

    dwgs = [it["path"] for it in ctx.inv.get("items", []) if it.get("format") == "dwg"]
    if not dwgs:
        raise SkipStage("输入无 DWG")
    if not detect_converter()["found"]:
        raise Fatal("检测到 DWG 但未找到转换器：请安装 ODA File Converter 或 LibreDWG")
    out_dir = os.path.join(ctx.work, "normalized")
    outs, warns = [], []
    for p in dwgs:
        try:
            outs.append(ensure_dxf(p, out_dir))
        except Exception as e:
            warns.append(_warn(f"{os.path.basename(p)} 转换失败: {e}"))
    if not outs:
        return _result("failed", None, "DWG 转换全部失败", warns)
    status = "degraded" if warns else "ok"
    return _result(status, outs[0], f"{len(outs)}/{len(dwgs)} 张 DWG 已转换",
                   warns, outputs=outs)


def stage_catalog(ctx: Ctx) -> dict:
    from extract_catalog import extract_catalog

    cat = extract_catalog(ctx.input)
    path = os.path.join(ctx.work, "catalog.json")
    _dump_json(cat, path)
    warns = []
    if not cat.get("has_catalog_table"):
        warns.append(_warn("未找到图纸目录表，已按图框补建"))
    status = "degraded" if warns else "ok"
    return _result(status, path, f"{cat.get('total_sheets', 0)} 张", warns)


def stage_classify(ctx: Ctx) -> dict:
    from classify_discipline import classify_catalog

    cat = _load_json(os.path.join(ctx.work, "catalog.json"))
    if not cat:
        raise SkipStage("catalog.json 不存在")
    res = classify_catalog(cat)
    path = os.path.join(ctx.work, "catalog_classified.json")
    _dump_json(res, path)
    low = sum(1 for it in res.get("items", []) if str(it.get("置信度", "")).startswith("低"))
    warns = [f"{low} 张置信度为「低」"] if low else []
    status = "degraded" if low else "ok"
    return _result(status, path, f"{len(res.get('disciplines', []))} 专业", warns)


def _merge_quantities(items: list) -> dict:
    """合并多张图纸的工程量（单张时原样返回）。"""
    if len(items) == 1:
        return items[0][1]
    blocks = {}
    lengths = {}
    areas = {}
    for _, r in items:
        for b in r.get("block_table", []):
            name = b.get("block_name", "")
            cur = blocks.setdefault(name, {
                "block_name": name, "quantity": 0, "unit": b.get("unit", "个"),
                "layers": {}, "specs": {},
            })
            cur["quantity"] += b.get("quantity", 0)
            for k, v in (b.get("layers") or {}).items():
                cur["layers"][k] = cur["layers"].get(k, 0) + v
            for k, v in (b.get("specs") or {}).items():
                cur["specs"][k] = cur["specs"].get(k, 0) + v
        for row in r.get("length_table", []):
            lengths[row["layer"]] = lengths.get(row["layer"], 0) + row.get("total_length_mm", 0)
        for row in r.get("area_table", []):
            areas[row["layer"]] = areas.get(row["layer"], 0) + row.get("total_area_mm2", 0)

    block_table = sorted(blocks.values(), key=lambda x: -x["quantity"])
    length_table = [
        {"layer": k, "total_length_mm": round(v, 3), "total_length_m": round(v / 1000, 3), "unit": "m"}
        for k, v in sorted(lengths.items(), key=lambda x: -x[1]) if v > 0
    ]
    area_table = [
        {"layer": k, "total_area_mm2": round(v, 3), "total_area_m2": round(v / 1e6, 3), "unit": "m²"}
        for k, v in sorted(areas.items(), key=lambda x: -x[1]) if v > 0
    ]
    return {
        "schema": "tzp.quantities/1.0",
        "file": f"{len(items)} 张图纸",
        "block_table": block_table,
        "length_table": length_table,
        "area_table": area_table,
        "summary": {
            "block_types": len(block_table),
            "block_total": sum(b["quantity"] for b in block_table),
            "length_layers": len(length_table),
            "area_layers": len(area_table),
        },
    }


def stage_measure(ctx: Ctx) -> dict:
    from measure_dxf import measure_dxf

    files = ctx.dxf_files()
    if not files:
        raise SkipStage("输入无 DXF")
    items, warns = [], []
    for p in files:
        try:
            items.append((p, measure_dxf(p)))
        except Exception as e:
            warns.append(_warn(f"{os.path.basename(p)} 失败: {e}"))
    if not items:
        return _result("failed", None, "全部图纸量算失败", warns)
    merged = _merge_quantities(items)
    path = os.path.join(ctx.work, "quantities.json")
    _dump_json(merged, path)
    s = merged["summary"]
    status = "degraded" if warns else "ok"
    return _result(status, path,
                   f"{s['block_types']} 类构件 / {s['block_total']} 个", warns)


def stage_tables(ctx: Ctx) -> dict:
    from measure_pdf import measure_pdfs
    from parse_tables import parse_pdf_tables

    pdfs = ctx.pdf_files()
    if not pdfs:
        raise SkipStage("输入无 PDF")
    tables = []
    for p in pdfs:
        tables.extend(parse_pdf_tables(p))
    path = os.path.join(ctx.work, "tables.json")
    _dump_json(tables, path)

    # PDF 矢量几何量算：独立产物，不改 tables.json 结构
    geo = measure_pdfs(pdfs, ctx.pdf_scale)
    geo_path = os.path.join(ctx.work, "pdf_quantities.json")
    _dump_json(geo, geo_path)

    t = geo.get("totals", {})
    warns = [_warn(e) for e in geo.get("errors", [])]
    scanned = t.get("scanned_pages", 0)
    if scanned:
        warns.append(_warn(f"{scanned} 页为扫描件，几何量算跳过（建议视觉识别）"))
    status = "degraded" if warns else "ok"
    summary = (f"{len(tables)} 个表 / 线条 {t.get('line_count', 0)} 条"
               f" / 矩形 {t.get('rect_count', 0)} 个")
    return _result(status, path, summary, warns, outputs=[path, geo_path])


def stage_wbs(ctx: Ctx) -> dict:
    from map_wbs import build_wbs_tree

    q = _load_json(os.path.join(ctx.work, "quantities.json"))
    if not q:
        raise SkipStage("无工程量数据")
    catalog = (_load_json(os.path.join(ctx.work, "catalog_classified.json"))
               or _load_json(os.path.join(ctx.work, "catalog.json")))
    wbs = build_wbs_tree(q, catalog)
    path = os.path.join(ctx.work, "wbs.json")
    _dump_json(wbs, path)
    return _result("ok", path, f"{wbs.get('division_count', 0)} 个分部")


def stage_aggregate(ctx: Ctx) -> dict:
    import aggregate as agg

    common = [
        os.path.join(ctx.work, "catalog.json"),
        os.path.join(ctx.work, "catalog_classified.json"),
        os.path.join(ctx.work, "quantities.json"),
        os.path.join(ctx.work, "wbs.json"),
    ]
    if not any(os.path.isfile(p) for p in common):
        raise SkipStage("无中间产物")
    excel = os.path.join(ctx.work, "工程量汇总统计.xlsx")
    report = os.path.join(ctx.work, "分析报告.md")
    agg.generate_excel(ctx.work, excel)
    agg.generate_report(ctx.work, report)
    return _result("ok", excel, "Excel + Markdown", outputs=[excel, report])


def assemble_report_bundle(work: str) -> dict:
    """调用 PPR 模块组装报告包；关闭/失败均显式返回状态，不静默。"""
    module_src = os.path.join(ROOT_DIR, "modules", "project_profile_report", "src")
    if module_src not in sys.path:
        sys.path.insert(0, module_src)
    try:
        from profile_extractor import (
            extract_project_profile, is_enabled, load_module_config, load_work_sources)
        from report_assembler import assemble_project_report

        cfg = load_module_config()
        if not is_enabled(cfg):
            return {"status": "skipped",
                    "reason": "PPR 模块已关闭（enable_project_profile_report: false）"}
        sources = load_work_sources(work)
        profile = extract_project_profile(sources, config=cfg)
        res = assemble_project_report(profile, sources, config=cfg)
        if not res.get("enabled"):
            return {"status": "skipped", "reason": "PPR 模块未启用"}
        return {
            "status": "ok",
            "out_dir": res["out_dir"],
            "report_status": res.get("report_status", ""),
            "files": [os.path.basename(p) for p in res.get("files", [])],
        }
    except Exception as e:
        return {"status": "failed", "error": _warn(e)}


# ---------------------------------------------------------------- deep 链
def _require_dxf(ctx: Ctx) -> str:
    dxf = ctx.primary_dxf()
    if not dxf:
        raise SkipStage("输入无 DXF")
    return dxf


def _require_cio(ctx: Ctx) -> str:
    path = ctx.cio_path()
    if not path or not os.path.isfile(path):
        raise SkipStage("上游 CIO 缺失")
    return path


def deep_inspect(ctx: Ctx) -> dict:
    import cad_inspect

    dxf = _require_dxf(ctx)
    data = cad_inspect.inspect_dxf(dxf, ctx.work, as_json=True)
    base = ctx.base_of(dxf)
    md = os.path.join(ctx.work, f"{base}_分析报告.md")
    js = os.path.join(ctx.work, f"{base}_分析数据.json")
    ent = data.get("overview", {}).get("modelspace_entities", "-")
    return _result("ok", js, f"模型空间实体 {ent}", outputs=[js, md], data=data)


def deep_cio(ctx: Ctx) -> dict:
    from cad_cio import CioConverter

    dxf = _require_dxf(ctx)
    cio = CioConverter(dxf, None).convert()
    path = os.path.join(ctx.work, f"{ctx.base_of(dxf)}_CIO.json")
    _dump_json(cio, path)
    pm = cio.get("project_meta", {})
    return _result("ok", path,
                   f"{pm.get('discipline', '-')} / {len(cio.get('parsed_entities', []))} 实体",
                   data=cio)


def deep_space(ctx: Ctx) -> dict:
    import ezdxf

    from cad_space import ElevationParser, FrameDetector, SpaceDetector, write_space_md

    dxf = _require_dxf(ctx)
    doc = ezdxf.readfile(dxf)
    frame = FrameDetector(doc).detect()
    sd = SpaceDetector(doc, ctx.snap)
    space = sd.detect()
    assignment = sd.assign_components(space.get("spaces", []))
    elevation = ElevationParser(doc).parse()
    result = {
        "schema": "tzp.space/1.0",
        "file_name": os.path.basename(dxf),
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "frame": frame,
        "space": space,
        "component_assignment": assignment,
        "elevation": elevation,
    }
    base = ctx.base_of(dxf)
    md = os.path.join(ctx.work, f"{base}_空间识别报告.md")
    write_space_md(result, md)
    path = os.path.join(ctx.work, f"{base}_空间识别数据.json")
    _dump_json(result, path)
    return _result("ok", path, f"{space.get('space_count', 0)} 个空间",
                   outputs=[path, md], data=result)


def deep_process(ctx: Ctx) -> dict:
    from cad_process import ProcessEngine, write_process_md

    cio_path = _require_cio(ctx)
    cio = _load_json(cio_path)
    engine = ProcessEngine(_rule("process_rules.yaml"))
    result = engine.run(cio)
    base = ctx.base_of(cio_path)
    md = os.path.join(ctx.work, f"{base}_工序识别报告.md")
    write_process_md(result, cio, md)
    path = os.path.join(ctx.work, f"{base}_工序识别数据.json")
    _dump_json(result, path)
    return _result("ok", path, f"{result.get('process_count', 0)} 种工序",
                   outputs=[path, md], data=result)


def deep_material(ctx: Ctx) -> dict:
    from cad_material import MaterialExtractor, write_material_md

    cio_path = _require_cio(ctx)
    cio = _load_json(cio_path)
    result = MaterialExtractor(_rule("material_dict.yaml")).extract(cio)
    base = ctx.base_of(cio_path)
    md = os.path.join(ctx.work, f"{base}_材料识别报告.md")
    write_material_md(result, cio, md)
    path = os.path.join(ctx.work, f"{base}_材料识别数据.json")
    _dump_json(result, path)
    return _result("ok", path, f"{result.get('category_count', 0)} 个类别",
                   outputs=[path, md], data=result)


def deep_rules(ctx: Ctx) -> dict:
    from cad_rules import RuleEngine, write_rules_md

    cio_path = _require_cio(ctx)
    cio = _load_json(cio_path)
    engine = RuleEngine(_rule("default_rules.yaml"), enable_cross_discipline=False)
    result = engine.run(cio)
    base = ctx.base_of(cio_path)
    md = os.path.join(ctx.work, f"{base}_审图报告.md")
    write_rules_md(result, cio, md)
    path = os.path.join(ctx.work, f"{base}_审图结果.json")
    _dump_json(result, path)
    s = result.get("summary", {})
    return _result("ok", path,
                   f"错误 {s.get('total_errors', 0)} / 警告 {s.get('total_warnings', 0)}",
                   outputs=[path, md], data=result)


STAGE_FUNCS = {
    "inventory": stage_inventory,
    "normalize": stage_normalize,
    "catalog": stage_catalog,
    "classify": stage_classify,
    "measure": stage_measure,
    "tables": stage_tables,
    "wbs": stage_wbs,
    "aggregate": stage_aggregate,
    "inspect": deep_inspect,
    "cio": deep_cio,
    "space": deep_space,
    "process": deep_process,
    "material": deep_material,
    "rules": deep_rules,
}


# ---------------------------------------------------------------- 调度
def _stage_fp(ctx: Ctx, sid: str) -> str:
    parts = [ctx.input_sig, sid, round(float(ctx.snap), 3),
             round(float(ctx.pdf_scale), 3),
             [_sig(_script(s)) for s in STAGE_SCRIPTS.get(sid, [])]]
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _hydrate(ctx: Ctx, sid: str):
    """resume 跳过 inventory 时，从磁盘恢复上下文。"""
    if sid == "inventory" and not ctx.inv:
        ctx.inv = _load_json(os.path.join(ctx.work, "inventory.json"))
        ctx.input_sig = [_sig(it["path"]) for it in ctx.inv.get("items", [])]


def _emit(out: Out, sid: str, res: dict):
    if out.quiet:
        return
    sec = f"{res.get('ms', 0) / 1000:.2f}s"
    status = res.get("status")
    if status == "ok":
        out.ok(f"{sid:<10}", res.get("output"), sec, res.get("summary", ""))
    elif status == "degraded":
        print(f"[!] {sid:<10}  {sec}  {res.get('summary', '')}  → {res.get('output')}")
    elif status == "skipped":
        print(f"[--] {sid:<10}  {sec}  {res.get('reason', '跳过')}")
    elif status == "failed":
        print(f"[X] {sid:<10}  {sec}  {res.get('summary', '失败')}")
    for w in res.get("warnings", [])[:5]:
        out.detail(f"     {w}")


def _run_stage(ctx: Ctx, sid: str, manifest: dict, resume: bool, force: bool,
               verbose: bool) -> dict:
    fp = _stage_fp(ctx, sid)
    prev = manifest.get("stages", {}).get(sid)
    if resume and not force and prev and prev.get("fp") == fp:
        if all(os.path.exists(p) for p in prev.get("outputs", [])):
            _hydrate(ctx, sid)
            return {"status": "skipped", "reason": "指纹命中", "ms": 0,
                    "fp": fp, "output": prev.get("output"),
                    "outputs": prev.get("outputs", []), "summary": "", "warnings": []}

    t0 = time.time()
    sink = io.StringIO()
    try:
        if verbose:
            res = STAGE_FUNCS[sid](ctx)
        else:
            with contextlib.redirect_stdout(sink):
                res = STAGE_FUNCS[sid](ctx)
    except SkipStage as e:
        res = {"status": "skipped", "reason": str(e), "output": None, "outputs": [],
               "summary": "", "warnings": []}
    except Fatal:
        raise
    except Exception as e:
        res = {"status": "failed", "reason": "", "summary": _warn(e),
               "output": None, "outputs": [], "warnings": [_warn(e)]}
    res["ms"] = int((time.time() - t0) * 1000)
    res["fp"] = fp
    return res


def _summary_entry(sid: str, res: dict) -> dict:
    entry = {"id": sid, "status": res["status"], "ms": res.get("ms", 0)}
    if res.get("output"):
        entry["output"] = res["output"]
    if res["status"] == "skipped":
        entry["reason"] = res.get("reason", "")
    warns = res.get("warnings", [])[:MAX_WARNINGS]
    if warns:
        entry["warnings"] = warns
    return entry


def run_pipeline(input_path: str, work: str, profile: str = "standard",
                 resume: bool = False, force: bool = False,
                 only=None, skip=None, snap: float = 50.0,
                 pdf_scale: float = 1.0, dpi: int = 200,
                 tolerance: int = 3000, isolation: bool = False,
                 with_report: bool = False,
                 out: Out = None) -> dict:
    out = out or Out()
    if not os.path.exists(input_path):
        raise Fatal(f"输入不存在: {input_path}")
    os.makedirs(work, exist_ok=True)

    base_order = {
        "standard": STANDARD_STAGES,
        "deep": DEEP_STAGES,
        "all": STANDARD_STAGES + [s for s in DEEP_STAGES
                                  if s not in ("inventory", "normalize")],
    }[profile]
    order = list(base_order)
    if only:
        want = {s.strip() for s in only if s.strip()}
        unknown = want - set(base_order)
        if unknown:
            raise Fatal(f"--only 含 {profile} profile 未定义的 stage: {','.join(sorted(unknown))}")
        order = [s for s in order if s in want]
    if skip:
        drop = {s.strip() for s in skip if s.strip()}
        order = [s for s in order if s not in drop]

    manifest_path = os.path.join(work, ".pipeline", "manifest.json")
    manifest = _load_json(manifest_path) or {"schema": MANIFEST_SCHEMA, "stages": {}}
    ctx = Ctx(input_path, work, snap, pdf_scale, dpi, tolerance)

    stage_results = {}
    failed = []
    for sid in order:
        res = _run_stage(ctx, sid, manifest, resume, force, getattr(out, "verbose", False))
        stage_results[sid] = res
        if res["status"] == "failed":
            failed.append(sid)
        _emit(out, sid, res)
        # 缓存记录：failed 也写入（下次 --resume 会因输出不存在而重跑）
        manifest.setdefault("stages", {})[sid] = {
            "fp": res.get("fp", ""),
            "status": res["status"],
            "ms": res.get("ms", 0),
            "output": res.get("output"),
            "outputs": res.get("outputs", []),
        }

    # ---- 汇总
    cat = (_load_json(os.path.join(work, "catalog_classified.json"))
           or _load_json(os.path.join(work, "catalog.json")))
    q = _load_json(os.path.join(work, "quantities.json"))
    qs = q.get("summary", {}) if q else {}
    deliverables = {}
    for key, name in (("excel", "工程量汇总统计.xlsx"),
                      ("report", "分析报告.md"),
                      ("catalog", "catalog.json")):
        p = os.path.join(work, name)
        if os.path.isfile(p):
            deliverables[key] = p

    # ---- L4 报告交付闭环（可选，默认不调用 PPR）
    report_bundle = None
    if with_report:
        report_bundle = assemble_report_bundle(work)
        if report_bundle.get("status") == "ok":
            deliverables["report_bundle"] = report_bundle["out_dir"]

    warnings = []
    for sid, res in stage_results.items():
        for w in res.get("warnings", []):
            if len(warnings) < MAX_WARNINGS:
                warnings.append(_warn(w))
    if report_bundle:
        if report_bundle["status"] == "failed":
            failed.append("report")
            if len(warnings) < MAX_WARNINGS:
                warnings.append(_warn(f"报告包组装失败: {report_bundle.get('error', '')}"))
        elif not out.quiet:
            if report_bundle["status"] == "ok":
                print(f"[REPORT] {report_bundle['out_dir']}  "
                      f"{report_bundle.get('report_status', '')}")
            else:
                print(f"[--] report     跳过：{report_bundle.get('reason', '')}")
    deep_ids = {s for s in DEEP_STAGES if s not in ("inventory", "normalize")}
    n_dxf = len(ctx.dxf_files())
    if n_dxf > 1 and any(s in order for s in deep_ids) and len(warnings) < MAX_WARNINGS:
        warnings.insert(0, _warn(f"deep 仅处理首张图纸（共 {n_dxf} 张）"))

    summary = {
        "schema": SUMMARY_SCHEMA,
        "generated_at": _now_iso(),
        "profile": profile,
        "input": {
            "path": os.path.abspath(input_path),
            "files": ctx.inv.get("total_files", 0),
            "formats": ctx.inv.get("format_counts", {}),
        },
        "work_dir": os.path.abspath(work),
        "stages": [_summary_entry(sid, stage_results[sid]) for sid in order],
        "counts": {
            "sheets": cat.get("total_sheets", 0),
            "disciplines": len(cat.get("disciplines", [])),
            "block_types": qs.get("block_types", 0),
            "block_total": qs.get("block_total", 0),
        },
        "deliverables": deliverables,
        "failed": failed,
        "warnings": warnings,
    }
    if with_report:
        summary["report_bundle"] = report_bundle
    summary_path = os.path.join(work, "summary.json")
    _dump_json(_truncate_summary(summary), summary_path)

    manifest["generated_at"] = _now_iso()
    _dump_json(manifest, manifest_path)

    print(f"[SUMMARY] {summary_path}")
    return summary


# ---------------------------------------------------------------- deep 链复用（供 doc_report）
DEEP_LABELS = [
    ("inspect", "基础解析"),
    ("cio", "CIO转换"),
    ("space", "空间识别"),
    ("process", "工序识别"),
    ("material", "材料识别"),
    ("rules", "规则校验"),
]


def run_deep(dxf: str, work_dir: str, isolation: bool = False) -> dict:
    """跑完整分析流水线，返回 {base, steps, data}（与旧 doc_report.run_pipeline 一致）。"""
    if isolation:
        return _run_deep_isolated(dxf, work_dir)
    os.makedirs(work_dir, exist_ok=True)
    from cad_convert import ensure_dxf

    dxf = ensure_dxf(dxf)
    ctx = Ctx(dxf, work_dir)
    ctx.inv = {"items": [{"path": dxf, "format": "dxf"}]}
    result = {"base": ctx.base_of(dxf), "steps": {}, "data": {}}
    sink = io.StringIO()
    for sid, label in DEEP_LABELS:
        try:
            with contextlib.redirect_stdout(sink):
                res = STAGE_FUNCS[sid](ctx)
            result["steps"][label] = True
            result["data"][sid] = res["data"]
        except SkipStage:
            result["steps"][label] = False
        except Exception:
            result["steps"][label] = False
    return result


def _run_deep_isolated(dxf: str, work_dir: str) -> dict:
    """原 subprocess 隔离实现（--isolation 时保留）。"""
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(dxf))[0]
    result = {"base": base, "steps": {}, "data": {}}

    def run(script, args):
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, script)] + args
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
            return r.returncode == 0
        except Exception:
            return False

    steps = [
        ("基础解析", "cad_inspect.py", [dxf, "--out", work_dir, "--json"],
         f"{base}_分析数据.json", "inspect"),
        ("CIO转换", "cad_cio.py", [dxf, "--out", f"{work_dir}/{base}_CIO.json"],
         f"{base}_CIO.json", "cio"),
        ("空间识别", "cad_space.py", [dxf, "--out", work_dir, "--json"],
         f"{base}_空间识别数据.json", "space"),
        ("工序识别", "cad_process.py", [f"{work_dir}/{base}_CIO.json", "--out", work_dir, "--json"],
         f"{base}_CIO_工序识别数据.json", "process"),
        ("材料识别", "cad_material.py", [f"{work_dir}/{base}_CIO.json", "--out", work_dir, "--json"],
         f"{base}_CIO_材料识别数据.json", "material"),
        ("规则校验", "cad_rules.py", [f"{work_dir}/{base}_CIO.json", "--out", work_dir, "--json"],
         f"{base}_CIO_审图结果.json", "rules"),
    ]
    for label, script, args, fname, key in steps:
        if key in ("process", "material", "rules") and not os.path.isfile(
                os.path.join(work_dir, f"{base}_CIO.json")):
            result["steps"][label] = False
            continue
        ok = run(script, args)
        result["steps"][label] = ok
        jp = os.path.join(work_dir, fname)
        if ok and os.path.isfile(jp):
            result["data"][key] = _load_json(jp)
    return result


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="图智通统一流水线调度器")
    ap.add_argument("input", help="图纸文件或目录路径")
    ap.add_argument("--work", default="work", help="中间产物目录（默认 work）")
    ap.add_argument("--profile", default="standard",
                    choices=["standard", "deep", "all"], help="流水线范围")
    ap.add_argument("--resume", action="store_true", help="断点续跑：指纹命中则跳过")
    ap.add_argument("--force", action="store_true", help="强制重算，忽略缓存")
    ap.add_argument("--only", default=None, help="只跑指定 stage（逗号分隔）")
    ap.add_argument("--skip", default=None, help="跳过指定 stage（逗号分隔）")
    ap.add_argument("--snap", type=float, default=50.0, help="空间识别端点吸附容差")
    ap.add_argument("--pdf-scale", type=float, default=1.0,
                    help="PDF 几何量算比例分母，如 1:100 传 100（默认 1:1）")
    ap.add_argument("--dpi", type=int, default=200, help="PDF 渲染 DPI（扫描兜底时使用）")
    ap.add_argument("--tolerance", type=int, default=3000, help="空间识别端点吸附容差（兼容别名）")
    ap.add_argument("--isolation", action="store_true", help="deep 链用 subprocess 隔离模式（默认函数直调）")
    ap.add_argument("--with-report", action="store_true",
                    help="流水线结束后调用 PPR 组装报告包（默认关闭，不产生额外文件）")
    add_output_args(ap)
    args = ap.parse_args(argv)
    out = Out.from_args(args)

    try:
        summary = run_pipeline(
            args.input, args.work, profile=args.profile,
            resume=args.resume, force=args.force,
            only=args.only.split(",") if args.only else None,
            skip=args.skip.split(",") if args.skip else None,
            snap=args.snap, pdf_scale=args.pdf_scale,
            dpi=args.dpi, tolerance=args.tolerance, isolation=args.isolation,
            with_report=args.with_report,
            out=out,
        )
    except Fatal as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(1 if summary["failed"] else 0)


if __name__ == "__main__":
    main()
