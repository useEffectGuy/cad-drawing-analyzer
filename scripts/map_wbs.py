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

# GB 50300-2013 附录 B 十大分部 → 子分部 → 分项 基础映射表
# （完整版见 references/gb50300-division.md，此处内置核心映射供脚本查表）
GB50300_WBS = {
    "地基与基础": {
        "子分部": ["地基", "基础", "基坑支护", "地下水控制", "土方", "边坡"],
        "分项_keywords": {"混凝土基础": ["垫层", "承台", "基础梁", "独立基础", "筏板", "桩"],
                           "砌体基础": ["砖基础", "毛石基础"],
                           "桩基": ["灌注桩", "预制桩", "CFG桩"]},
    },
    "主体结构": {
        "子分部": ["混凝土结构", "砌体结构", "钢结构", "木结构", "钢管混凝土结构", "型钢混凝土结构"],
        "分项_keywords": {"混凝土结构": ["柱", "梁", "板", "墙", "楼梯", "后浇带", "施工缝"],
                           "砌体结构": ["砖砌体", "砌块", "填充墙"],
                           "模板": ["模板", "支架"],
                           "钢筋": ["钢筋", "配筋"]},
    },
    "建筑装饰装修": {
        "子分部": ["地面", "抹灰", "门窗", "吊顶", "轻质隔墙", "饰面板", "涂饰", "裱糊与软包", "细部"],
        "分项_keywords": {"门窗": ["门", "窗"],
                           "地面": ["地面", "楼面", "地砖", "地板"],
                           "抹灰": ["抹灰", "腻子"],
                           "涂饰": ["涂料", "油漆"]},
    },
    "屋面": {
        "子分部": ["基层与保护", "保温与隔热", "防水与密封", "瓦面与板面", "细部构造"],
        "分项_keywords": {"防水": ["防水", "卷材", "涂膜"],
                           "保温": ["保温", "隔热", "挤塑板"],
                           "瓦面": ["瓦", "屋面瓦"]},
    },
    "建筑给水排水及供暖": {
        "子分部": ["室内给水系统", "室内排水系统", "室内热水系统", "卫生器具", "室内供暖系统",
                    "室外给水管网", "室外排水管网", "室外供热管网", "建筑饮用水供应系统",
                    "建筑中水系统及雨水利用系统", "游泳池及公共浴池水系统", "水景喷泉系统",
                    "热源及辅助设备", "监测与控制仪表"],
        "分项_keywords": {"给水管道": ["给水", "冷水", "热水", "给水管"],
                           "排水管道": ["排水", "污水", "雨水", "排水管", "地漏"],
                           "卫生器具": ["洁具", "马桶", "洗手盆", "淋浴"],
                           "消火栓": ["消火栓", "消防箱"],
                           "喷淋": ["喷淋", "喷头", "消防管"]},
    },
    "通风与空调": {
        "子分部": ["送风系统", "排风系统", "防排烟系统", "除尘系统", "舒适性空调系统",
                    "恒温恒湿空调系统", "净化空调系统", "地下人防通风系统", "真空吸尘系统",
                    "冷凝水系统", "空调水系统", "设备自控系统"],
        "分项_keywords": {"风管": ["风管", "风道"],
                           "风口": ["风口", "散流器", "百叶"],
                           "空调设备": ["空调", "风机", "AHU", "FCU", "新风机"],
                           "水管": ["冷媒管", "冷凝水管", "空调水管"]},
    },
    "建筑电气": {
        "子分部": ["室外电气", "变配电室", "供电干线", "电气动力", "电气照明",
                    "备用和不间断电源", "防雷及接地安装"],
        "分项_keywords": {"照明": ["照明", "灯具", "灯"],
                           "动力": ["配电箱", "配电柜", "电缆", "桥架"],
                           "插座": ["插座", "开关"],
                           "防雷接地": ["防雷", "接地", "避雷", "引下线"]},
    },
    "智能建筑": {
        "子分部": ["智能化集成系统", "信息接入系统", "用户电话交换系统", "信息网络系统",
                    "综合布线系统", "移动通信室内信号覆盖系统", "卫星通信系统",
                    "有线电视及卫星电视接收系统", "公共广播系统", "会议系统",
                    "信息导引及发布系统", "时钟系统", "信息化应用系统", "建筑设备监控系统",
                    "火灾自动报警系统", "安全技术防范系统", "应急响应系统", "机房", "防雷与接地"],
        "分项_keywords": {"综合布线": ["网线", "双绞线", "光纤", "信息点"],
                           "安防": ["监控", "摄像机", "门禁", "报警"],
                           "消防报警": ["烟感", "温感", "火灾报警", "手报"]},
    },
    "建筑节能": {
        "子分部": ["围护系统节能", "供暖空调设备及管网节能", "电气动力节能", "监控系统节能", "可再生能源"],
        "分项_keywords": {"围护节能": ["保温", "隔热", "节能窗", "遮阳"],
                           "空调节能": ["变频", "能效"],
                           "电气节能": ["LED", "节能灯具"]},
    },
    "电梯": {
        "子分部": ["电力驱动的曳引式或强制式电梯", "液压电梯", "自动扶梯", "自动人行道"],
        "分项_keywords": {"电梯": ["电梯", "扶梯", "曳引机"]},
    },
}

# 专业 → 分部 映射
DISCIPLINE_TO_DIVISION = {
    "建筑": ["建筑装饰装修", "屋面", "建筑节能"],
    "结构": ["地基与基础", "主体结构"],
    "给排水": ["建筑给水排水及供暖"],
    "消防": ["建筑给水排水及供暖", "智能建筑"],
    "暖通": ["通风与空调", "建筑给水排水及供暖"],
    "电气": ["建筑电气", "智能建筑"],
    "智能建筑": ["智能建筑"],
    "装饰": ["建筑装饰装修"],
    "总图": [],
    "电梯": ["电梯"],
}

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
        disc = ""
        # 简单匹配：按图层名推断专业
        layer = list(item.get("layers", {}).keys())[0] if item.get("layers") else ""
        for d, kws in {
            "建筑": ["墙", "门", "窗", "WALL", "DOOR"],
            "结构": ["柱", "梁", "板", "COLUMN", "BEAM"],
            "给排水": ["水", "PIPE", "DRAIN", "WATER"],
            "电气": ["灯", "插座", "ELEC", "LIGHT"],
            "暖通": ["风", "DUCT", "HVAC"],
        }.items():
            if any(k.upper() in layer.upper() for k in kws):
                disc = d
                break
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
    args = ap.parse_args()

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
    print(f"[OK] 分部分项映射完成: {args.out}")
    print(f"     划分标准: {wbs['standard']}")
    print(f"     涉及分部数: {wbs['division_count']}")
    for div in wbs["divisions"]:
        sub_count = len(wbs["divisions"][div])
        print(f"     - {div}（{sub_count} 个子分部）")

if __name__ == "__main__":
    main()
