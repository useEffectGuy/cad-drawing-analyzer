#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CIO 数据契约转换器 —— 将 DXF 图纸转换为标准 CadInsightObject (CIO)
用法:
    python cad_cio.py <drawing.dxf> [--out cio.json] [--validate]
功能:
    1. 将 DXF 解析为统一的 CIO JSON 结构（project_meta / parsed_entities / global_context）
    2. 实体分类（category / sub_category）：按图层名+图块名智能归类
    3. 几何标准化（coordinates / bbox）
    4. 拓扑关系补全（空间邻近）
    5. JSON Schema 校验（--validate）
设计说明:
    CIO 是全系统模块间唯一的数据流转契约。感知层输出 geometry + label_text，
    解析层补全 attributes + topology，分析层只读并追加 validation_errors。
依赖: ezdxf（Schema 校验需 jsonschema）
"""
from __future__ import annotations
import argparse
import hashlib
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
    print("[ERROR] 缺少依赖 ezdxf，请执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)
CIO_SCHEMA_VERSION = "1.0.0"
# ---------------------------------------------------------------- 分类词典
# category 分类：按图层名/图块名关键词匹配，命中即归类
CATEGORY_RULES = [
    # (category, sub_category, 关键词列表)
    ("WALL", None, ["墙", "WALL", "剪力墙"]),
    ("DOOR", None, ["门", "DOOR"]),
    ("WINDOW", None, ["窗", "WINDOW"]),
    ("COLUMN", None, ["柱", "COLUMN", "KZ", "GZ"]),
    ("BEAM", None, ["梁", "BEAM", "KL", "LL"]),
    ("SLAB", None, ["板", "SLAB", "楼板"]),
    ("STAIR", None, ["楼梯", "STAIR", "LT"]),
    ("AXIS", None, ["轴线", "AXIS"]),
    ("DIMENSION", None, ["标注", "DIM", "尺寸"]),
    ("TEXT", None, ["文字", "TEXT", "TXT", "注释"]),
    ("HATCH", None, ["填充", "HATCH", "剖面线"]),
    ("VALVE", None, ["阀", "VALVE"]),
    ("PIPE", None, ["管道", "PIPE", "给水", "排水", "WATER", "DRAIN"]),
    ("DUCT", None, ["风管", "DUCT", "风道"]),
    ("EQUIPMENT", None, ["设备", "EQUIP", "机组", "水泵", "风机", "空调", "AHU", "FCU"]),
    ("LIGHT", None, ["灯", "LIGHT", "照明", "LAMP"]),
    ("SOCKET", None, ["插座", "SOCKET"]),
    ("SWITCH", None, ["开关", "SWITCH"]),
    ("PANEL", None, ["配电箱", "配电柜", "PANEL", "POWER_BOX"]),
    ("CABLE", None, ["桥架", "电缆", "CABLE", "TRAY", "线槽"]),
    ("FURNITURE", None, ["家具", "FURNITURE", "桌椅"]),
    ("SANITARY", None, ["洁具", "马桶", "洗手盆", "SANITARY", "WC"]),
]
# 细分类型识别（sub_category）
SUB_CATEGORY_RULES = [
    ("GATE_VALVE", ["闸阀", "Z41", "Z15"]),
    ("BUTTERFLY_VALVE", ["蝶阀", "D71", "D37"]),
    ("CHECK_VALVE", ["止回阀", "H41", "H44"]),
    ("GLOBE_VALVE", ["截止阀", "J41", "J11"]),
    ("FIRE_HYDRANT", ["消火栓", "室内消火栓"]),
    ("SPRINKLER", ["喷头", "喷淋头"]),
    ("WATER_PUMP", ["水泵", "给水泵", "排水泵"]),
    ("FAN", ["风机", "排风机", "送风机"]),
    ("AHU", ["空调机组", "AHU"]),
    ("FCU", ["风机盘管", "FCU"]),
    ("LED_PANEL", ["LED", "面板灯"]),
    ("FLUORESCENT", ["荧光灯", "日光灯"]),
    ("SOCKET_5HOLE", ["五孔", "5孔"]),
    ("SOCKET_3HOLE", ["三孔", "3孔"]),
]
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
def _norm(s) -> str:
    return (s or "").strip().upper()
def _make_entity_id(dxf_handle: str, dxf_type: str, layer: str, index: int) -> str:
    """生成确定性 entity_id：优先用 DXF handle，无则用内容哈希。"""
    seed = f"{dxf_handle}|{dxf_type}|{layer}|{index}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()
def _match_sub(haystack: str):
    """匹配细分类型。"""
    up = _norm(haystack)
    for sc, kws in SUB_CATEGORY_RULES:
        for kw in kws:
            if _norm(kw) in up:
                return sc
    return None
def _classify(layer: str, block_name: str, text: str) -> tuple:
    """返回 (category, sub_category)。
    优先级：图块名 > 文字 > 图层名。
    图块名最能反映构件本质（如 SWITCH_SINGLE 就是开关，即使它在 ELEC_SOCKET 图层上）。
    """
    if block_name:
        bn = _norm(block_name)
        for cat, _, kws in CATEGORY_RULES:
            for kw in kws:
                if _norm(kw) in bn:
                    return cat, _match_sub(f"{block_name} {text}")
    if text:
        tx = _norm(text)
        for cat, _, kws in CATEGORY_RULES:
            for kw in kws:
                if _norm(kw) in tx:
                    return cat, _match_sub(f"{block_name} {text}")
    if layer:
        ly = _norm(layer)
        for cat, _, kws in CATEGORY_RULES:
            for kw in kws:
                if _norm(kw) in ly:
                    return cat, _match_sub(f"{layer} {block_name} {text}")
    return "UNKNOWN", None
# ---------------------------------------------------------------- 几何提取
def _extract_geometry(e) -> dict:
    """将 DXF 实体几何标准化为 CIO geometry 结构。"""
    t = e.dxftype()
    try:
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            coords = [[_round(s.x), _round(s.y)], [_round(en.x), _round(en.y)]]
            return {"type": "LINE", "coordinates": coords,
                    "bbox": [_round(min(s.x, en.x)), _round(min(s.y, en.y)),
                             _round(max(s.x, en.x)), _round(max(s.y, en.y))]}
        if t == "CIRCLE":
            c = e.dxf.center
            r = e.dxf.radius
            return {"type": "CIRCLE", "coordinates": [[_round(c.x), _round(c.y)]],
                    "radius": _round(r),
                    "bbox": [_round(c.x - r), _round(c.y - r), _round(c.x + r), _round(c.y + r)]}
        if t == "ARC":
            c = e.dxf.center
            r = e.dxf.radius
            return {"type": "ARC", "coordinates": [[_round(c.x), _round(c.y)]],
                    "radius": _round(r),
                    "bbox": [_round(c.x - r), _round(c.y - r), _round(c.x + r), _round(c.y + r)]}
        if t == "LWPOLYLINE":
            pts = [[_round(p[0]), _round(p[1])] for p in e.get_points()]
            closed = bool(e.closed)
            xs = [p[0] for p in pts] or [0]
            ys = [p[1] for p in pts] or [0]
            return {"type": "POLYGON" if closed else "POLYLINE",
                    "coordinates": pts, "closed": closed,
                    "bbox": [_round(min(xs)), _round(min(ys)), _round(max(xs)), _round(max(ys))]}
        if t == "POLYLINE":
            pts = [[_round(v.dxf.location.x), _round(v.dxf.location.y)] for v in e.vertices]
            closed = bool(getattr(e, "is_closed", False))
            xs = [p[0] for p in pts] or [0]
            ys = [p[1] for p in pts] or [0]
            return {"type": "POLYGON" if closed else "POLYLINE",
                    "coordinates": pts, "closed": closed,
                    "bbox": [_round(min(xs)), _round(min(ys)), _round(max(xs)), _round(max(ys))]}
        if t in ("TEXT", "MTEXT"):
            ins = _dxf_attr(e, "insert", (0, 0, 0))
            return {"type": "TEXT", "coordinates": [[_round(ins[0]), _round(ins[1])]],
                    "bbox": [_round(ins[0]), _round(ins[1]), _round(ins[0]), _round(ins[1])]}
        if t == "INSERT":
            ins = _dxf_attr(e, "insert", (0, 0, 0))
            return {"type": "BLOCK_REF", "coordinates": [[_round(ins[0]), _round(ins[1])]],
                    "bbox": [_round(ins[0]), _round(ins[1]), _round(ins[0]), _round(ins[1])]}
        if t == "POINT":
            loc = e.dxf.location
            return {"type": "POINT", "coordinates": [[_round(loc.x), _round(loc.y)]],
                    "bbox": [_round(loc.x), _round(loc.y), _round(loc.x), _round(loc.y)]}
        if t == "DIMENSION":
            dp = _dxf_attr(e, "defpoint", (0, 0, 0))
            return {"type": "POINT", "coordinates": [[_round(dp[0]), _round(dp[1])]],
                    "bbox": [_round(dp[0]), _round(dp[1]), _round(dp[0]), _round(dp[1])]}
    except Exception:
        pass
    return {"type": "UNKNOWN", "coordinates": [], "bbox": [0, 0, 0, 0]}
# ---------------------------------------------------------------- 图纸类型识别
def _detect_drawing_type(texts: list, layers: list) -> str:
    blob = _norm(" ".join(texts) + " " + " ".join(layers))
    if any(k in blob for k in ["总平面", "SITE PLAN", "红线"]):
        return "SITE_PLAN"
    if any(k in blob for k in ["系统图", "SYSTEM DIAGRAM", "系统流程"]):
        return "SYSTEM_DIAGRAM"
    if any(k in blob for k in ["大样", "节点", "详图", "DETAIL"]):
        return "DETAIL"
    if any(k in blob for k in ["剖面", "SECTION"]):
        return "SECTION"
    if any(k in blob for k in ["立面", "ELEVATION"]):
        return "ELEVATION"
    if any(k in blob for k in ["平面", "PLAN", "FLOOR"]):
        return "FLOOR_PLAN"
    if any(k in blob for k in ["表", "SCHEDULE", "清单"]):
        return "SCHEDULE"
    return "UNKNOWN"
# ---------------------------------------------------------------- 专业识别（复用词典）
DISCIPLINE_MAP = {
    "建筑": "ARCH", "结构": "STR", "给排水": "PLUMB",
    "电气": "ELEC", "暖通": "HVAC", "总图": "SITE", "装饰": "DECO",
}
DISCIPLINE_KEYWORDS = {
    "ARCH": ["墙", "门", "窗", "轴线", "楼梯", "房间", "WALL", "DOOR", "WINDOW", "AXIS", "平面图"],
    "STR": ["柱", "梁", "板", "基础", "钢筋", "承台", "剪力墙", "COLUMN", "BEAM", "SLAB", "REBAR"],
    "PLUMB": ["给水", "排水", "雨水", "污水", "消火栓", "喷淋", "管道", "阀门", "地漏", "WATER", "DRAIN", "PIPE", "DN"],
    "ELEC": ["照明", "插座", "开关", "配电箱", "桥架", "电缆", "防雷", "接地", "LIGHT", "POWER", "SOCKET", "BV", "YJV"],
    "HVAC": ["风管", "风口", "空调", "风机", "冷媒", "冷凝水", "保温", "DUCT", "AHU", "FCU", "排烟", "新风"],
    "SITE": ["红线", "道路", "绿化", "坐标", "场地", "竖向", "REDLINE", "ROAD", "总平面"],
    "DECO": ["地面", "墙面", "天花", "踢脚", "造型", "饰面", "装修", "装饰", "CEILING"],
}
# 图层名前缀 → 专业映射（行业通用制图规范）
# 建筑 A、结构 S、给排水 P、暖通 M、电气 E、总图 L
LAYER_PREFIX_MAP = {
    "A": "ARCH", "AR": "ARCH", "ARCH": "ARCH",
    "S": "STR", "ST": "STR", "STR": "STR", "GS": "STR",
    "P": "PLUMB", "PL": "PLUMB", "W": "PLUMB", "PLUMB": "PLUMB",
    "M": "HVAC", "HV": "HVAC", "HVAC": "HVAC",
    "E": "ELEC", "EL": "ELEC", "ELEC": "ELEC",
    "L": "SITE", "LA": "SITE",
}
def _detect_by_layer_prefix(layers: list) -> tuple:
    """按图层名前缀判定专业（如 A-WALL → 建筑）。返回 (专业, 命中图层列表)。"""
    scores = defaultdict(list)
    for lname in layers:
        name = (lname or "").strip().upper()
        parts = re.split(r"[-_\s]", name)
        prefix = parts[0] if parts and parts[0] else name
        for cand in (prefix, prefix[:2], prefix[:1]):
            if cand and cand in LAYER_PREFIX_MAP:
                scores[LAYER_PREFIX_MAP[cand]].append(lname)
                break
    if not scores:
        return None, []
    best = max(scores.items(), key=lambda x: len(x[1]))
    return best[0], best[1]
def _detect_discipline(layers: list, blocks: list, texts: list) -> tuple:
    """返回 (discipline_code, confidence, evidence)。"""
    scores = defaultdict(float)
    evidence = defaultdict(lambda: defaultdict(list))
    for lname in layers:
        up = _norm(lname)
        for code, kws in DISCIPLINE_KEYWORDS.items():
            for kw in kws:
                if _norm(kw) in up:
                    scores[code] += 3
                    evidence[code]["layers"].append(lname)
    for bname in blocks:
        up = _norm(bname)
        for code, kws in DISCIPLINE_KEYWORDS.items():
            for kw in kws:
                if _norm(kw) in up:
                    scores[code] += 3
                    evidence[code]["blocks"].append(bname)
    for txt in texts:
        up = _norm(txt)
        for code, kws in DISCIPLINE_KEYWORDS.items():
            for kw in kws:
                if _norm(kw) in up:
                    scores[code] += 2
                    evidence[code]["texts"].append(txt[:40])
    # 图层前缀信号（权重 5，高于中文关键词的 3，因为前缀是制图规范强信号）
    prefix_code, prefix_layers = _detect_by_layer_prefix(layers)
    if prefix_code:
        scores[prefix_code] += 5 * len(prefix_layers)
        evidence[prefix_code]["layer_prefix"] = prefix_layers[:8]
    if not scores:
        return "UNKNOWN", 0.0, {}
    total = sum(scores.values())
    best = max(scores.items(), key=lambda x: x[1])
    conf = _round(best[1] / total * 100, 1) if total else 0.0
    # 多专业判定
    multi = len([s for s in scores.values() if s / total > 0.25]) > 1
    code = "MULTI" if multi and conf < 60 else best[0]
    ev = {k: {kk: list(dict.fromkeys(vv))[:8] for kk, vv in v.items()}
          for k, v in evidence.items() if k == best[0]}
    return code, conf, ev
# ---------------------------------------------------------------- 主转换器
class CioConverter:
    def __init__(self, dxf_path: str, project_id: str | None = None):
        self.path = os.path.abspath(dxf_path)
        self.doc = ezdxf.readfile(self.path)
        self.msp = self.doc.modelspace()
        self.project_id = project_id or f"PRJ-{os.path.splitext(os.path.basename(self.path))[0]}"
    def convert(self) -> dict:
        # ---- 收集基础信息 ----
        layers = [l.dxf.name for l in self.doc.layers]
        blocks = [b.name for b in self.doc.blocks if not b.name.startswith(("*", "_"))]
        texts = []
        for e in self.msp:
            t = e.dxftype()
            if t == "TEXT":
                texts.append(_dxf_attr(e, "text", ""))
            elif t == "MTEXT":
                texts.append(e.plain_text() if hasattr(e, "plain_text") else "")
        # ---- project_meta ----
        disc, conf, ev = _detect_discipline(layers, blocks, texts)
        dtype = _detect_drawing_type(texts, layers)
        project_meta = {
            "project_id": self.project_id,
            "discipline": disc,
            "drawing_type": dtype,
            "source_file": os.path.basename(self.path),
            "dxf_version": self.doc.dxfversion,
            "discipline_confidence": conf,
            "discipline_evidence": ev,
        }
        # ---- parsed_entities ----
        entities = []
        for idx, e in enumerate(self.msp):
            t = e.dxftype()
            layer = _dxf_attr(e, "layer", "0")
            block_name = _dxf_attr(e, "name", "") if t == "INSERT" else ""
            label = ""
            if t == "TEXT":
                label = _dxf_attr(e, "text", "")
            elif t == "MTEXT":
                label = e.plain_text() if hasattr(e, "plain_text") else ""
            cat, sub = _classify(layer, block_name, label)
            handle = _dxf_attr(e, "handle", "") or ""
            ent = {
                "entity_id": _make_entity_id(handle, t, layer, idx),
                "category": cat,
                "geometry": _extract_geometry(e),
                "attributes": {
                    "layer_name": layer,
                    "dxf_type": t,
                },
            }
            if sub:
                ent["sub_category"] = sub
            if block_name:
                ent["attributes"]["block_name"] = block_name
            if label:
                ent["attributes"]["label_text"] = label[:200]
            # 图块属性
            if t == "INSERT":
                try:
                    if e.attribs:
                        ent["attributes"]["raw_attribs"] = {
                            a.dxf.tag: a.dxf.text for a in e.attribs
                        }
                except Exception:
                    pass
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                ent["attributes"]["rotation"] = _round(_dxf_attr(e, "rotation", 0))
                ent["attributes"]["scale"] = [
                    _round(_dxf_attr(e, "xscale", 1)),
                    _round(_dxf_attr(e, "yscale", 1)),
                ]
            if t in ("TEXT", "MTEXT"):
                h = _dxf_attr(e, "height", _dxf_attr(e, "char_height", 0))
                ent["attributes"]["height"] = _round(h)
            entities.append(ent)
        # ---- 拓扑补全（空间邻近）----
        self._build_topology(entities, tolerance=3000.0)
        # ---- global_context ----
        from ezdxf import bbox as _bbox
        extents = None
        try:
            ext = _bbox.extents(self.msp, fast=True)
            if ext.has_data:
                extents = {
                    "min": [_round(ext.extmin.x), _round(ext.extmin.y)],
                    "max": [_round(ext.extmax.x), _round(ext.extmax.y)],
                    "width": _round(ext.size.x),
                    "height": _round(ext.size.y),
                }
        except Exception:
            pass
        layer_summary = Counter(_dxf_attr(e, "layer", "0") for e in self.msp)
        units_code = self.doc.header.get("$INSUNITS", 0) or 0
        units_map = {0: "unitless", 1: "inch", 2: "foot", 4: "mm", 5: "cm", 6: "m"}
        # 图层状态（供规则引擎使用）
        def _call_bool(obj, m, d=False):
            try:
                f = getattr(obj, m, None)
                return bool(f() if callable(f) else f)
            except Exception:
                return d
        layers_off = [l.dxf.name for l in self.doc.layers
                      if _call_bool(l, "is_off") and l.dxf.name != "Defpoints"]
        layers_frozen = [l.dxf.name for l in self.doc.layers if _call_bool(l, "is_frozen")]
        # 用户块定义清单（供"未使用块"规则）
        block_defs = [b.name for b in self.doc.blocks if not b.name.startswith(("*", "_"))]
        global_context = {
            "units": units_map.get(units_code, f"code_{units_code}"),
            "extents": extents,
            "layer_summary": dict(layer_summary),
            "layers_off": layers_off,
            "layers_frozen": layers_frozen,
            "block_definitions": block_defs,
            "scale_ratio": None,
            "elevation_base": None,
        }
        return {
            "schema_version": CIO_SCHEMA_VERSION,
            "project_meta": project_meta,
            "parsed_entities": entities,
            "global_context": global_context,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    def _build_topology(self, entities: list, tolerance: float = 3000.0):
        """基于空间邻近为实体补全 topology.connected_to。"""
        # 只对非文字实体做拓扑（文字作为标签不参与连接）
        candidates = [e for e in entities
                      if e["category"] not in ("TEXT", "DIMENSION", "AXIS", "UNKNOWN")
                      and e["geometry"].get("coordinates")]
        # 限制规模，避免 O(n²) 爆炸
        if len(candidates) > 400:
            candidates = candidates[:400]
        for i, a in enumerate(candidates):
            pa = a["geometry"]["coordinates"][0]
            conns = []
            for j, b in enumerate(candidates):
                if i == j:
                    continue
                pb = b["geometry"]["coordinates"][0]
                try:
                    d = math.dist(pa, pb)
                except Exception:
                    continue
                if d <= tolerance:
                    conns.append((b["entity_id"], d))
            if conns:
                conns.sort(key=lambda x: x[1])
                a["topology"] = {
                    "connected_to": [c[0] for c in conns[:10]],
                    "relation": "NEAR",
                }
# ---------------------------------------------------------------- Schema 校验
def validate_cio(cio: dict, schema_path: str) -> tuple:
    """返回 (是否通过, 错误列表)。"""
    try:
        import jsonschema
    except ImportError:
        return None, ["未安装 jsonschema，跳过校验（pip install jsonschema）"]
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in validator.iter_errors(cio):
        loc = " -> ".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{loc}: {err.message}")
    return len(errors) == 0, errors
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="DXF → CIO 标准数据契约转换")
    ap.add_argument("dxf", help="DXF 图纸路径")
    ap.add_argument("--out", default=None, help="CIO JSON 输出路径")
    ap.add_argument("--project-id", default=None, help="项目编号")
    ap.add_argument("--validate", action="store_true", help="执行 JSON Schema 校验")
    args = ap.parse_args()
    base = os.path.splitext(os.path.basename(args.dxf))[0]
    out = args.out or f"{base}_CIO.json"
    try:
        cio = CioConverter(args.dxf, args.project_id).convert()
    except Exception as e:
        print(f"[ERROR] 转换失败: {e}", file=sys.stderr)
        sys.exit(2)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cio, f, ensure_ascii=False, indent=2)
    print(f"[OK] CIO 数据: {out}")
    pm = cio["project_meta"]
    print("\n===== CIO 摘要 =====")
    print(f"项目编号  : {pm['project_id']}")
    print(f"专业      : {pm['discipline']}（置信度 {pm['discipline_confidence']}%）")
    print(f"图纸类型  : {pm['drawing_type']}")
    print(f"实体总数  : {len(cio['parsed_entities'])}")
    cats = Counter(e["category"] for e in cio["parsed_entities"])
    print(f"实体分类  : {dict(cats.most_common(8))}")
    with_topo = sum(1 for e in cio["parsed_entities"] if "topology" in e)
    print(f"含拓扑    : {with_topo}")
    if args.validate:
        schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "schemas", "cio_schema.json")
        ok, errs = validate_cio(cio, schema_path)
        if ok is None:
            print(f"[WARN] {errs[0]}")
        elif ok:
            print("[OK] Schema 校验通过 ✅")
        else:
            print(f"[FAIL] Schema 校验失败，共 {len(errs)} 处：")
            for e in errs[:10]:
                print("   -", e)
            sys.exit(3)
if __name__ == "__main__":
    main()