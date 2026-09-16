#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工序识别模块 —— 构件类型 → 工序映射 + 工序序列推理
用法:
    python cad_process.py <cio.json> [--rules rules/process_rules.yaml] [--out report_dir]
功能:
    1. 加载工序规则库（构件类型 × 属性特征 → 工序序列）
    2. 为每个构件匹配工序，输出工序清单
    3. 按空间/楼层聚合，推理工序先后顺序
    4. 生成工序汇总表（可用于工期估算）
设计说明:
    工序识别是从图纸元素推断施工/加工顺序。核心是「构件类型 × 属性特征 → 工序类型」
    的映射规则库，规则与代码分离，工程师可按项目工艺特点增删。
依赖: pyyaml
"""
from __future__ import annotations
import argparse
import json
import re
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
try:
    import yaml
except ImportError:
    print("[ERROR] 缺少依赖 pyyaml，请执行: pip install pyyaml", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------- 工序识别引擎
class ProcessEngine:
    def __init__(self, rules_path: str):
        with open(rules_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.version = cfg.get("version", "unknown")
        self.rules = cfg.get("rules", [])
    def match_rule(self, entity: dict, discipline: str) -> dict | None:
        """为单个构件匹配工序规则。"""
        cat = entity.get("category", "")
        attrs = entity.get("attributes", {})
        # 构造匹配文本：图块名 + 文字标签 + 属性值
        blob = " ".join([
            str(attrs.get("block_name", "")),
            str(attrs.get("label_text", "")),
            str(attrs.get("nearby_text", "")),
            " ".join(str(v) for v in (attrs.get("raw_attribs") or {}).values()),
        ]).upper()
        best = None
        for rule in self.rules:
            m = rule.get("match", {})
            # 专业过滤
            rd = rule.get("discipline", "ALL")
            if rd != "ALL" and discipline and rd != discipline:
                continue
            # 类别匹配
            if m.get("category") and m["category"] != cat:
                continue
            # 关键词匹配
            kws = m.get("keywords", [])
            if kws:
                if not any(str(k).upper() in blob for k in kws):
                    continue
            # 命中，记录（优先选择关键词更多的规则，更具体）
            score = len(kws)
            if best is None or score > best[0]:
                best = (score, rule)
        return best[1] if best else None
    def run(self, cio: dict) -> dict:
        discipline = cio.get("project_meta", {}).get("discipline", "")
        # 若图纸是多专业，放宽专业过滤
        if discipline in ("MULTI", "UNKNOWN"):
            discipline = ""
        # 预收集所有文字实体，用于给构件补充"邻近文字"作为工序匹配依据
        # 原因：材料/工艺说明常以独立文字实体存在（如"剪力墙 C30混凝土"），
        #       而构件本身（如柱图块）属性里没有材料信息，需要空间邻近关联
        text_entities = []
        for e in cio.get("parsed_entities", []):
            attrs = e.get("attributes", {})
            if attrs.get("dxf_type") in ("TEXT", "MTEXT") and attrs.get("label_text"):
                coords = e.get("geometry", {}).get("coordinates", [])
                if coords:
                    text_entities.append({
                        "text": attrs["label_text"],
                        "pt": coords[0],
                    })
        def _nearby_texts(entity, radius=8000.0):
            """返回构件附近的文字内容。"""
            coords = entity.get("geometry", {}).get("coordinates", [])
            if not coords:
                return ""
            pt = coords[0]
            found = []
            for te in text_entities:
                try:
                    d = math.dist(pt, te["pt"])
                except Exception:
                    continue
                if d <= radius:
                    found.append(te["text"])
            return " ".join(found)
        results = []
        process_counter = Counter()
        by_category = defaultdict(lambda: {"count": 0, "processes": Counter()})
        for e in cio.get("parsed_entities", []):
            # 把邻近文字注入属性，供规则匹配
            nearby = _nearby_texts(e)
            if nearby:
                e = dict(e)
                attrs = dict(e.get("attributes", {}))
                attrs["nearby_text"] = nearby
                e["attributes"] = attrs
            rule = self.match_rule(e, discipline)
            if not rule:
                continue
            attrs = e.get("attributes", {})
            item = {
                "entity_id": e["entity_id"],
                "category": e.get("category", ""),
                "sub_category": e.get("sub_category", ""),
                "block_name": attrs.get("block_name", ""),
                "layer": attrs.get("layer_name", ""),
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "processes": rule.get("processes", []),
                "duration_hint": rule.get("duration_hint"),
            }
            results.append(item)
            cat = e.get("category", "UNKNOWN")
            by_category[cat]["count"] += 1
            for p in rule.get("processes", []):
                process_counter[p] += 1
                by_category[cat]["processes"][p] += 1
        # 工序序列推理：按"先结构后建筑、先主体后安装"的常规施工顺序排序
        SEQUENCE_ORDER = {
            # 结构阶段
            "放线": 1, "钢筋绑扎": 2, "模板支设": 3, "梁底模板支设": 3, "侧模支设": 3,
            "水电预埋": 4, "混凝土浇筑": 5, "构造柱浇筑": 5, "圈梁浇筑": 5,
            "模板拆除": 6, "混凝土养护": 7, "养护": 7,
            # 砌筑阶段
            "砌筑": 10, "墙面抹灰": 11,
            # 安装阶段
            "管道预制": 20, "风管制作": 20, "法兰制作": 20, "构件加工": 20,
            "支吊架安装": 21, "支架安装": 21, "基础型钢制作": 21, "基础制作": 21,
            "管路预制": 21, "除锈涂装": 21,
            "管道敷设": 22, "风管吊装": 22, "管路敷设": 22, "桥架组装": 22,
            "运输吊装": 22, "设备就位": 22,
            "螺纹连接/沟槽连接": 23, "承插粘接": 23, "法兰连接": 23,
            "高强螺栓连接": 23, "焊接": 23, "桥架固定": 23, "管路固定": 23,
            "阀门安装": 24, "灯具安装": 24, "面板安装": 24, "箱体安装": 24,
            "进出线连接": 24, "接线": 24, "面板接线": 24, "管道连接": 24,
            "减振安装": 24, "接地跨接": 24, "盖板安装": 25,
            "水压试验": 30, "灌水试验": 30, "漏风量测试": 30, "严密性试验": 30,
            "绝缘测试": 30, "通电试亮": 31, "通电测试": 31, "送电调试": 31,
            "单机试运转": 32, "系统调试": 33, "防腐保温": 34, "防腐涂装": 34,
            "保温施工": 34, "试压验收": 35, "密封打胶": 35, "打胶密封": 35,
            "水枪水带配置": 35, "洞口预留": 1, "洞口修整": 2,
            # 安装前检验类（应在安装工序之前）
            "阀门检验": 19, "灯具检验": 19, "设备开箱检验": 19,
            # 管路/接线准备类
            "穿引线": 22, "接线盒预埋": 4, "电气接线": 24,
            "门窗框安装": 3, "窗框安装": 3, "门窗扇安装": 4, "玻璃安装": 4,
        }
        # 汇总所有工序，按施工顺序排序
        all_processes = []
        for p, cnt in process_counter.most_common():
            all_processes.append({
                "process": p,
                "occurrence": cnt,
                "sequence_order": SEQUENCE_ORDER.get(p, 99),
            })
        all_processes.sort(key=lambda x: (x["sequence_order"], -x["occurrence"]))
        # 工序阶段划分
        def _stage(order):
            if order <= 9:
                return "结构施工阶段"
            if order <= 15:
                return "砌筑抹灰阶段"
            if order <= 29:
                return "安装施工阶段"
            return "调试验收阶段"
        for p in all_processes:
            p["stage"] = _stage(p["sequence_order"])
        stage_summary = Counter(p["stage"] for p in all_processes)
        return {
            "rules_version": self.version,
            "matched_entities": len(results),
            "total_entities": len(cio.get("parsed_entities", [])),
            "process_count": len(process_counter),
            "processes": all_processes,
            "stage_summary": dict(stage_summary),
            "by_category": {
                k: {"count": v["count"],
                    "processes": [{"process": p, "count": c} for p, c in v["processes"].most_common()]}
                for k, v in by_category.items()
            },
            "entity_processes": results[:500],
            "truncated": len(results) > 500,
        }
# ---------------------------------------------------------------- 报告输出
def write_process_md(result: dict, cio: dict, out_path: str):
    L = []
    A = L.append
    pm = cio.get("project_meta", {})
    A("# CAD 图纸工序识别报告\n")
    A(f"> 文件：`{pm.get('source_file', '')}`　|　专业：{pm.get('discipline', '')}　|　"
      f"分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    A("## 一、识别汇总\n")
    A("| 指标 | 值 |")
    A("|---|---:|")
    A(f"| 规则库版本 | {result['rules_version']} |")
    A(f"| 构件总数 | {result['total_entities']} |")
    A(f"| 匹配到工序的构件 | {result['matched_entities']} |")
    A(f"| 涉及工序种类 | {result['process_count']} |")
    A("")
    if result["stage_summary"]:
        A("### 工序阶段分布\n")
        A("| 阶段 | 工序种类数 |")
        A("|---|---:|")
        for stage, cnt in result["stage_summary"].items():
            A(f"| {stage} | {cnt} |")
        A("")
    A("## 二、工序清单（按施工顺序）\n")
    if not result["processes"]:
        A("未匹配到工序规则。可能原因：图纸构件类型不在规则库覆盖范围内，或属性信息不足。\n")
    else:
        A("| 序号 | 工序 | 涉及构件数 | 施工阶段 |")
        A("|---|---|---:|---|")
        for i, p in enumerate(result["processes"], 1):
            A(f"| {i} | {p['process']} | {p['occurrence']} | {p['stage']} |")
        A("")
    A("## 三、按构件类别统计\n")
    if result["by_category"]:
        for cat, info in sorted(result["by_category"].items(), key=lambda x: -x[1]["count"]):
            A(f"### {cat}（{info['count']} 个构件）\n")
            A("| 工序 | 数量 |")
            A("|---|---:|")
            for p in info["processes"]:
                A(f"| {p['process']} | {p['count']} |")
            A("")
    A("## 四、构件工序明细（前 50 条）\n")
    if result["entity_processes"]:
        A("| 构件 | 类别 | 匹配规则 | 工序序列 |")
        A("|---|---|---|---|")
        for it in result["entity_processes"][:50]:
            name = it["block_name"] or it["category"]
            procs = " → ".join(it["processes"])
            A(f"| {name} | {it['category']} | {it['rule_name']} | {procs} |")
        A("")
        if result.get("truncated"):
            A(f"> 共 {result['matched_entities']} 条，此处仅展示前 50 条。\n")
    A("## 五、工序推理说明\n")
    A("工序序列基于「构件类型 × 属性特征 → 工序类型」规则库推导，并按常规施工顺序"
      "（结构 → 砌筑 → 安装 → 调试）排列。实际施工顺序需结合项目施工组织设计调整。\n")
    A("---\n")
    A("*本报告由 CAD 图纸工序识别模块自动生成。工序规则可在 rules/process_rules.yaml 中配置。*")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="CAD 图纸工序识别")
    ap.add_argument("cio", help="CIO JSON 文件路径")
    ap.add_argument("--rules", default=None, help="工序规则库 YAML 路径")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    rules_path = args.rules
    if not rules_path:
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "rules", "process_rules.yaml")
    if not os.path.isfile(rules_path):
        print(f"[ERROR] 规则库不存在: {rules_path}", file=sys.stderr)
        sys.exit(2)
    with open(args.cio, encoding="utf-8") as f:
        cio = json.load(f)
    engine = ProcessEngine(rules_path)
    result = engine.run(cio)
    # 一致性校验：YAML 工序是否全部在顺序表中登记
    src_self = open(os.path.abspath(__file__), encoding="utf-8").read()
    m = re.search(r"SEQUENCE_ORDER\s*=\s*\{(.*?)\}", src_self, re.S)
    reg = set(re.findall(r'"([^"]+)"\s*:', m.group(1))) if m else set()
    yaml_procs = set()
    for rule in engine.rules:
        yaml_procs.update(rule.get("processes", []))
    missing = yaml_procs - reg
    if missing:
        print(f"[WARN] 以下 {len(missing)} 个工序未在 SEQUENCE_ORDER 登记（按默认顺序处理）:", file=sys.stderr)
        print("       " + "、".join(sorted(missing)), file=sys.stderr)
    base = os.path.splitext(os.path.basename(args.cio))[0]
    out_dir = args.out or os.path.dirname(os.path.abspath(args.cio))
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"{base}_工序识别报告.md")
    write_process_md(result, cio, md_path)
    print(f"[OK] 工序识别报告: {md_path}")
    if args.json:
        json_path = os.path.join(out_dir, f"{base}_工序识别数据.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] JSON 数据: {json_path}")
    print("\n===== 工序识别摘要 =====")
    print(f"构件总数  : {result['total_entities']}")
    print(f"匹配构件  : {result['matched_entities']}")
    print(f"工序种类  : {result['process_count']}")
    if result["processes"]:
        print("主要工序  :")
        for p in result["processes"][:8]:
            print(f"   - {p['process']}（{p['occurrence']} 个构件）")
if __name__ == "__main__":
    main()