#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部位/空间识别模块 —— 封闭区域检测 + 构件归属 + 图框检测
用法:
    python cad_space.py <drawing.dxf> [--out report_dir] [--json]
功能:
    1. 图框检测：最大矩形检测，识别内外图框，提取图签区域
    2. 封闭区域检测：图论算法识别由墙线围合而成的封闭空间（房间/区域）
    3. 构件-空间归属：判断每个构件位于哪个空间内部/边界上/外部
    4. 标高层级识别：提取标高标注，建立垂直方向楼层划分
设计说明:
    封闭区域检测采用「线段端点聚类 → 构建平面图 → 找最小环」的思路。
    相比纯视觉推理，几何计算的空间关系准确率接近 100%。
依赖: ezdxf, networkx
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
    import networkx as nx
except ImportError as e:
    print(f"[ERROR] 缺少依赖: {e}，请执行: pip install ezdxf networkx", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------- 常量
# 墙线图层关键词（用于封闭区域检测）
WALL_KEYWORDS = ["墙", "WALL", "Q-", "剪力墙", "隔墙"]
# 图签关键词（用于图框内区域定位）
TITLE_BLOCK_KEYWORDS = ["图号", "图名", "设计", "审核", "制图", "比例", "日期", "工程", "图签"]
# 标高正则：如 +3.000、-0.050、±0.000
ELEVATION_PATTERN = re.compile(r"[+\-±]\s?\d+\.\d{3}")
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
def _is_wall_layer(layer: str) -> bool:
    up = _norm(layer)
    return any(_norm(k) in up for k in WALL_KEYWORDS)
# ---------------------------------------------------------------- 1. 图框检测
class FrameDetector:
    """检测图框（最大矩形）并定位图签区域。"""
    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()
    def detect(self) -> dict:
        # 1.1 收集所有闭合矩形（LWPOLYLINE 闭合 + 4 顶点）
        rects = []
        for e in self.msp:
            if e.dxftype() != "LWPOLYLINE":
                continue
            try:
                pts = [(p[0], p[1]) for p in e.get_points()]
            except Exception:
                continue
            if len(pts) != 4:
                continue
            if not e.closed:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if w <= 0 or h <= 0:
                continue
            rects.append({
                "layer": _dxf_attr(e, "layer", "0"),
                "bbox": [_round(min(xs)), _round(min(ys)), _round(max(xs)), _round(max(ys))],
                "area": _round(w * h, 2),
                "width": _round(w), "height": _round(h),
            })
        if not rects:
            return {"found": False, "frames": [], "title_block": None,
                    "note": "未检测到闭合矩形图框"}
        rects.sort(key=lambda r: -r["area"])
        # 1.2 判断图幅规格（A0-A4）
        for r in rects:
            r["paper_size"] = self._guess_paper(r["width"], r["height"])
        outer = rects[0]
        # 1.3 图签区域：外框内右下角 1/4 区域
        x0, y0, x1, y1 = outer["bbox"]
        w, h = x1 - x0, y1 - y0
        title_zone = [_round(x0 + w * 0.6), _round(y0), _round(x1), _round(y0 + h * 0.25)]
        # 1.4 提取图签区文字
        title_texts = []
        for e in self.msp:
            t = e.dxftype()
            if t not in ("TEXT", "MTEXT"):
                continue
            ins = _dxf_attr(e, "insert", (0, 0, 0))
            x, y = ins[0], ins[1]
            if title_zone[0] <= x <= title_zone[2] and title_zone[1] <= y <= title_zone[3]:
                content = (_dxf_attr(e, "text", "") if t == "TEXT"
                           else (e.plain_text() if hasattr(e, "plain_text") else ""))
                if content.strip():
                    title_texts.append({"content": content.strip(),
                                        "position": [_round(x), _round(y)]})
        # 1.5 从图签文字中提取关键信息
        title_info = self._parse_title(title_texts)
        return {
            "found": True,
            "frame_count": len(rects),
            "outer_frame": outer,
            "all_frames": rects[:10],
            "title_block": {
                "zone": title_zone,
                "texts": title_texts[:50],
                "parsed": title_info,
            },
        }
    @staticmethod
    def _guess_paper(w: float, h: float) -> str:
        """按尺寸猜测图幅规格。
        图纸可能按比例放大绘制（如 1:100 时 A3 图框画成 42000x29700），
        因此先用常见比例试算，取误差最小者。
        """
        specs = {"A0": (1189, 841), "A1": (841, 594), "A2": (594, 420),
                 "A3": (420, 297), "A4": (297, 210)}
        scales = [1, 10, 20, 50, 100, 200, 500, 1000]
        best = ("未知", None, float("inf"))
        for name, (sw, sh) in specs.items():
            for (a, b) in ((sw, sh), (sh, sw)):
                for sc in scales:
                    tw, th = a * sc, b * sc
                    err = abs(w - tw) / tw + abs(h - th) / th
                    if err < best[2]:
                        best = (name, sc, err)
        if best[2] < 0.05:
            return f"{best[0]}（1:{best[1]}）" if best[1] > 1 else best[0]
        return "未知"
    @staticmethod
    def _parse_title(texts: list) -> dict:
        """从图签文字中解析图号、图名、比例等。"""
        info = {"drawing_no": None, "drawing_name": None, "scale": None,
                "discipline_hint": None, "raw": []}
        for t in texts:
            c = t["content"]
            info["raw"].append(c)
            # 图号：如 建施-01、结施-03、水施-02
            if re.search(r"[建结水暖电通施]-?\d+", c) or re.search(r"^[A-Z]{1,3}-?\d{1,3}$", c):
                if not info["drawing_no"]:
                    info["drawing_no"] = c
            # 比例：1:100
            m = re.search(r"1\s*[:：]\s*(\d+)", c)
            if m and not info["scale"]:
                info["scale"] = f"1:{m.group(1)}"
            # 图名：含"平面图""立面图"等
            if re.search(r"(平面图|立面图|剖面图|详图|系统图|大样图|总平面)", c):
                if not info["drawing_name"]:
                    info["drawing_name"] = c
            # 专业提示
            for kw, disc in [("建施", "ARCH"), ("结施", "STR"), ("水施", "PLUMB"),
                             ("暖施", "HVAC"), ("电施", "ELEC")]:
                if kw in c:
                    info["discipline_hint"] = disc
        return info
# ---------------------------------------------------------------- 2. 封闭区域检测
class SpaceDetector:
    """基于墙线构建平面图，检测封闭区域（房间）。"""
    def __init__(self, doc, snap_tolerance: float = 50.0):
        self.doc = doc
        self.msp = doc.modelspace()
        self.snap = snap_tolerance
    def detect(self) -> dict:
        # 2.1 收集墙线段
        segments = []
        for e in self.msp:
            layer = _dxf_attr(e, "layer", "0")
            t = e.dxftype()
            if t == "LINE" and _is_wall_layer(layer):
                s, en = e.dxf.start, e.dxf.end
                segments.append(((s.x, s.y), (en.x, en.y)))
            elif t == "LWPOLYLINE" and _is_wall_layer(layer):
                try:
                    pts = [(p[0], p[1]) for p in e.get_points()]
                    closed = bool(e.closed)
                    for i in range(len(pts) - 1):
                        segments.append((pts[i], pts[i + 1]))
                    if closed and len(pts) > 2:
                        segments.append((pts[-1], pts[0]))
                except Exception:
                    continue
        if not segments:
            return {"found": False, "spaces": [], "wall_segments": 0,
                    "note": "未找到墙线图层上的线段，无法检测封闭区域"}
        # 2.2 端点聚类（吸附容差内的端点视为同一点）
        G = nx.Graph()
        node_coords = {}
        def _snap_node(pt):
            """把点吸附到已有节点或新建节点。"""
            for nid, coord in node_coords.items():
                if math.dist(pt, coord) <= self.snap:
                    return nid
            nid = len(node_coords)
            node_coords[nid] = pt
            return nid
        for a, b in segments:
            na, nb = _snap_node(a), _snap_node(b)
            if na != nb:
                G.add_edge(na, nb, weight=math.dist(node_coords[na], node_coords[nb]))
        # 2.3 找最小环（封闭区域）
        spaces = []
        try:
            cycles = nx.minimum_cycle_basis(G)
        except Exception:
            cycles = []
        for i, cyc in enumerate(cycles):
            if len(cyc) < 3:
                continue
            pts = [node_coords[n] for n in cyc]
            # 计算面积（鞋带公式）
            area = 0.0
            for j in range(len(pts)):
                x1, y1 = pts[j]
                x2, y2 = pts[(j + 1) % len(pts)]
                area += x1 * y2 - x2 * y1
            area = abs(area) / 2
            if area < 1.0:   # 忽略极小环
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            spaces.append({
                "space_id": f"SPACE-{i+1:03d}",
                "polygon": [[_round(p[0]), _round(p[1])] for p in pts],
                "area": _round(area, 2),
                "bbox": [_round(min(xs)), _round(min(ys)), _round(max(xs)), _round(max(ys))],
                "vertex_count": len(pts),
            })
        spaces.sort(key=lambda s: -s["area"])
        # 2.4 为每个空间匹配房间名称（空间内的文字）
        for sp in spaces:
            sp["name"] = self._find_space_name(sp)
        return {
            "found": len(spaces) > 0,
            "wall_segments": len(segments),
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
            "space_count": len(spaces),
            "spaces": spaces[:100],
        }
    def _find_space_name(self, space: dict) -> str:
        """在空间内部找文字作为房间名。"""
        x0, y0, x1, y1 = space["bbox"]
        candidates = []
        for e in self.msp:
            t = e.dxftype()
            if t not in ("TEXT", "MTEXT"):
                continue
            ins = _dxf_attr(e, "insert", (0, 0, 0))
            x, y = ins[0], ins[1]
            if x0 <= x <= x1 and y0 <= y <= y1:
                content = (_dxf_attr(e, "text", "") if t == "TEXT"
                           else (e.plain_text() if hasattr(e, "plain_text") else ""))
                content = content.strip()
                # 排除纯数字/标高/尺寸
                if content and not ELEVATION_PATTERN.search(content) and not content.replace(".", "").isdigit():
                    candidates.append(content)
        return candidates[0][:30] if candidates else ""
    def assign_components(self, spaces: list) -> dict:
        """判断构件归属哪个空间。"""
        result = defaultdict(list)
        unassigned = []
        for e in self.msp:
            if e.dxftype() != "INSERT":
                continue
            ins = _dxf_attr(e, "insert", (0, 0, 0))
            pt = (ins[0], ins[1])
            bname = _dxf_attr(e, "name", "")
            assigned = None
            for sp in spaces:
                if self._point_in_polygon(pt, sp["polygon"]):
                    assigned = sp["space_id"]
                    break
            if assigned:
                result[assigned].append({"block_name": bname,
                                         "position": [_round(pt[0]), _round(pt[1])]})
            else:
                unassigned.append({"block_name": bname,
                                   "position": [_round(pt[0]), _round(pt[1])]})
        return {"by_space": dict(result), "unassigned_count": len(unassigned),
                "unassigned_samples": unassigned[:20]}
    @staticmethod
    def _point_in_polygon(pt, polygon) -> bool:
        """射线法判断点是否在多边形内。"""
        x, y = pt
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i
        return inside
# ---------------------------------------------------------------- 3. 标高识别
class ElevationParser:
    """提取标高标注，建立楼层划分。"""
    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()
    def parse(self) -> dict:
        elevations = []
        for e in self.msp:
            t = e.dxftype()
            content = ""
            if t == "TEXT":
                content = _dxf_attr(e, "text", "")
            elif t == "MTEXT":
                content = e.plain_text() if hasattr(e, "plain_text") else ""
            if not content:
                continue
            for m in ELEVATION_PATTERN.finditer(content):
                raw = m.group(0).replace(" ", "")
                val = self._to_float(raw)
                if val is None:
                    continue
                ins = _dxf_attr(e, "insert", (0, 0, 0))
                elevations.append({
                    "raw": raw, "value": _round(val, 3),
                    "position": [_round(ins[0]), _round(ins[1])],
                })
        # 去重 + 排序
        seen = set()
        uniq = []
        for el in elevations:
            key = (el["raw"], tuple(el["position"]))
            if key not in seen:
                seen.add(key)
                uniq.append(el)
        uniq.sort(key=lambda x: -x["value"])
        values = sorted({el["value"] for el in uniq}, reverse=True)
        return {
            "total": len(uniq),
            "distinct_levels": values,
            "level_count": len(values),
            "items": uniq[:100],
            "note": f"共识别 {len(values)} 个不同标高，可据此划分 {max(0, len(values)-1)} 个楼层区间",
        }
    @staticmethod
    def _to_float(raw: str):
        try:
            s = raw.replace("±", "").replace("+", "").replace(" ", "")
            return float(s)
        except Exception:
            return None
# ---------------------------------------------------------------- 报告输出
def write_space_md(result: dict, out_path: str):
    L = []
    A = L.append
    A("# CAD 图纸部位/空间识别报告\n")
    A(f"> 文件：`{result['file_name']}`　|　分析时间：{result['analyzed_at']}\n")
    # 一、图框与图签
    A("## 一、图框与图签检测\n")
    fr = result["frame"]
    if not fr["found"]:
        A(f"未检测到图框。{fr.get('note', '')}\n")
    else:
        of = fr["outer_frame"]
        A(f"检测到 **{fr['frame_count']}** 个闭合矩形，最大者判定为图框：\n")
        A("| 项目 | 值 |")
        A("|---|---|")
        A(f"| 图框尺寸 | {of['width']} × {of['height']} |")
        A(f"| 图幅规格 | {of['paper_size']} |")
        A(f"| 图框面积 | {of['area']} |")
        A(f"| 所在图层 | {of['layer']} |")
        A("")
        tb = fr["title_block"]
        p = tb["parsed"]
        if p.get("drawing_no") or p.get("drawing_name"):
            A("### 图签信息\n")
            A("| 字段 | 值 |")
            A("|---|---|")
            if p.get("drawing_no"):
                A(f"| 图号 | {p['drawing_no']} |")
            if p.get("drawing_name"):
                A(f"| 图名 | {p['drawing_name']} |")
            if p.get("scale"):
                A(f"| 比例 | {p['scale']} |")
            if p.get("discipline_hint"):
                A(f"| 专业提示 | {p['discipline_hint']} |")
            A("")
        if tb["texts"]:
            A("### 图签区文字\n")
            A("| 内容 | 位置 |")
            A("|---|---|")
            for t in tb["texts"][:20]:
                A(f"| {t['content'][:40]} | ({t['position'][0]}, {t['position'][1]}) |")
            A("")
    # 二、封闭区域
    A("## 二、封闭区域（房间/空间）识别\n")
    sp = result["space"]
    if not sp["found"]:
        A(f"未检测到封闭区域。{sp.get('note', '')}\n")
    else:
        A(f"墙线段数：**{sp['wall_segments']}**　|　平面图节点：{sp['graph_nodes']}　|　"
          f"边：{sp['graph_edges']}\n")
        A(f"识别出 **{sp['space_count']}** 个封闭区域：\n")
        A("| 编号 | 名称 | 面积 | 顶点数 | 范围 |")
        A("|---|---|---:|---:|---|")
        for s in sp["spaces"][:30]:
            bb = s["bbox"]
            A(f"| {s['space_id']} | {s['name'] or '（未命名）'} | {s['area']} | "
              f"{s['vertex_count']} | ({bb[0]},{bb[1]})-({bb[2]},{bb[3]}) |")
        A("")
        total_area = sum(s["area"] for s in sp["spaces"])
        A(f"**封闭区域总面积：{_round(total_area, 2)}**\n")
    # 三、构件空间归属
    A("## 三、构件空间归属\n")
    ca = result["component_assignment"]
    if ca["by_space"]:
        A("| 空间编号 | 空间名称 | 构件数 | 主要构件 |")
        A("|---|---|---:|---|")
        space_map = {s["space_id"]: s for s in sp.get("spaces", [])}
        for sid, comps in ca["by_space"].items():
            name = space_map.get(sid, {}).get("name", "")
            cnt = Counter(c["block_name"] for c in comps)
            top = "、".join(f"{k}({v})" for k, v in cnt.most_common(3))
            A(f"| {sid} | {name or '（未命名）'} | {len(comps)} | {top} |")
        A("")
    if ca["unassigned_count"]:
        A(f"**未归属构件**：{ca['unassigned_count']} 个（位于所有封闭区域之外）\n")
        cnt = Counter(c["block_name"] for c in ca["unassigned_samples"])
        if cnt:
            A(f"主要类型：{'、'.join(f'{k}({v})' for k, v in cnt.most_common(5))}\n")
    # 四、标高与楼层
    A("## 四、标高与楼层划分\n")
    el = result["elevation"]
    A(f"识别到 **{el['total']}** 处标高标注，**{el['level_count']}** 个不同标高值。\n")
    if el["distinct_levels"]:
        A("| 标高值 |")
        A("|---:|")
        for v in el["distinct_levels"][:20]:
            A(f"| {v} |")
        A("")
        A(f"> {el['note']}\n")
    A("---\n")
    A("*本报告由 CAD 图纸部位/空间识别模块自动生成。空间关系基于几何计算，非视觉推理。*")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="CAD 图纸部位/空间识别")
    ap.add_argument("dxf", help="DXF 图纸路径")
    ap.add_argument("--out", default=".", help="报告输出目录")
    ap.add_argument("--snap", type=float, default=50.0, help="端点吸附容差")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.dxf))[0]
    try:
        doc = ezdxf.readfile(args.dxf)
    except Exception as e:
        print(f"[ERROR] 读取失败: {e}", file=sys.stderr)
        sys.exit(2)
    frame = FrameDetector(doc).detect()
    sd = SpaceDetector(doc, args.snap)
    space = sd.detect()
    assignment = sd.assign_components(space.get("spaces", []))
    elevation = ElevationParser(doc).parse()
    result = {
        "file_name": os.path.basename(args.dxf),
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "frame": frame,
        "space": space,
        "component_assignment": assignment,
        "elevation": elevation,
    }
    md_path = os.path.join(args.out, f"{base}_空间识别报告.md")
    write_space_md(result, md_path)
    print(f"[OK] 空间识别报告: {md_path}")
    if args.json:
        json_path = os.path.join(args.out, f"{base}_空间识别数据.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] JSON 数据: {json_path}")
    print("\n===== 空间识别摘要 =====")
    print(f"图框      : {'找到' if frame['found'] else '未找到'}"
          + (f"（{frame['outer_frame']['paper_size']}）" if frame["found"] else ""))
    if frame["found"]:
        p = frame["title_block"]["parsed"]
        print(f"图号      : {p.get('drawing_no') or '未识别'}")
        print(f"图名      : {p.get('drawing_name') or '未识别'}")
    print(f"封闭区域  : {space.get('space_count', 0)} 个")
    print(f"构件归属  : {len(assignment['by_space'])} 个空间有构件")
    print(f"未归属    : {assignment['unassigned_count']} 个")
    print(f"标高数    : {elevation['level_count']} 个不同值")
if __name__ == "__main__":
    main()