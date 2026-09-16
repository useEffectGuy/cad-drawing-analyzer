#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAD 图纸智能分析脚本 —— 专业识别 / 图例解析 / 跨图层关联 / 工程量汇总
用法:
    python cad_analyze.py <drawing.dxf> [--out report_dir] [--excel]
功能:
    1. 专业自动识别：图层名 + 图块名 + 文字 三重加权投票，判定图纸专业
    2. 图例自适应解析：定位图例区，提取"符号→含义"映射表
    3. 跨图层关联分析：空间邻近算法，还原构件间的空间关系
    4. 工程量汇总：按图块/图层分类汇总，可导出 Excel
    5. 审图报告增强：基于以上结果给出专业判断
依赖: ezdxf（Excel 导出需 openpyxl）
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
    import ezdxf
except ImportError:
    print("[ERROR] 缺少依赖 ezdxf，请先执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)

def _ensure_dxf_input(path: str) -> str:
    """若输入是 DWG，自动转换为 DXF（需要系统安装 ODA/LibreDWG）。"""
    if not path.lower().endswith(".dwg"):
        return path
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from cad_convert import ensure_dxf
        print("[INFO] 检测到 DWG 文件，正在自动转换为 DXF...")
        dxf = ensure_dxf(path)
        print(f"[INFO] 转换完成: {dxf}")
        return dxf
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

# ---------------------------------------------------------------- 专业词典
# 每个专业：关键词 -> 权重（图层名权重最高，因为图层是制图规范的第一组织维度）
DISCIPLINE_DICT = {
    "建筑": {
        "layer": {"墙": 3, "门": 3, "窗": 3, "轴线": 3, "楼梯": 3, "房间": 2, "标高": 2,
                  "WALL": 3, "DOOR": 3, "WINDOW": 3, "AXIS": 3, "STAIR": 3, "FLOOR": 2,
                  "ARCH": 2, "建": 2},
        "block": {"门": 3, "窗": 3, "楼梯": 3, "洁具": 2, "家具": 2, "DOOR": 3, "WINDOW": 3},
        "text": {"平面图": 3, "立面图": 3, "剖面图": 3, "房间": 2, "卫生间": 2,
                 "办公室": 2, "会议室": 2, "走廊": 2, "建筑面积": 3},
    },
    "结构": {
        "layer": {"柱": 3, "梁": 3, "板": 3, "基础": 3, "钢筋": 3, "承台": 3, "剪力墙": 3,
                  "COLUMN": 3, "BEAM": 3, "SLAB": 3, "REBAR": 3, "FOUND": 3, "STRU": 2},
        "block": {"柱": 3, "梁": 3, "桩": 3, "COLUMN": 3, "BEAM": 3},
        "text": {"配筋": 3, "混凝土": 2, "钢筋": 3, "结构": 3, "承台": 3, "梁平法": 3},
    },
    "给排水": {
        "layer": {"给水": 3, "排水": 3, "雨水": 3, "污水": 3, "消火栓": 3, "喷淋": 3,
                  "管道": 3, "阀门": 3, "地漏": 3, "WATER": 3, "DRAIN": 3, "PIPE": 3,
                  "PLUMB": 2, "SPRINK": 3},
        "block": {"阀门": 3, "地漏": 3, "消火栓": 3, "喷头": 3, "水泵": 3, "洁具": 2},
        "text": {"给水": 3, "排水": 3, "DN": 3, "管径": 3, "消火栓": 3, "喷淋": 3,
                 "给排水": 3, "系统图": 2},
    },
    "电气": {
        "layer": {"照明": 3, "插座": 3, "开关": 3, "配电箱": 3, "桥架": 3, "电缆": 3,
                  "防雷": 3, "接地": 3, "LIGHT": 3, "POWER": 3, "SOCKET": 3,
                  "ELEC": 3, "CABLE": 3, "TRAY": 3},
        "block": {"灯具": 3, "插座": 3, "开关": 3, "配电箱": 3, "LIGHT": 3, "SOCKET": 3},
        "text": {"照明": 3, "插座": 3, "配电": 3, "电气": 3, "BV": 3, "YJV": 3,
                 "回路": 3, "配电箱": 3, "强电": 3, "弱电": 3},
    },
    "暖通": {
        "layer": {"风管": 3, "风口": 3, "空调": 3, "风机": 3, "冷媒": 3, "冷凝水": 3,
                  "保温": 2, "DUCT": 3, "AHU": 3, "FCU": 3, "HVAC": 3},
        "block": {"风口": 3, "风机": 3, "空调": 3, "阀": 2, "DUCT": 3},
        "text": {"风管": 3, "风口": 3, "空调": 3, "暖通": 3, "排烟": 3, "新风": 3,
                 "冷负荷": 3, "风量": 3},
    },
    "总图": {
        "layer": {"红线": 3, "道路": 3, "绿化": 3, "坐标": 3, "场地": 3, "竖向": 3,
                  "REDLINE": 3, "ROAD": 3, "SITE": 3},
        "block": {"指北针": 3, "坐标": 3, "树": 2, "车": 2},
        "text": {"总平面": 3, "红线": 3, "坐标": 3, "道路": 3, "绿化": 3, "竖向": 3},
    },
    "装饰": {
        "layer": {"地面": 3, "墙面": 3, "天花": 3, "踢脚": 3, "造型": 3, "饰面": 3,
                  "FLOOR": 2, "CEILING": 3, "DECO": 3},
        "block": {"家具": 3, "灯具": 2, "洁具": 2, "饰面": 3},
        "text": {"装饰": 3, "装修": 3, "地面": 3, "墙面": 3, "天花": 3, "材料表": 3},
    },
}
# 图例区常见标题关键词
LEGEND_TITLE_KEYWORDS = ["图例", "图 例", "LEGEND", "符号表", "图例表", "说明", "设计说明"]
# ---------------------------------------------------------------- 工具
def _dxf_attr(entity, name, default=None):
    try:
        return entity.dxf.get(name, default)
    except Exception:
        return default
def _round(v, n=3):
    try:
        return round(float(v), n)
    except Exception:
        return v
def _norm(s: str) -> str:
    return (s or "").strip().upper()
# ---------------------------------------------------------------- 1. 专业识别
class DisciplineClassifier:
    """基于图层名/图块名/文字的三重加权投票，判定图纸专业。"""
    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()
    def classify(self) -> dict:
        scores = defaultdict(float)
        evidence = defaultdict(lambda: defaultdict(list))
        # 1.1 图层名投票（权重最高）
        for layer in self.doc.layers:
            name = _norm(layer.dxf.name)
            for disc, d in DISCIPLINE_DICT.items():
                for kw, w in d["layer"].items():
                    if _norm(kw) in name:
                        scores[disc] += w
                        evidence[disc]["layers"].append(layer.dxf.name)
        # 1.2 图块名投票
        for blk in self.doc.blocks:
            if blk.name.startswith(("*", "_")):
                continue
            name = _norm(blk.name)
            for disc, d in DISCIPLINE_DICT.items():
                for kw, w in d["block"].items():
                    if _norm(kw) in name:
                        scores[disc] += w
                        evidence[disc]["blocks"].append(blk.name)
        # 1.3 文字内容投票
        for e in self.msp:
            t = e.dxftype()
            content = ""
            if t == "TEXT":
                content = _dxf_attr(e, "text", "")
            elif t == "MTEXT":
                content = e.plain_text() if hasattr(e, "plain_text") else _dxf_attr(e, "text", "")
            if not content:
                continue
            up = _norm(content)
            for disc, d in DISCIPLINE_DICT.items():
                for kw, w in d["text"].items():
                    if _norm(kw) in up:
                        scores[disc] += w
                        evidence[disc]["texts"].append(content[:40])
        if not scores:
            return {"primary": "未识别", "confidence": 0, "ranking": [], "evidence": {}}
        total = sum(scores.values())
        ranking = sorted(scores.items(), key=lambda x: -x[1])
        primary, top_score = ranking[0]
        confidence = _round(top_score / total * 100, 1) if total else 0
        # 去重证据
        ev = {}
        for disc, groups in evidence.items():
            ev[disc] = {k: list(dict.fromkeys(v))[:10] for k, v in groups.items()}
        return {
            "primary": primary,
            "confidence": confidence,
            "ranking": [{"discipline": d, "score": _round(s), "ratio": _round(s / total * 100, 1)}
                        for d, s in ranking],
            "evidence": ev,
            "is_multi_discipline": len([s for _, s in ranking if s / total > 0.2]) > 1,
        }
# ---------------------------------------------------------------- 2. 图例解析
class LegendParser:
    """定位图例区，提取符号→含义映射。"""
    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()
    def parse(self) -> dict:
        # 2.1 找到图例标题文字的位置
        legend_anchors = []
        for e in self.msp:
            t = e.dxftype()
            content = ""
            pos = None
            if t == "TEXT":
                content = _dxf_attr(e, "text", "")
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                pos = (ins[0], ins[1])
            elif t == "MTEXT":
                content = e.plain_text() if hasattr(e, "plain_text") else _dxf_attr(e, "text", "")
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                pos = (ins[0], ins[1])
            if not content or not pos:
                continue
            up = _norm(content)
            for kw in LEGEND_TITLE_KEYWORDS:
                if _norm(kw) in up:
                    legend_anchors.append({"text": content, "position": [_round(pos[0]), _round(pos[1])],
                                           "keyword": kw})
                    break
        if not legend_anchors:
            return {"found": False, "anchors": [], "entries": [],
                    "note": "未在图面文字中找到图例标题，可能图例在图纸空间或为图片"}
        # 2.2 以图例标题为锚点，收集其右下方区域的文字作为图例条目
        entries = []
        for anchor in legend_anchors:
            ax, ay = anchor["position"]
            nearby = []
            for e in self.msp:
                t = e.dxftype()
                if t not in ("TEXT", "MTEXT"):
                    continue
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                x, y = ins[0], ins[1]
                # 图例条目通常在标题下方或右侧，取一个合理邻域
                if abs(x - ax) < 8000 and -6000 < (y - ay) < 500:
                    content = (_dxf_attr(e, "text", "") if t == "TEXT"
                               else (e.plain_text() if hasattr(e, "plain_text") else ""))
                    if content and _norm(content) != _norm(anchor["text"]):
                        nearby.append({"content": content.strip(), "x": _round(x), "y": _round(y)})
            # 按 y 降序、x 升序排列，模拟阅读顺序
            nearby.sort(key=lambda r: (-r["y"], r["x"]))
            entries.extend(nearby)
        # 去重
        seen = set()
        uniq = []
        for it in entries:
            key = (it["content"], it["x"], it["y"])
            if key not in seen:
                seen.add(key)
                uniq.append(it)
        return {
            "found": True,
            "anchors": legend_anchors,
            "entries": uniq[:200],
            "entry_count": len(uniq),
            "note": f"共识别到 {len(uniq)} 条图例相关文字，需结合图面符号人工确认对应关系",
        }
# ---------------------------------------------------------------- 3. 跨图层关联
class CrossLayerAnalyzer:
    """空间邻近分析，还原不同图层构件间的空间关系。"""
    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()
    def analyze(self, tolerance: float = 3000.0) -> dict:
        # 3.1 收集各图层的代表性点位（块插入点 + 文字位置）
        layer_points = defaultdict(list)
        for e in self.msp:
            t = e.dxftype()
            layer = _dxf_attr(e, "layer", "0")
            if t == "INSERT":
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                layer_points[layer].append({
                    "type": "INSERT", "name": _dxf_attr(e, "name", ""),
                    "x": ins[0], "y": ins[1],
                })
            elif t in ("TEXT", "MTEXT"):
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                content = (_dxf_attr(e, "text", "") if t == "TEXT"
                           else (e.plain_text() if hasattr(e, "plain_text") else ""))
                if content.strip():
                    layer_points[layer].append({
                        "type": "TEXT", "name": content.strip()[:30],
                        "x": ins[0], "y": ins[1],
                    })
        # 3.2 找出不同图层间的空间邻近关系
        relations = []
        layers = list(layer_points.keys())
        for i in range(len(layers)):
            for j in range(i + 1, len(layers)):
                la, lb = layers[i], layers[j]
                # 限制比较规模，避免 O(n²) 爆炸
                pa = layer_points[la][:200]
                pb = layer_points[lb][:200]
                pairs = []
                for a in pa:
                    for b in pb:
                        d = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
                        if d <= tolerance:
                            pairs.append({
                                "from": {"layer": la, "name": a["name"], "pos": [_round(a["x"]), _round(a["y"])]},
                                "to": {"layer": lb, "name": b["name"], "pos": [_round(b["x"]), _round(b["y"])]},
                                "distance": _round(d, 1),
                            })
                if pairs:
                    pairs.sort(key=lambda p: p["distance"])
                    relations.append({
                        "layer_a": la, "layer_b": lb,
                        "pair_count": len(pairs),
                        "samples": pairs[:10],
                    })
        relations.sort(key=lambda r: -r["pair_count"])
        return {
            "tolerance": tolerance,
            "layer_point_counts": {k: len(v) for k, v in layer_points.items()},
            "relation_count": len(relations),
            "relations": relations[:30],
        }
# ---------------------------------------------------------------- 4. 工程量汇总
class QuantitySummarizer:
    """按图块/图层分类汇总工程量。"""
    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()
    def summarize(self) -> dict:
        # 4.1 图块工程量
        block_qty = Counter()
        block_layers = defaultdict(Counter)
        block_attrs = defaultdict(list)
        for e in self.msp:
            if e.dxftype() != "INSERT":
                continue
            name = _dxf_attr(e, "name", "UNKNOWN")
            block_qty[name] += 1
            block_layers[name][_dxf_attr(e, "layer", "0")] += 1
            try:
                if e.attribs:
                    block_attrs[name].append({a.dxf.tag: a.dxf.text for a in e.attribs})
            except Exception:
                pass
        block_table = []
        for name, qty in block_qty.most_common():
            block_table.append({
                "block_name": name,
                "quantity": qty,
                "layers": dict(block_layers[name]),
                "attribute_keys": list(block_attrs[name][0].keys()) if block_attrs[name] else [],
                "attribute_samples": block_attrs[name][:3],
            })
        # 4.2 线型构件长度统计（按图层）
        layer_length = defaultdict(float)
        for e in self.msp:
            t = e.dxftype()
            layer = _dxf_attr(e, "layer", "0")
            try:
                if t == "LINE":
                    s, en = e.dxf.start, e.dxf.end
                    layer_length[layer] += math.dist((s.x, s.y), (en.x, en.y))
                elif t == "LWPOLYLINE":
                    try:
                        layer_length[layer] += e.length()
                    except Exception:
                        # 兜底：按顶点逐段累加
                        pts = [(pt[0], pt[1]) for pt in e.get_points()]
                        seg = 0.0
                        for i in range(len(pts) - 1):
                            seg += math.dist(pts[i], pts[i + 1])
                        if e.closed and len(pts) > 2:
                            seg += math.dist(pts[-1], pts[0])
                        layer_length[layer] += seg
                elif t == "ARC":
                    span = (e.dxf.end_angle - e.dxf.start_angle) % 360
                    layer_length[layer] += math.radians(span) * e.dxf.radius
                elif t == "CIRCLE":
                    layer_length[layer] += 2 * math.pi * e.dxf.radius
            except Exception:
                continue
        length_table = [
            {"layer": k, "total_length": _round(v, 2)}
            for k, v in sorted(layer_length.items(), key=lambda x: -x[1]) if v > 0
        ]
        # 4.3 闭合多段线面积（按图层）
        layer_area = defaultdict(float)
        for e in self.msp:
            if e.dxftype() == "LWPOLYLINE":
                try:
                    if e.closed:
                        layer_area[_dxf_attr(e, "layer", "0")] += e.area()
                except Exception:
                    continue
        area_table = [
            {"layer": k, "total_area": _round(v, 2)}
            for k, v in sorted(layer_area.items(), key=lambda x: -x[1]) if v > 0
        ]
        return {
            "block_table": block_table,
            "length_table": length_table,
            "area_table": area_table,
        }
# ---------------------------------------------------------------- 5. Excel 导出
def export_excel(result: dict, out_path: str) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        raise RuntimeError("导出 Excel 需要 openpyxl，请执行: pip install openpyxl")
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    def style_header(ws, ncol):
        for c in range(1, ncol + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border
        ws.freeze_panes = "A2"
    def style_body(ws, nrow, ncol):
        for r in range(2, nrow + 1):
            for c in range(1, ncol + 1):
                ws.cell(row=r, column=c).border = border
                ws.cell(row=r, column=c).alignment = Alignment(vertical="center")
    # Sheet1 图块工程量
    ws1 = wb.active
    ws1.title = "图块工程量"
    ws1.append(["序号", "图块名称", "数量", "所在图层", "属性字段"])
    for i, r in enumerate(result["quantity"]["block_table"], 1):
        ws1.append([i, r["block_name"], r["quantity"],
                    "、".join(r["layers"].keys()), "、".join(r["attribute_keys"])])
    style_header(ws1, 5)
    style_body(ws1, ws1.max_row, 5)
    for col, w in zip("ABCDE", [6, 24, 10, 24, 30]):
        ws1.column_dimensions[col].width = w
    # Sheet2 图层长度
    ws2 = wb.create_sheet("图层长度")
    ws2.append(["序号", "图层", "总长度"])
    for i, r in enumerate(result["quantity"]["length_table"], 1):
        ws2.append([i, r["layer"], r["total_length"]])
    style_header(ws2, 3)
    style_body(ws2, ws2.max_row, 3)
    for col, w in zip("ABC", [6, 24, 16]):
        ws2.column_dimensions[col].width = w
    # Sheet3 图层面积
    ws3 = wb.create_sheet("图层面积")
    ws3.append(["序号", "图层", "总面积"])
    for i, r in enumerate(result["quantity"]["area_table"], 1):
        ws3.append([i, r["layer"], r["total_area"]])
    style_header(ws3, 3)
    style_body(ws3, ws3.max_row, 3)
    for col, w in zip("ABC", [6, 24, 16]):
        ws3.column_dimensions[col].width = w
    # Sheet4 专业识别
    ws4 = wb.create_sheet("专业识别")
    ws4.append(["专业", "得分", "占比(%)"])
    for r in result["discipline"]["ranking"]:
        ws4.append([r["discipline"], r["score"], r["ratio"]])
    style_header(ws4, 3)
    style_body(ws4, ws4.max_row, 3)
    for col, w in zip("ABC", [16, 12, 12]):
        ws4.column_dimensions[col].width = w
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
    return out_path
# ---------------------------------------------------------------- 报告输出
def write_analysis_md(result: dict, out_path: str):
    L = []
    A = L.append
    A("# CAD 图纸智能分析报告\n")
    A(f"> 文件：`{result['file_name']}`　|　分析时间：{result['analyzed_at']}\n")
    # 一、专业识别
    A("## 一、专业识别\n")
    d = result["discipline"]
    A(f"**判定专业：{d['primary']}**（置信度 {d['confidence']}%）\n")
    if d.get("is_multi_discipline"):
        A("> ⚠️ 本图为多专业综合图纸，以下为各专业占比：\n")
    A("| 专业 | 得分 | 占比 |")
    A("|---|---:|---:|")
    for r in d["ranking"]:
        A(f"| {r['discipline']} | {r['score']} | {r['ratio']}% |")
    A("")
    if d.get("evidence"):
        A("### 判定依据\n")
        for disc, groups in d["evidence"].items():
            if disc != d["primary"]:
                continue
            for kind, items in groups.items():
                if items:
                    label = {"layers": "图层", "blocks": "图块", "texts": "文字"}.get(kind, kind)
                    A(f"- **{label}**：{'、'.join(str(i) for i in items[:8])}")
        A("")
    # 二、图例解析
    A("## 二、图例解析\n")
    lg = result["legend"]
    if not lg["found"]:
        A(f"未找到图例区域。{lg.get('note', '')}\n")
    else:
        A(f"找到 {len(lg['anchors'])} 处图例标题，识别到 {lg['entry_count']} 条相关文字。\n")
        A("| 图例标题 | 位置 |")
        A("|---|---|")
        for a in lg["anchors"]:
            A(f"| {a['text']} | ({a['position'][0]}, {a['position'][1]}) |")
        A("")
        if lg["entries"]:
            A("### 图例区文字条目\n")
            A("| 内容 | 位置 |")
            A("|---|---|")
            for it in lg["entries"][:50]:
                A(f"| {it['content'][:50]} | ({it['x']}, {it['y']}) |")
            A("")
    # 三、跨图层关联
    A("## 三、跨图层关联分析\n")
    cl = result["cross_layer"]
    A(f"分析容差：{cl['tolerance']} 图形单位\n")
    if not cl["relations"]:
        A("未发现明显的跨图层空间关联。\n")
    else:
        A(f"发现 {cl['relation_count']} 组图层间空间关联：\n")
        A("| 图层A | 图层B | 邻近对数 |")
        A("|---|---|---:|")
        for r in cl["relations"]:
            A(f"| {r['layer_a']} | {r['layer_b']} | {r['pair_count']} |")
        A("")
        A("### 关联示例（距离最近的组合）\n")
        for r in cl["relations"][:3]:
            A(f"**{r['layer_a']} ↔ {r['layer_b']}**：")
            for s in r["samples"][:5]:
                A(f"  - {s['from']['name']}（{s['from']['layer']}）↔ {s['to']['name']}（{s['to']['layer']}），距离 {s['distance']}")
            A("")
    # 四、工程量汇总
    A("## 四、工程量汇总\n")
    q = result["quantity"]
    A("### 4.1 图块工程量\n")
    if q["block_table"]:
        A("| 序号 | 图块名称 | 数量 | 所在图层 |")
        A("|---|---|---:|---|")
        for i, r in enumerate(q["block_table"], 1):
            A(f"| {i} | {r['block_name']} | {r['quantity']} | {'、'.join(r['layers'].keys())} |")
        A("")
        # 属性明细
        with_attr = [r for r in q["block_table"] if r["attribute_samples"]]
        if with_attr:
            A("**图块属性明细：**\n")
            for r in with_attr[:5]:
                A(f"- **{r['block_name']}**（{r['quantity']} 个）属性字段：{'、'.join(r['attribute_keys'])}")
                for s in r["attribute_samples"][:3]:
                    A(f"  - {'、'.join(f'{k}={v}' for k, v in s.items())}")
            A("")
    else:
        A("图纸中无图块参照。\n")
    A("### 4.2 图层线长统计\n")
    if q["length_table"]:
        A("| 图层 | 总长度 |")
        A("|---|---:|")
        for r in q["length_table"]:
            A(f"| {r['layer']} | {r['total_length']} |")
        A("")
    else:
        A("无线性构件。\n")
    A("### 4.3 图层面积统计（闭合多段线）\n")
    if q["area_table"]:
        A("| 图层 | 总面积 |")
        A("|---|---:|")
        for r in q["area_table"]:
            A(f"| {r['layer']} | {r['total_area']} |")
        A("")
    else:
        A("无闭合多段线。\n")
    # 五、专业判断
    A("## 五、专业判断与建议\n")
    A(f"- **图纸专业**：判定为「{d['primary']}」专业图纸，置信度 {d['confidence']}%。")
    if d["confidence"] < 50:
        A("  - ⚠️ 置信度偏低，可能是多专业综合图或图层命名不规范，建议人工确认。")
    if lg["found"]:
        A(f"- **图例**：图纸包含图例说明（{lg['entry_count']} 条），建议结合图例核对符号含义。")
    else:
        A("- **图例**：未检测到图例区，若图纸含特殊符号，需人工提供符号含义。")
    if q["block_table"]:
        top = q["block_table"][0]
        A(f"- **主要构件**：{top['block_name']} 数量最多（{top['quantity']} 个），是图纸主体构件。")
    if cl["relations"]:
        top_rel = cl["relations"][0]
        A(f"- **空间关联**：{top_rel['layer_a']} 与 {top_rel['layer_b']} 图层构件空间关系最密切"
          f"（{top_rel['pair_count']} 对邻近），可能存在配套关系。")
    A("")
    A("---\n")
    A("*本报告由 CAD 图纸智能分析技能自动生成。所有数据来自 DXF 文件实际解析，未做任何推测填充。*")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="CAD 图纸智能分析（专业识别/图例/关联/工程量）")
    ap.add_argument("dxf", help="DXF 图纸路径")
    ap.add_argument("--out", default=".", help="报告输出目录")
    ap.add_argument("--excel", action="store_true", help="导出工程量 Excel")
    ap.add_argument("--tolerance", type=float, default=3000.0, help="跨图层关联容差")
    args = ap.parse_args()
    # DWG 自动转换
    args.dxf = _ensure_dxf_input(args.dxf)
    os.makedirs(args.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.dxf))[0]
    try:
        doc = ezdxf.readfile(args.dxf)
    except Exception as e:
        print(f"[ERROR] 读取失败: {e}", file=sys.stderr)
        sys.exit(2)
    result = {
        "file_name": os.path.basename(args.dxf),
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    result["discipline"] = DisciplineClassifier(doc).classify()
    result["legend"] = LegendParser(doc).parse()
    result["cross_layer"] = CrossLayerAnalyzer(doc).analyze(args.tolerance)
    result["quantity"] = QuantitySummarizer(doc).summarize()
    md_path = os.path.join(args.out, f"{base}_智能分析报告.md")
    write_analysis_md(result, md_path)
    print(f"[OK] 智能分析报告: {md_path}")
    json_path = os.path.join(args.out, f"{base}_智能分析数据.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON 数据: {json_path}")
    if args.excel:
        try:
            xl = os.path.join(args.out, f"{base}_工程量清单.xlsx")
            export_excel(result, xl)
            print(f"[OK] Excel 清单: {xl}")
        except Exception as e:
            print(f"[WARN] Excel 导出失败: {e}", file=sys.stderr)
    d = result["discipline"]
    print("\n===== 智能分析摘要 =====")
    print(f"判定专业  : {d['primary']}（置信度 {d['confidence']}%）")
    print(f"图例      : {'找到' if result['legend']['found'] else '未找到'}")
    print(f"跨层关联  : {result['cross_layer']['relation_count']} 组")
    print(f"图块种类  : {len(result['quantity']['block_table'])}")
    print(f"线长图层  : {len(result['quantity']['length_table'])}")
if __name__ == "__main__":
    main()