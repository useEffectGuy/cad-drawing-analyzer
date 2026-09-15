#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAD 图纸解析与统计核心脚本 (DXF)
用法:
    python cad_inspect.py <drawing.dxf> [--out report_dir] [--json]
功能:
    1. 读取 DXF 图纸，输出图纸概览（版本、单位、范围、实体总数）
    2. 图层清单与各图层实体数量、颜色、线型、开关状态
    3. 实体类型统计（LINE/CIRCLE/ARC/LWPOLYLINE/TEXT/INSERT...）
    4. 图块(Block)定义清单 + 块参照(INSERT)数量统计 —— 工程量统计核心
    5. 文字内容提取（TEXT/MTEXT/ATTRIB），支持关键词检索
    6. 尺寸标注(DIMENSION)清单与数量
    7. 图纸规范性检查（空图层、零长度线、0图层实体、文字过小等）
    8. 输出 JSON + Markdown 报告
依赖: ezdxf
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
    from ezdxf import bbox
except ImportError:
    print("[ERROR] 缺少依赖 ezdxf，请先执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------- 常量
# 系统块前缀（*Model_Space、*Paper_Space 等）与标注箭头块（_ARCHTICK 等）
SYSTEM_BLOCK_PREFIXES = ("*", "_")
DIM_TYPE_NAMES = {
    0: "线性/旋转", 1: "对齐", 2: "角度", 3: "直径",
    4: "半径", 5: "角度3点", 6: "坐标",
}
UNITS_MAP = {
    0: "Unitless(无单位)", 1: "Inches(英寸)", 2: "Feet(英尺)",
    3: "Miles(英里)", 4: "Millimeters(毫米)", 5: "Centimeters(厘米)",
    6: "Meters(米)", 7: "Kilometers(千米)", 8: "Microinches(微英寸)",
    9: "Mils(密尔)", 10: "Yards(码)", 11: "Angstroms(埃)",
    12: "Nanometers(纳米)", 13: "Microns(微米)", 14: "Decimeters(分米)",
    15: "Decameters(十米)", 16: "Hectometers(百米)", 17: "Gigameters",
    18: "Astronomical(天文单位)", 19: "Light years(光年)", 20: "Parsecs(秒差距)",
}
# ---------------------------------------------------------------- 工具函数
def _dxf_attr(entity, name, default=None):
    """安全读取 entity.dxf.<name>。"""
    try:
        return entity.dxf.get(name, default)
    except Exception:
        return default
def _round(v, n=3):
    try:
        return round(float(v), n)
    except Exception:
        return v
def _call_bool(obj, method_name: str, default: bool = False) -> bool:
    """安全调用返回 bool 的方法（如 Layer.is_off()）。"""
    try:
        m = getattr(obj, method_name, None)
        if m is None:
            return default
        return bool(m() if callable(m) else m)
    except Exception:
        return default
def _is_system_block(name: str) -> bool:
    """判断是否为系统块/标注内部块（非用户图块）。"""
    return name.startswith(SYSTEM_BLOCK_PREFIXES)
def _tokenize(text: str) -> list:
    """粗粒度分词：中文按 2-gram，英文数字按连续串。"""
    tokens = []
    tokens += re.findall(r"[A-Za-z0-9_\-]{2,}", text)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) <= 2:
            tokens.append(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i + 2])
    return tokens
# ---------------------------------------------------------------- 解析主体
class CadInspector:
    """DXF 图纸解析器。"""
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        if not os.path.isfile(self.path):
            raise FileNotFoundError(f"图纸文件不存在: {self.path}")
        self.doc = ezdxf.readfile(self.path)
        self.msp = self.doc.modelspace()
        self.result: dict = {}
    # -------------------------------------------------- 1. 图纸概览
    def overview(self) -> dict:
        doc = self.doc
        # 单位：优先 $INSUNITS，其次 $MEASUREMENT
        units_code = _safe_header(doc, "$INSUNITS", 0)
        if units_code in (None, 0):
            # 回退：$MEASUREMENT 1=公制 0=英制
            meas = _safe_header(doc, "$MEASUREMENT", None)
            if meas == 1:
                units_code = 4  # 默认按毫米
                units_note = "未显式设置，按公制推断为毫米"
            elif meas == 0:
                units_code = 1
                units_note = "未显式设置，按英制推断为英寸"
            else:
                units_note = "图纸未设置单位，请人工确认"
        else:
            units_note = ""
        info = {
            "file_name": os.path.basename(self.path),
            "file_path": self.path,
            "file_size_kb": _round(os.path.getsize(self.path) / 1024, 2),
            "dxf_version": doc.dxfversion,
            "acad_release": _safe_header(doc, "$ACADVER", "unknown"),
            "units_code": units_code,
            "units_name": UNITS_MAP.get(units_code, f"未知({units_code})"),
            "units_note": units_note,
            "modelspace_entities": len(self.msp),
            "paperspace_layouts": len(doc.layouts),
            "layer_count": len(doc.layers),
            "block_definitions": len([b for b in doc.blocks if not _is_system_block(b.name)]),
            "text_styles": len(doc.styles),
            "dimension_styles": len(doc.dimstyles),
        }
        # 图纸范围
        try:
            ext = bbox.extents(self.msp, fast=True)
            if ext.has_data:
                info["extents"] = {
                    "min": [_round(ext.extmin.x), _round(ext.extmin.y)],
                    "max": [_round(ext.extmax.x), _round(ext.extmax.y)],
                    "width": _round(ext.size.x),
                    "height": _round(ext.size.y),
                }
            else:
                info["extents"] = None
        except Exception as e:
            info["extents"] = None
            info["extents_error"] = str(e)
        self.result["overview"] = info
        return info
    # -------------------------------------------------- 2. 图层分析
    def layers(self) -> list:
        rows = []
        layer_entity_count = Counter()
        layer_type_count = defaultdict(Counter)
        for e in self.msp:
            lname = _dxf_attr(e, "layer", "0")
            layer_entity_count[lname] += 1
            layer_type_count[lname][e.dxftype()] += 1
        for layer in self.doc.layers:
            name = layer.dxf.name
            # 图层状态：is_off / is_frozen / is_locked 是方法，必须调用
            is_off = _call_bool(layer, "is_off")
            is_frozen = _call_bool(layer, "is_frozen")
            is_locked = _call_bool(layer, "is_locked")
            rows.append({
                "name": name,
                "color": _dxf_attr(layer, "color", 7),
                "linetype": _dxf_attr(layer, "linetype", "CONTINUOUS"),
                "lineweight": _dxf_attr(layer, "lineweight", None),
                "is_off": is_off,
                "is_frozen": is_frozen,
                "is_locked": is_locked,
                "entity_count": layer_entity_count.get(name, 0),
                "entity_types": dict(layer_type_count.get(name, {})),
            })
        rows.sort(key=lambda r: (-r["entity_count"], r["name"]))
        self.result["layers"] = rows
        return rows
    # -------------------------------------------------- 3. 实体类型统计
    def entity_stats(self) -> dict:
        counter = Counter()
        for e in self.msp:
            counter[e.dxftype()] += 1
        total = sum(counter.values())
        rows = [
            {"type": t, "count": c, "ratio": _round(c / total * 100, 2) if total else 0}
            for t, c in counter.most_common()
        ]
        self.result["entity_stats"] = {"total": total, "rows": rows}
        return self.result["entity_stats"]
    # -------------------------------------------------- 4. 图块统计（工程量核心）
    def blocks(self) -> dict:
        # 4.1 用户块定义清单（排除系统块）
        defs = []
        for blk in self.doc.blocks:
            if _is_system_block(blk.name):
                continue
            bp = _dxf_attr(blk.block, "base_point", (0, 0, 0))
            defs.append({
                "name": blk.name,
                "entity_count": len(blk),
                "base_point": [_round(bp[0]), _round(bp[1])],
                "has_attributes": any(e.dxftype() == "ATTDEF" for e in blk),
            })
        # 4.2 块参照统计（模型空间中实际插入数量）
        insert_counter = Counter()
        insert_layers = defaultdict(Counter)
        insert_attrs = defaultdict(list)
        for e in self.msp:
            if e.dxftype() != "INSERT":
                continue
            bname = _dxf_attr(e, "name", "UNKNOWN")
            insert_counter[bname] += 1
            insert_layers[bname][_dxf_attr(e, "layer", "0")] += 1
            try:
                if e.attribs:
                    attrs = {a.dxf.tag: a.dxf.text for a in e.attribs}
                    if attrs and len(insert_attrs[bname]) < 50:
                        insert_attrs[bname].append(attrs)
            except Exception:
                pass
        usage = []
        for name, cnt in insert_counter.most_common():
            usage.append({
                "block_name": name,
                "insert_count": cnt,
                "layers": dict(insert_layers[name]),
                "sample_attributes": insert_attrs.get(name, [])[:5],
                "defined": name in self.doc.blocks,
            })
        # 4.3 定义了但未使用的用户块
        used = set(insert_counter.keys())
        unused = [d["name"] for d in defs if d["name"] not in used]
        self.result["blocks"] = {
            "definition_count": len(defs),
            "definitions": defs,
            "insert_total": sum(insert_counter.values()),
            "usage": usage,
            "unused_definitions": unused,
        }
        return self.result["blocks"]
    # -------------------------------------------------- 5. 文字提取
    def texts(self) -> dict:
        items = []
        for e in self.msp:
            t = e.dxftype()
            try:
                if t == "TEXT":
                    ins = _dxf_attr(e, "insert", (0, 0, 0))
                    items.append({
                        "type": "TEXT",
                        "content": _dxf_attr(e, "text", ""),
                        "layer": _dxf_attr(e, "layer", "0"),
                        "position": [_round(ins[0]), _round(ins[1])],
                        "height": _round(_dxf_attr(e, "height", 0)),
                    })
                elif t == "MTEXT":
                    ins = _dxf_attr(e, "insert", (0, 0, 0))
                    content = e.plain_text() if hasattr(e, "plain_text") else _dxf_attr(e, "text", "")
                    items.append({
                        "type": "MTEXT",
                        "content": content,
                        "layer": _dxf_attr(e, "layer", "0"),
                        "position": [_round(ins[0]), _round(ins[1])],
                        "height": _round(_dxf_attr(e, "char_height", 0)),
                    })
                elif t == "INSERT":
                    for a in getattr(e, "attribs", []):
                        ins = _dxf_attr(a, "insert", (0, 0, 0))
                        items.append({
                            "type": "ATTRIB",
                            "content": _dxf_attr(a, "text", ""),
                            "tag": _dxf_attr(a, "tag", ""),
                            "layer": _dxf_attr(a, "layer", "0"),
                            "position": [_round(ins[0]), _round(ins[1])],
                            "height": _round(_dxf_attr(a, "height", 0)),
                        })
            except Exception:
                continue
        # 关键词词频（过滤纯数字噪声）
        word_counter = Counter()
        for it in items:
            content = (it.get("content") or "").strip()
            if not content:
                continue
            for token in _tokenize(content):
                if token.isdigit():
                    continue
                word_counter[token] += 1
        self.result["texts"] = {
            "total": len(items),
            "items": items,
            "top_keywords": [{"word": w, "count": c} for w, c in word_counter.most_common(30)],
        }
        return self.result["texts"]
    # -------------------------------------------------- 6. 标注统计
    def dimensions(self) -> dict:
        rows = []
        counter = Counter()
        for e in self.msp:
            if e.dxftype() != "DIMENSION":
                continue
            dtype = _dxf_attr(e, "dimtype", 0) or 0
            base_type = int(dtype) & 7
            counter[base_type] += 1
            try:
                measurement = e.get_measurement()
                if isinstance(measurement, (list, tuple)):
                    measurement = measurement[0] if measurement else None
            except Exception:
                measurement = None
            rows.append({
                "dim_type_code": base_type,
                "dim_type_name": DIM_TYPE_NAMES.get(base_type, f"其他({base_type})"),
                "layer": _dxf_attr(e, "layer", "0"),
                "measurement": _round(measurement) if measurement is not None else None,
                "text_override": _dxf_attr(e, "text", ""),
            })
        by_type = [
            {"dim_type_name": DIM_TYPE_NAMES.get(k, f"其他({k})"), "count": v}
            for k, v in counter.most_common()
        ]
        self.result["dimensions"] = {
            "total": len(rows),
            "by_type": by_type,
            "items": rows[:500],
            "truncated": len(rows) > 500,
        }
        return self.result["dimensions"]
    # -------------------------------------------------- 7. 规范性检查
    def quality_check(self) -> dict:
        issues = []
        msp = self.msp
        # 7.1 未使用图层（排除系统图层 Defpoints）
        used_layers = {_dxf_attr(e, "layer", "0") for e in msp}
        empty_layers = [l.dxf.name for l in self.doc.layers
                        if l.dxf.name not in used_layers
                        and l.dxf.name not in ("0", "Defpoints")]
        if empty_layers:
            issues.append({
                "level": "info",
                "category": "未使用图层",
                "detail": f"共 {len(empty_layers)} 个图层无任何实体，建议清理",
                "items": empty_layers[:30],
            })
        # 7.2 零长度线 / 零半径圆
        zero_len = 0
        tiny_circle = 0
        for e in msp:
            t = e.dxftype()
            try:
                if t == "LINE":
                    s, en = e.dxf.start, e.dxf.end
                    if math.dist((s.x, s.y), (en.x, en.y)) < 1e-6:
                        zero_len += 1
                elif t == "CIRCLE":
                    if (_dxf_attr(e, "radius", 1) or 0) < 1e-6:
                        tiny_circle += 1
            except Exception:
                continue
        if zero_len:
            issues.append({"level": "warn", "category": "零长度直线",
                           "detail": f"发现 {zero_len} 条起终点重合的 LINE，建议清理"})
        if tiny_circle:
            issues.append({"level": "warn", "category": "零半径圆",
                           "detail": f"发现 {tiny_circle} 个半径近似为 0 的 CIRCLE"})
        # 7.3 文字高度异常
        small_text = 0
        for e in msp:
            if e.dxftype() in ("TEXT", "MTEXT"):
                h = _dxf_attr(e, "height", _dxf_attr(e, "char_height", 0)) or 0
                if 0 < h < 0.5:
                    small_text += 1
        if small_text:
            issues.append({"level": "info", "category": "文字高度过小",
                           "detail": f"{small_text} 处文字高度 < 0.5 图形单位，可能影响打印可读性"})
        # 7.4 实体位于 0 图层
        layer0_count = sum(1 for e in msp if _dxf_attr(e, "layer", "0") == "0")
        if layer0_count > 0:
            issues.append({"level": "info", "category": "实体位于 0 图层",
                           "detail": f"{layer0_count} 个实体在默认图层 0 上，建议按专业归层"})
        # 7.5 图块属性为空
        missing_attr = 0
        for e in msp:
            if e.dxftype() == "INSERT":
                try:
                    if e.attribs and any(not (a.dxf.text or "").strip() for a in e.attribs):
                        missing_attr += 1
                except Exception:
                    continue
        if missing_attr:
            issues.append({"level": "warn", "category": "图块属性为空",
                           "detail": f"{missing_attr} 个带属性图块的属性值为空，可能影响设备/材料统计"})
        # 7.6 图层被关闭/冻结（交付前应恢复）
        off_layers = [l.dxf.name for l in self.doc.layers
                      if _call_bool(l, "is_off") and l.dxf.name != "Defpoints"]
        frozen_layers = [l.dxf.name for l in self.doc.layers
                         if _call_bool(l, "is_frozen")]
        if off_layers:
            issues.append({"level": "info", "category": "图层被关闭",
                           "detail": f"{len(off_layers)} 个图层处于关闭状态，交付前建议检查",
                           "items": off_layers[:20]})
        if frozen_layers:
            issues.append({"level": "info", "category": "图层被冻结",
                           "detail": f"{len(frozen_layers)} 个图层处于冻结状态",
                           "items": frozen_layers[:20]})
        self.result["quality_check"] = {"issue_count": len(issues), "issues": issues}
        return self.result["quality_check"]
    # -------------------------------------------------- 汇总
    def run_all(self) -> dict:
        self.overview()
        self.layers()
        self.entity_stats()
        self.blocks()
        self.texts()
        self.dimensions()
        self.quality_check()
        self.result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self.result
def _safe_header(doc, name, default=None):
    """安全读取 DXF 头变量。"""
    try:
        v = doc.header.get(name, default)
        return default if v is None else v
    except Exception:
        return default
# ---------------------------------------------------------------- 报告输出
def write_markdown(res: dict, out_path: str):
    ov = res["overview"]
    L = []
    A = L.append
    A("# CAD 图纸分析报告\n")
    A(f"> 文件：`{ov['file_name']}`　|　分析时间：{res['analyzed_at']}\n")
    A("## 一、图纸概览\n")
    A("| 项目 | 值 |")
    A("|---|---|")
    A(f"| 文件大小 | {ov['file_size_kb']} KB |")
    A(f"| DXF 版本 | {ov['dxf_version']} |")
    A(f"| 图形单位 | {ov['units_name']}{'（' + ov['units_note'] + '）' if ov.get('units_note') else ''} |")
    A(f"| 模型空间实体数 | {ov['modelspace_entities']} |")
    A(f"| 图层数 | {ov['layer_count']} |")
    A(f"| 用户块定义数 | {ov['block_definitions']} |")
    A(f"| 标注样式数 | {ov['dimension_styles']} |")
    if ov.get("extents"):
        e = ov["extents"]
        A(f"| 图纸范围 | X: {e['min'][0]} ~ {e['max'][0]}　Y: {e['min'][1]} ~ {e['max'][1]} |")
        A(f"| 图纸尺寸 | {e['width']} × {e['height']} |")
    A("")
    A("## 二、图层清单\n")
    A("| 图层名 | 实体数 | 颜色 | 线型 | 状态 |")
    A("|---|---:|---:|---|---|")
    for r in res["layers"]:
        state = []
        if r["is_off"]:
            state.append("关闭")
        if r["is_frozen"]:
            state.append("冻结")
        if r["is_locked"]:
            state.append("锁定")
        A(f"| {r['name']} | {r['entity_count']} | {r['color']} | {r['linetype']} | {'/'.join(state) or '正常'} |")
    A("")
    A("## 三、实体类型统计\n")
    es = res["entity_stats"]
    A(f"模型空间实体总数：**{es['total']}**\n")
    A("| 实体类型 | 数量 | 占比 |")
    A("|---|---:|---:|")
    for r in es["rows"]:
        A(f"| {r['type']} | {r['count']} | {r['ratio']}% |")
    A("")
    A("## 四、图块统计（工程量）\n")
    bl = res["blocks"]
    A(f"用户块定义数：**{bl['definition_count']}**　|　块参照插入总数：**{bl['insert_total']}**\n")
    if bl["usage"]:
        A("### 4.1 图块使用数量\n")
        A("| 图块名称 | 插入数量 | 所在图层 |")
        A("|---|---:|---|")
        for u in bl["usage"]:
            layers = "、".join(f"{k}({v})" for k, v in u["layers"].items())
            A(f"| {u['block_name']} | {u['insert_count']} | {layers} |")
        A("")
        # 属性明细
        has_attr = [u for u in bl["usage"] if u["sample_attributes"]]
        if has_attr:
            A("### 4.2 图块属性明细（前 5 条示例）\n")
            for u in has_attr:
                A(f"**{u['block_name']}**（共 {u['insert_count']} 个）：")
                for i, attrs in enumerate(u["sample_attributes"][:5], 1):
                    kv = "、".join(f"{k}={v}" for k, v in attrs.items())
                    A(f"  {i}. {kv}")
                A("")
    if bl["unused_definitions"]:
        A("### 4.3 已定义未使用的图块\n")
        A("、".join(bl["unused_definitions"][:50]))
        A("")
    A("## 五、文字内容提取\n")
    tx = res["texts"]
    A(f"文字对象总数：**{tx['total']}**\n")
    if tx["top_keywords"]:
        A("### 5.1 高频关键词（已过滤纯数字）\n")
        A("| 关键词 | 出现次数 |")
        A("|---|---:|")
        for k in tx["top_keywords"][:20]:
            A(f"| {k['word']} | {k['count']} |")
        A("")
    A("### 5.2 文字明细（前 100 条）\n")
    A("| 类型 | 内容 | 图层 |")
    A("|---|---|---|")
    for it in tx["items"][:100]:
        content = (it.get("content") or "").replace("\n", " ").replace("|", "/")[:80]
        A(f"| {it['type']} | {content} | {it['layer']} |")
    if tx["total"] > 100:
        A(f"\n> 共 {tx['total']} 条，此处仅展示前 100 条，完整数据见 JSON。")
    A("")
    A("## 六、尺寸标注统计\n")
    dm = res["dimensions"]
    A(f"标注总数：**{dm['total']}**\n")
    if dm["by_type"]:
        A("| 标注类型 | 数量 |")
        A("|---|---:|")
        for r in dm["by_type"]:
            A(f"| {r['dim_type_name']} | {r['count']} |")
        A("")
    A("## 七、图纸规范性检查\n")
    qc = res["quality_check"]
    if not qc["issues"]:
        A("未发现明显规范性问题。\n")
    else:
        A(f"共发现 **{qc['issue_count']}** 项待关注问题：\n")
        for i, iss in enumerate(qc["issues"], 1):
            A(f"{i}. **[{iss['level'].upper()}] {iss['category']}** — {iss['detail']}")
            if iss.get("items"):
                A(f"   - 示例：{'、'.join(str(x) for x in iss['items'][:10])}")
        A("")
    A("---\n")
    A("*本报告由 CAD 图纸分析技能自动生成，数据来源于 DXF 文件解析结果。*")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="CAD DXF 图纸解析与统计")
    ap.add_argument("dxf", help="DXF 图纸路径")
    ap.add_argument("--out", default=".", help="报告输出目录")
    ap.add_argument("--json", action="store_true", help="同时输出 JSON")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.dxf))[0]
    try:
        inspector = CadInspector(args.dxf)
        res = inspector.run_all()
    except Exception as e:
        print(f"[ERROR] 解析失败: {e}", file=sys.stderr)
        sys.exit(2)
    md_path = os.path.join(args.out, f"{base}_分析报告.md")
    write_markdown(res, md_path)
    print(f"[OK] Markdown 报告: {md_path}")
    if args.json:
        json_path = os.path.join(args.out, f"{base}_分析数据.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"[OK] JSON 数据: {json_path}")
    ov = res["overview"]
    print("\n===== 图纸摘要 =====")
    print(f"文件      : {ov['file_name']}")
    print(f"DXF版本   : {ov['dxf_version']}")
    print(f"图形单位  : {ov['units_name']}")
    print(f"实体总数  : {ov['modelspace_entities']}")
    print(f"图层数    : {ov['layer_count']}")
    print(f"块定义数  : {ov['block_definitions']}")
    print(f"块插入数  : {res['blocks']['insert_total']}")
    print(f"文字数    : {res['texts']['total']}")
    print(f"标注数    : {res['dimensions']['total']}")
    print(f"问题项    : {res['quality_check']['issue_count']}")
if __name__ == "__main__":
    main()