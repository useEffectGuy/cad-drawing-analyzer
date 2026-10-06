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

# 图号前缀 → 专业 映射
DRAWING_NO_PREFIX_MAP = {
    "建施": "建筑", "JZ": "建筑", "JS": "建筑", "A": "建筑",
    "结施": "结构", "JG": "结构", "GS": "结构", "S": "结构",
    "水施": "给排水", "SS": "给排水", "PS": "给排水", "P": "给排水", "水": "给排水",
    "暖施": "暖通", "NS": "暖通", "M": "暖通", "通施": "暖通", "TS": "暖通",
    "电施": "电气", "DS": "电气", "E": "电气", "强电": "电气", "弱电": "电气",
    "设施": "给排水", "FS": "给排水",
    "装施": "装饰", "ZS": "装饰", "DE": "装饰",
    "总图": "总图", "总": "总图", "L": "总图", "Z": "总图",
    "消施": "消防", "XF": "消防", "消防": "消防",
    "智施": "智能建筑", "智能": "智能建筑",
}

# 图名关键词 → 专业
DRAWING_NAME_KEYWORDS = {
    "建筑": ["建施", "建筑", "平面", "立面", "剖面", "门窗", "楼梯", "卫生间", "厨房", "阳台"],
    "结构": ["结施", "结构", "柱", "梁", "板", "基础", "钢筋", "承台", "剪力墙", "配筋", "平法"],
    "给排水": ["水施", "给排水", "给水", "排水", "雨水", "污水", "消火栓", "喷淋", "管道", "阀门", "地漏", "水泵"],
    "暖通": ["暖施", "暖通", "风管", "风口", "空调", "风机", "冷媒", "冷凝水", "排烟", "新风", "采暖"],
    "电气": ["电施", "电气", "照明", "插座", "开关", "配电箱", "桥架", "电缆", "防雷", "接地", "强电", "弱电", "回路"],
    "消防": ["消防", "火灾报警", "喷淋", "消火栓", "烟感", "温感"],
    "总图": ["总平面", "红线", "道路", "绿化", "坐标", "竖向", "场地"],
    "装饰": ["装饰", "装修", "地面", "墙面", "天花", "饰面"],
    "智能建筑": ["智能", "弱电", "监控", "门禁", "网络", "综合布线"],
}

# 专业排序（GB 50300 十大分部顺序优先）
DISCIPLINE_ORDER = [
    "总图", "建筑", "结构", "给排水", "消防", "暖通", "电气",
    "智能建筑", "装饰", "建筑节能", "电梯", "其他",
]

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
    args = ap.parse_args()

    with open(args.catalog, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    result = classify_catalog(catalog)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] 专业分类完成: {args.out}")
    for disc, count in result["discipline_stats"].items():
        print(f"     {disc}: {count} 张")

if __name__ == "__main__":
    main()
