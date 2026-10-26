#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专业分类脚本 —— 工作流步骤④专业分类
用法:
    python classify_discipline.py catalog.json --out catalog_classified.json
功能:
    基于图号前缀规则 + 图名关键词双重判定，为每张图纸打专业标签并排序。
    专业：建筑、结构、给排水、暖通、电气、总图、装饰、智能建筑、建筑节能、电梯、消防
依赖: 无（纯 Python）
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_common import Out, add_output_args, load_discipline_dict  # noqa: E402

# 三张表统一来自 rules/discipline_dict.yaml（脚本只查表）
_D = load_discipline_dict()
DRAWING_NO_PREFIX_MAP = _D.drawing_no_prefix
DRAWING_NAME_KEYWORDS = _D.name_keywords
DISCIPLINE_ORDER = _D.order

def classify_by_drawing_no(drawing_no: str) -> str | None:
    """根据图号前缀判定专业。"""
    if not drawing_no or drawing_no == "未识别":
        return None
    # 逐个尝试前缀匹配（最长前缀优先）
    for prefix in sorted(DRAWING_NO_PREFIX_MAP.keys(), key=len, reverse=True):
        if drawing_no.upper().startswith(prefix.upper()):
            return DRAWING_NO_PREFIX_MAP[prefix]
    return None

def classify_by_name(drawing_name: str) -> str | None:
    """根据图名关键词判定专业。"""
    if not drawing_name:
        return None
    scores = {}
    for disc, keywords in DRAWING_NAME_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in drawing_name)
        if score > 0:
            scores[disc] = score
    if scores:
        return max(scores, key=scores.get)
    return None

def classify_item(item: dict) -> dict:
    """对单条目录项进行专业分类。"""
    drawing_no = item.get("图号", "")
    drawing_name = item.get("图名", "")
    by_no = classify_by_drawing_no(drawing_no)
    by_name = classify_by_name(drawing_name)
    # 双重判定：以图号为准，图名为辅
    if by_no and by_name:
        if by_no == by_name:
            discipline = by_no
            confidence = "高"
        else:
            discipline = by_no
            confidence = "中（图号与图名不一致）"
    elif by_no:
        discipline = by_no
        confidence = "中（仅图号）"
    elif by_name:
        discipline = by_name
        confidence = "中（仅图名）"
    else:
        discipline = "其他"
        confidence = "低（未匹配到专业特征）"
    item["专业"] = discipline
    item["置信度"] = confidence
    return item

def classify_catalog(catalog: dict) -> dict:
    """对整个目录进行专业分类并排序。"""
    items = [classify_item(item) for item in catalog.get("items", [])]
    # 按专业排序
    def sort_key(item):
        disc = item.get("专业", "其他")
        try:
            order = DISCIPLINE_ORDER.index(disc)
        except ValueError:
            order = len(DISCIPLINE_ORDER)
        return (order, item.get("图号", ""))
    items.sort(key=sort_key)
    # 按专业统计
    disc_stats = {}
    for item in items:
        d = item.get("专业", "其他")
        disc_stats[d] = disc_stats.get(d, 0) + 1
    catalog["items"] = items
    catalog["discipline_stats"] = disc_stats
    catalog["disciplines"] = list(disc_stats.keys())
    return catalog

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="专业分类（工作流步骤④）")
    ap.add_argument("catalog", help="catalog.json 路径")
    ap.add_argument("--out", required=True, help="输出分类后的 catalog JSON 路径")
    add_output_args(ap)
    args = ap.parse_args()
    out = Out.from_args(args)

    with open(args.catalog, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    result = classify_catalog(catalog)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    stats = "，".join(f"{k} {v}" for k, v in result["discipline_stats"].items())
    out.ok("专业分类", args.out, f"{len(result['items'])} 张", stats)

if __name__ == "__main__":
    main()
