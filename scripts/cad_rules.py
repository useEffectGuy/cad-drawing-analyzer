#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则引擎 —— 基于 YAML 规则库对 CIO 数据执行审图校验
用法:
    python cad_rules.py <cio.json> [--rules rules/default_rules.yaml] [--out result.json]
功能:
    1. 加载 YAML 规则库（条件 + 动作 DSL）
    2. 对 CIO 数据逐条执行规则
    3. 将校验结果追加到实体的 validation_errors 字段（分析层只读不修改原数据）
    4. 输出校验报告
设计说明:
    规则与代码分离。工程师只需编辑 YAML 文件即可增删审查规则，无需修改 Python 代码。
依赖: pyyaml
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
try:
    import yaml
except ImportError:
    print("[ERROR] 缺少依赖 pyyaml，请执行: pip install pyyaml", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------- 算子实现
# 每个算子接收 (cio, condition) 返回 (命中数量, 命中实体ID列表, 附加信息)
def _op_zero_length_line(cio, cond):
    thr = cond.get("threshold", 1e-6)
    hits = []
    for e in cio["parsed_entities"]:
        g = e.get("geometry", {})
        if g.get("type") == "LINE":
            coords = g.get("coordinates", [])
            if len(coords) == 2:
                try:
                    if math.dist(coords[0], coords[1]) < thr:
                        hits.append(e["entity_id"])
                except Exception:
                    pass
    return len(hits), hits, {}
def _op_zero_radius_circle(cio, cond):
    thr = cond.get("threshold", 1e-6)
    hits = [e["entity_id"] for e in cio["parsed_entities"]
            if e.get("geometry", {}).get("type") == "CIRCLE"
            and (e["geometry"].get("radius") or 0) < thr]
    return len(hits), hits, {}
def _get_unit_scale(cio) -> float:
    """按 CIO 单位返回阈值换算系数（把"米级默认阈值"适配到图形单位）。
    毫米图纸：0.1 的极短线阈值应放大 1000 倍。"""
    units = cio.get("global_context", {}).get("units", "unitless")
    return {"mm": 1000.0, "cm": 100.0, "m": 1.0,
            "inch": 39.37, "foot": 3.28}.get(units, 1.0)
def _op_short_line(cio, cond):
    thr = cond.get("threshold", 0.1) * _get_unit_scale(cio)
    hits = []
    for e in cio["parsed_entities"]:
        g = e.get("geometry", {})
        if g.get("type") == "LINE":
            coords = g.get("coordinates", [])
            if len(coords) == 2:
                try:
                    d = math.dist(coords[0], coords[1])
                    if 1e-9 < d < thr:
                        hits.append(e["entity_id"])
                except Exception:
                    pass
    return len(hits), hits, {"threshold": thr}
def _op_duplicate_entity(cio, cond):
    thr = cond.get("threshold", 1e-6)
    seen = defaultdict(list)
    for e in cio["parsed_entities"]:
        g = e.get("geometry", {})
        coords = g.get("coordinates", [])
        if not coords:
            continue
        key = (g.get("type"), tuple(tuple(round(c, 3) for c in p) for p in coords))
        seen[key].append(e["entity_id"])
    dups = [ids for ids in seen.values() if len(ids) > 1]
    hits = [i for ids in dups for i in ids]
    return len(dups), hits, {}
def _op_layer_entity_count_zero(cio, cond):
    exclude = set(cond.get("exclude", []))
    gc = cio.get("global_context", {})
    layer_summary = gc.get("layer_summary", {})
    # 全量图层（doc.layers）- 已用图层（出现在实体中）= 空图层
    all_layers = gc.get("all_layers") or list(layer_summary.keys())
    used = {l for l, c in layer_summary.items() if c > 0}
    hits = [l for l in all_layers if l not in used and l not in exclude]
    return len(hits), hits, {"layers": hits[:20]}
def _op_entity_on_layer(cio, cond):
    layer = cond.get("layer", "0")
    hits = [e["entity_id"] for e in cio["parsed_entities"]
            if e.get("attributes", {}).get("layer_name") == layer]
    return len(hits), hits, {}
def _op_layer_is_off(cio, cond):
    # CIO 中若含图层状态信息则读取，否则从 global_context 推断
    exclude = set(cond.get("exclude", []))
    off = cio.get("global_context", {}).get("layers_off", [])
    hits = [l for l in off if l not in exclude]
    return len(hits), hits, {"layers": hits[:20]}
def _op_layer_is_frozen(cio, cond):
    frozen = cio.get("global_context", {}).get("layers_frozen", [])
    return len(frozen), frozen, {"layers": frozen[:20]}
def _op_layer_name_matches(cio, cond):
    pattern = cond.get("pattern", "")
    layer_summary = cio.get("global_context", {}).get("layer_summary", {})
    try:
        rx = re.compile(pattern)
    except re.error:
        return 0, [], {}
    hits = [l for l in layer_summary if rx.match(l)]
    return len(hits), hits, {"layers": hits[:20]}
def _op_text_height_lt(cio, cond):
    thr = cond.get("threshold", 0.5) * _get_unit_scale(cio)
    hits = []
    for e in cio["parsed_entities"]:
        if e.get("attributes", {}).get("dxf_type") in ("TEXT", "MTEXT"):
            h = e["attributes"].get("height") or 0
            if 0 < h < thr:
                hits.append(e["entity_id"])
    return len(hits), hits, {"threshold": thr}
def _op_empty_text(cio, cond):
    hits = [e["entity_id"] for e in cio["parsed_entities"]
            if e.get("attributes", {}).get("dxf_type") in ("TEXT", "MTEXT")
            and not (e.get("attributes", {}).get("label_text") or "").strip()]
    return len(hits), hits, {}
def _op_block_attr_empty(cio, cond):
    hits = []
    for e in cio["parsed_entities"]:
        attrs = e.get("attributes", {}).get("raw_attribs")
        if attrs and any(not (v or "").strip() for v in attrs.values()):
            hits.append(e["entity_id"])
    return len(hits), hits, {}
def _op_unused_block_definition(cio, cond):
    # CIO 中若记录了块定义清单则比对，否则跳过
    defs = cio.get("global_context", {}).get("block_definitions", [])
    used = {e.get("attributes", {}).get("block_name")
            for e in cio["parsed_entities"]
            if e.get("attributes", {}).get("block_name")}
    hits = [d for d in defs if d not in used]
    return len(hits), hits, {"blocks": hits[:20]}
def _op_entity_bbox_overlap(cio, cond):
    """包围盒重叠检测（硬碰撞）。"""
    kw_a = [k.upper() for k in cond.get("layer_a_keywords", [])]
    kw_b = [k.upper() for k in cond.get("layer_b_keywords", [])]
    tol = cond.get("tolerance", 0.0)
    def match(e, kws):
        blob = f"{e.get('attributes', {}).get('layer_name', '')} {e.get('attributes', {}).get('block_name', '')}".upper()
        return any(k in blob for k in kws)
    group_a = [e for e in cio["parsed_entities"] if match(e, kw_a)]
    group_b = [e for e in cio["parsed_entities"] if match(e, kw_b)]
    hits = []
    for a in group_a[:200]:
        ba = a.get("geometry", {}).get("bbox")
        if not ba:
            continue
        for b in group_b[:200]:
            bb = b.get("geometry", {}).get("bbox")
            if not bb:
                continue
            # AABB 相交测试
            if (ba[0] - tol <= bb[2] and ba[2] + tol >= bb[0]
                    and ba[1] - tol <= bb[3] and ba[3] + tol >= bb[1]):
                hits.append(a["entity_id"])
                hits.append(b["entity_id"])
                break
    return len(hits) // 2, list(set(hits)), {}
def _op_discipline_mismatch(cio, cond):
    expected = cond.get("expected_discipline", "")
    forbidden = [k.upper() for k in cond.get("forbidden_keywords", [])]
    actual = cio.get("project_meta", {}).get("discipline", "")
    if actual != expected:
        return 0, [], {}
    hits = []
    for e in cio["parsed_entities"]:
        blob = f"{e.get('attributes', {}).get('layer_name', '')} {e.get('attributes', {}).get('label_text', '')}".upper()
        if any(k in blob for k in forbidden):
            hits.append(e["entity_id"])
    return len(hits), hits, {}
def _op_layer_pair_distance_lt(cio, cond):
    """两图层构件间距小于阈值。"""
    kw_a = [k.upper() for k in cond.get("layer_a_keywords", [])]
    kw_b = [k.upper() for k in cond.get("layer_b_keywords", [])]
    thr = cond.get("threshold", 500.0)
    def match(e, kws):
        blob = f"{e.get('attributes', {}).get('layer_name', '')}".upper()
        return any(k in blob for k in kws)
    ga = [e for e in cio["parsed_entities"] if match(e, kw_a) and e.get("geometry", {}).get("coordinates")]
    gb = [e for e in cio["parsed_entities"] if match(e, kw_b) and e.get("geometry", {}).get("coordinates")]
    hits = []
    for a in ga[:150]:
        pa = a["geometry"]["coordinates"][0]
        for b in gb[:150]:
            pb = b["geometry"]["coordinates"][0]
            try:
                if math.dist(pa, pb) < thr:
                    hits.extend([a["entity_id"], b["entity_id"]])
                    break
            except Exception:
                pass
    return len(hits) // 2, list(set(hits)), {"threshold": thr}
# ---------------- 补充算子 ----------------
def _op_entity_count_gt(cio, cond):
    """实体总数超过阈值。"""
    thr = cond.get("threshold", 0)
    total = len(cio.get("parsed_entities", []))
    if total > thr:
        return total, [], {"count": total}
    return 0, [], {}
def _op_entity_count_lt(cio, cond):
    """实体总数低于阈值。"""
    thr = cond.get("threshold", 0)
    total = len(cio.get("parsed_entities", []))
    if total < thr:
        return total, [], {"count": total}
    return 0, [], {}
def _op_entity_type_count_gt(cio, cond):
    """指定实体类型数量超过阈值。"""
    dxf_type = cond.get("dxf_type", "")
    thr = cond.get("threshold", 0)
    cnt = sum(1 for e in cio.get("parsed_entities", [])
              if e.get("attributes", {}).get("dxf_type") == dxf_type)
    if cnt > thr:
        return cnt, [], {"count": cnt}
    return 0, [], {}
# 算子注册表
OPERATORS = {
    "zero_length_line": _op_zero_length_line,
    "zero_radius_circle": _op_zero_radius_circle,
    "short_line": _op_short_line,
    "duplicate_entity": _op_duplicate_entity,
    "layer_entity_count_zero": _op_layer_entity_count_zero,
    "entity_on_layer": _op_entity_on_layer,
    "layer_is_off": _op_layer_is_off,
    "layer_is_frozen": _op_layer_is_frozen,
    "layer_name_matches": _op_layer_name_matches,
    "text_height_lt": _op_text_height_lt,
    "empty_text": _op_empty_text,
    "block_attr_empty": _op_block_attr_empty,
    "unused_block_definition": _op_unused_block_definition,
    "entity_bbox_overlap": _op_entity_bbox_overlap,
    "discipline_mismatch": _op_discipline_mismatch,
    "layer_pair_distance_lt": _op_layer_pair_distance_lt,
    "entity_count_gt": _op_entity_count_gt,
    "entity_count_lt": _op_entity_count_lt,
    "entity_type_count_gt": _op_entity_type_count_gt,
}
# ---------------------------------------------------------------- 规则引擎
class RuleEngine:
    def __init__(self, rules_path: str, enable_cross_discipline: bool = False):
        """初始化规则引擎。
        enable_cross_discipline: 是否启用跨专业规则（默认关闭，避免误报）。
            跨专业规则（category=cross_discipline）涉及专业间碰撞与一致性校验，
            需要项目上下文才能准确判断，因此默认关闭，由用户按需启用。
        """
        with open(rules_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.version = cfg.get("version", "unknown")
        self.enable_cross_discipline = enable_cross_discipline
        self.all_rules = cfg.get("rules", [])
        self.rules = []
        for r in self.all_rules:
            is_cross = r.get("category") == "cross_discipline"
            # 跨专业规则：需显式启用；其他规则：看 enabled 字段
            if is_cross:
                if enable_cross_discipline:
                    self.rules.append(r)
            elif r.get("enabled", True):
                self.rules.append(r)
    def run(self, cio: dict) -> dict:
        results = []
        entity_errors = defaultdict(list)
        for rule in self.rules:
            cond = rule.get("condition", {})
            op_name = cond.get("operator")
            op = OPERATORS.get(op_name)
            if not op:
                results.append({
                    "rule_id": rule["id"], "name": rule["name"],
                    "severity": rule.get("severity", "info"),
                    "status": "skipped",
                    "message": f"未知算子: {op_name}",
                })
                continue
            try:
                count, hits, extra = op(cio, cond)
            except Exception as ex:
                results.append({
                    "rule_id": rule["id"], "name": rule["name"],
                    "severity": rule.get("severity", "info"),
                    "status": "error",
                    "message": f"执行异常: {ex}",
                })
                continue
            if count == 0:
                results.append({
                    "rule_id": rule["id"], "name": rule["name"],
                    "severity": rule.get("severity", "info"),
                    "status": "pass", "count": 0,
                    "message": "符合规范",
                })
                continue
            # 渲染 action 模板（模板变量缺失不应整轮崩溃）
            try:
                msg = rule.get("action", "").format(count=count, **extra)
            except (KeyError, IndexError, ValueError) as ex:
                msg = f'{rule.get("action", "")}（模板渲染失败: {ex}，命中 {count} 处）'
                msg = msg.split("（模板渲染失败")[0] + f"，命中 {count} 处"
            results.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "category": rule.get("category", ""),
                "severity": rule.get("severity", "info"),
                "status": "fail",
                "count": count,
                "message": msg,
                "reference": rule.get("reference"),
                "hit_entities": hits[:50],
            })
            # 追加到实体 validation_errors（分析层只追加）
            for eid in hits:
                entity_errors[eid].append({
                    "rule_id": rule["id"],
                    "severity": rule.get("severity", "info"),
                    "message": msg,
                })
        # 汇总
        summary = {
            "total_errors": sum(1 for r in results if r["status"] == "fail" and r["severity"] == "error"),
            "total_warnings": sum(1 for r in results if r["status"] == "fail" and r["severity"] == "warning"),
            "total_infos": sum(1 for r in results if r["status"] == "fail" and r["severity"] == "info"),
            "rules_executed": len(results),
            "rules_passed": sum(1 for r in results if r["status"] == "pass"),
        }
        return {"summary": summary, "results": results, "entity_errors": dict(entity_errors)}
# ---------------------------------------------------------------- 报告输出
def write_rules_md(engine_result: dict, cio: dict, out_path: str):
    L = []
    A = L.append
    s = engine_result["summary"]
    pm = cio.get("project_meta", {})
    A("# CAD 审图规则校验报告\n")
    A(f"> 文件：`{pm.get('source_file', '')}`　|　专业：{pm.get('discipline', '')}　|　"
      f"校验时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    A("## 一、校验汇总\n")
    A("| 指标 | 数量 |")
    A("|---|---:|")
    A(f"| 执行规则数 | {s['rules_executed']} |")
    A(f"| 通过规则数 | {s['rules_passed']} |")
    A(f"| 错误 (error) | {s['total_errors']} |")
    A(f"| 警告 (warning) | {s['total_warnings']} |")
    A(f"| 提示 (info) | {s['total_infos']} |")
    A("")
    fails = [r for r in engine_result["results"] if r["status"] == "fail"]
    if not fails:
        A("## 二、校验结果\n")
        A("✅ 所有规则均通过，未发现规范性问题。\n")
    else:
        A("## 二、问题清单\n")
        # 按严重级别排序
        order = {"error": 0, "warning": 1, "info": 2}
        fails.sort(key=lambda r: order.get(r["severity"], 3))
        for i, r in enumerate(fails, 1):
            icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(r["severity"], "⚪")
            A(f"### {i}. {icon} [{r['rule_id']}] {r['name']}\n")
            A(f"- **级别**：{r['severity']}")
            A(f"- **命中数量**：{r['count']}")
            A(f"- **说明**：{r['message']}")
            if r.get("reference"):
                A(f"- **规范依据**：{r['reference']}")
            if r.get("hit_entities"):
                A(f"- **涉及实体**：{len(r['hit_entities'])} 个（示例 ID：{', '.join(r['hit_entities'][:3])}）")
            A("")
    A("## 三、规则执行明细\n")
    A("| 规则ID | 规则名称 | 级别 | 状态 | 命中数 |")
    A("|---|---|---|---|---:|")
    for r in engine_result["results"]:
        status = {"pass": "✅ 通过", "fail": "❌ 未通过", "skipped": "⏭️ 跳过", "error": "⚠️ 异常"}.get(r["status"], r["status"])
        A(f"| {r['rule_id']} | {r['name']} | {r['severity']} | {status} | {r.get('count', '-')} |")
    A("")
    A("---\n")
    A("*本报告由 CAD 审图规则引擎自动生成。规则库可编辑，工程师可在 rules/*.yaml 中增删规则。*")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="CIO 规则引擎校验")
    ap.add_argument("cio", help="CIO JSON 文件路径")
    ap.add_argument("--rules", default=None, help="规则库 YAML 路径")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果")
    ap.add_argument("--enable-cross-discipline", action="store_true",
                    help="启用跨专业规则（碰撞检测、专业一致性校验，默认关闭）")
    args = ap.parse_args()
    # 默认规则库
    rules_path = args.rules
    if not rules_path:
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "rules", "default_rules.yaml")
    if not os.path.isfile(rules_path):
        print(f"[ERROR] 规则库不存在: {rules_path}", file=sys.stderr)
        sys.exit(2)
    with open(args.cio, encoding="utf-8") as f:
        cio = json.load(f)
    engine = RuleEngine(rules_path, enable_cross_discipline=args.enable_cross_discipline)
    result = engine.run(cio)
    base = os.path.splitext(os.path.basename(args.cio))[0]
    out_dir = args.out or os.path.dirname(os.path.abspath(args.cio))
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"{base}_审图报告.md")
    write_rules_md(result, cio, md_path)
    print(f"[OK] 审图报告: {md_path}")
    if args.json:
        json_path = os.path.join(out_dir, f"{base}_审图结果.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 审图结果: {json_path}")
    s = result["summary"]
    print("\n===== 审图摘要 =====")
    print(f"规则库版本: {engine.version}")
    print(f"跨专业规则: {'已启用' if args.enable_cross_discipline else '未启用（加 --enable-cross-discipline 开启）'}")
    print(f"执行规则  : {s['rules_executed']}")
    print(f"通过      : {s['rules_passed']}")
    print(f"错误      : {s['total_errors']}")
    print(f"警告      : {s['total_warnings']}")
    print(f"提示      : {s['total_infos']}")
if __name__ == "__main__":
    main()