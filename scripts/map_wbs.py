#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分部分项映射脚本 —— 工作流步骤⑥分部分项映射（GB 50300-2013）
用法:
    python map_wbs.py quantities.json --standard gb50300 --out wbs.json
功能:
    将工程量明细按 GB 50300-2013 附录 B 划分标准映射为：
    分部 → 子分部 → 分项 → 工序
    映射规则落在 references/gb50300-division.md 和 references/disciplines/ 中，
    脚本只查表，不硬编码业务逻辑。
依赖: 无（纯 Python，规则内置基础映射，可扩展）
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_common import Out, add_output_args, classify_layer, load_gb50300_map  # noqa: E402

# 数据唯一来源：rules/gb50300_map.yaml（文档副本见 references/gb50300-division.md）
_GB = load_gb50300_map()
GB50300_WBS = _GB.get("divisions", {})
DISCIPLINE_TO_DIVISION = _GB.get("discipline_to_division", {})

def _match_division_by_discipline(discipline: str) -> list:
    """根据专业返回可能的分部列表。"""
    return DISCIPLINE_TO_DIVISION.get(discipline, [])

def _match_subitem(division: str, text: str) -> tuple:
    """在分部内匹配子分部和分项。返回 (子分部, 分项)。"""
    div_info = GB50300_WBS.get(division, {})
    sub_divs = div_info.get("子分部", [])
    keywords = div_info.get("分项_keywords", {})
    for sub, kws in keywords.items():
        for kw in kws:
            if kw in text:
                return (sub_divs[0] if sub_divs else division, sub)
    return (sub_divs[0] if sub_divs else division, "未明确分项")

def map_quantity_to_wbs(item: dict, discipline: str = "") -> dict:
    """将单条工程量映射到分部分项。"""
    name = item.get("block_name", item.get("layer", item.get("名称", "")))
    text = str(name)
    divisions = _match_division_by_discipline(discipline)
    if not divisions:
        divisions = list(GB50300_WBS.keys())
    # 遍历可能分部，匹配关键词
    matched = None
    for div in divisions:
        sub, subitem = _match_subitem(div, text)
        if subitem != "未明确分项":
            matched = {"分部": div, "子分部": sub, "分项": subitem}
            break
    if not matched:
        # 退而求其次：取第一个分部
        if divisions:
            matched = {"分部": divisions[0], "子分部": GB50300_WBS.get(divisions[0], {}).get("子分部", [""])[0], "分项": "未明确分项"}
        else:
            matched = {"分部": "未分类", "子分部": "", "分项": ""}
    item["wbs"] = matched
    return item

def build_wbs_tree(quantities: dict, catalog: dict = None) -> dict:
    """构建分部分项划分树。"""
    # 获取专业信息
    discipline_map = {}
    if catalog and "items" in catalog:
        for item in catalog["items"]:
            discipline_map[item.get("图号", "")] = item.get("专业", "其他")

    tree = {}
    # 处理 block_table
    for item in quantities.get("block_table", []):
        # 简单匹配：按图层名推断专业（词典见 rules/discipline_dict.yaml）
        layer = list(item.get("layers", {}).keys())[0] if item.get("layers") else ""
        disc = classify_layer(layer)
        mapped = map_quantity_to_wbs(item, disc)
        wbs = mapped["wbs"]
        div = wbs["分部"]
        sub = wbs["子分部"]
        subitem = wbs["分项"]
        tree.setdefault(div, {}).setdefault(sub, {}).setdefault(subitem, []).append({
            "名称": item.get("block_name", ""),
            "数量": item.get("quantity", 0),
            "单位": item.get("unit", "个"),
            "规格": item.get("specs", {}),
            "图层": item.get("layers", {}),
        })

    # 处理 length_table（管线类）
    for item in quantities.get("length_table", []):
        layer = item.get("layer", "")
        disc = "给排水" if any(k in layer.upper() for k in ["PIPE", "WATER", "DRAIN"]) else \
               "暖通" if any(k in layer.upper() for k in ["DUCT", "HVAC"]) else \
               "电气" if any(k in layer.upper() for k in ["CABLE", "WIRE", "ELEC"]) else "其他"
        mapped = map_quantity_to_wbs(item, disc)
        wbs = mapped["wbs"]
        div = wbs["分部"]
        sub = wbs["子分部"]
        subitem = wbs["分项"]
        tree.setdefault(div, {}).setdefault(sub, {}).setdefault(subitem, []).append({
            "名称": f"管线({layer})",
            "数量": item.get("total_length_m", 0),
            "单位": "m",
            "图层": layer,
        })

    # 处理 area_table（面积类）
    for item in quantities.get("area_table", []):
        layer = item.get("layer", "")
        mapped = map_quantity_to_wbs(item, "建筑")
        wbs = mapped["wbs"]
        div = wbs["分部"]
        sub = wbs["子分部"]
        subitem = wbs["分项"]
        tree.setdefault(div, {}).setdefault(sub, {}).setdefault(subitem, []).append({
            "名称": f"面积({layer})",
            "数量": item.get("total_area_m2", 0),
            "单位": "m²",
            "图层": layer,
        })

    return {
        "schema": "tzp.wbs/1.0",
        "standard": "GB 50300-2013",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "divisions": tree,
        "division_count": len(tree),
    }

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="分部分项映射（工作流步骤⑥，GB 50300-2013）")
    ap.add_argument("quantities", help="quantities.json 路径")
    ap.add_argument("--catalog", help="可选：catalog.json 路径（用于获取专业信息）")
    ap.add_argument("--standard", default="gb50300", help="划分标准（默认 gb50300）")
    ap.add_argument("--out", required=True, help="输出 wbs.json 路径")
    add_output_args(ap)
    args = ap.parse_args()
    out = Out.from_args(args)

    with open(args.quantities, "r", encoding="utf-8") as f:
        quantities = json.load(f)
    catalog = None
    if args.catalog and os.path.isfile(args.catalog):
        with open(args.catalog, "r", encoding="utf-8") as f:
            catalog = json.load(f)

    wbs = build_wbs_tree(quantities, catalog)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(wbs, f, ensure_ascii=False, indent=2)
    out.ok("WBS", args.out, f"{wbs['division_count']} 个分部", wbs["standard"])
    out.detail(*(f"     - {div}（{len(wbs['divisions'][div])} 个子分部）" for div in wbs["divisions"]))

if __name__ == "__main__":
    main()
