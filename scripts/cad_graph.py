#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工程知识图谱 —— 基于 CIO 数据构建 NetworkX 图谱，支持多跳推理问答
用法:
    python cad_graph.py build <cio.json> [--out graph.json]
    python cad_graph.py query <graph.json> --ask "问题"
    python cad_graph.py stats <graph.json>
功能:
    1. 从 CIO 数据构建工程知识图谱（节点 + 边）
    2. 图遍历多跳推理（如"与消防水箱相连的阀门有哪些"）
    3. 关键词检索（精确匹配设备型号、图号）
    4. 图谱统计与可视化导出
设计说明:
    采用「实体-关系-实体」三元组结构。初期用 NetworkX + JSON 持久化跑通闭环，
    数据量达到百万级时可平滑迁移至 Neo4j（节点/边结构一致）。
依赖: networkx
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
try:
    import networkx as nx
except ImportError:
    print("[ERROR] 缺少依赖 networkx，请执行: pip install networkx", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------- 图谱构建
def build_graph(cio: dict) -> nx.DiGraph:
    """从 CIO 数据构建有向图。"""
    G = nx.DiGraph()
    pm = cio.get("project_meta", {})
    gc = cio.get("global_context", {})
    # ---- 顶层节点：项目 ----
    project_id = pm.get("project_id", "UNKNOWN")
    G.add_node(project_id, node_type="Project",
               discipline=pm.get("discipline", ""),
               drawing_type=pm.get("drawing_type", ""),
               source_file=pm.get("source_file", ""))
    # ---- 图纸节点 ----
    drawing_id = f"DRAWING::{pm.get('source_file', 'unknown')}"
    G.add_node(drawing_id, node_type="Drawing",
               name=pm.get("source_file", ""),
               discipline=pm.get("discipline", ""))
    G.add_edge(project_id, drawing_id, relation="CONTAINS")
    # ---- 图层节点 ----
    layer_summary = gc.get("layer_summary", {})
    for lname, cnt in layer_summary.items():
        lid = f"LAYER::{lname}"
        G.add_node(lid, node_type="Layer", name=lname, entity_count=cnt)
        G.add_edge(drawing_id, lid, relation="HAS_LAYER")
    # ---- 实体节点 ----
    for e in cio.get("parsed_entities", []):
        eid = e["entity_id"]
        attrs = e.get("attributes", {})
        cat = e.get("category", "UNKNOWN")
        label = attrs.get("label_text", "") or attrs.get("block_name", "") or cat
        G.add_node(eid, node_type="Component", category=cat,
                   sub_category=e.get("sub_category", ""),
                   layer=attrs.get("layer_name", ""),
                   block_name=attrs.get("block_name", ""),
                   label=label[:80],
                   dxf_type=attrs.get("dxf_type", ""))
        # 实体 -> 图层
        layer = attrs.get("layer_name")
        if layer:
            G.add_edge(eid, f"LAYER::{layer}", relation="LOCATED_IN")
        # 实体 -> 图纸
        G.add_edge(eid, drawing_id, relation="BELONGS_TO")
        # 图块属性作为子节点
        raw = attrs.get("raw_attribs") or {}
        for k, v in raw.items():
            if v and str(v).strip():
                aid = f"ATTR::{eid}::{k}"
                G.add_node(aid, node_type="Attribute", key=k, value=str(v))
                G.add_edge(eid, aid, relation="HAS_ATTRIBUTE")
    # ---- 拓扑边 ----
    for e in cio.get("parsed_entities", []):
        topo = e.get("topology")
        if not topo:
            continue
        for target in topo.get("connected_to", []):
            if G.has_node(target):
                G.add_edge(e["entity_id"], target,
                           relation=topo.get("relation", "NEAR"))
    return G
# ---------------------------------------------------------------- 图谱持久化
def save_graph(G: nx.DiGraph, path: str):
    data = nx.node_link_data(G)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
def load_graph(path: str) -> nx.DiGraph:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return nx.node_link_graph(data, directed=True)
# ---------------------------------------------------------------- 中英同义词映射
# 用户用中文提问，但图块名常为英文（LIGHT_LED_PANEL），需要建立映射桥接
SYNONYM_MAP = {
    "灯具": ["LIGHT", "LAMP", "灯", "LED", "FLUORESCENT"],
    "灯": ["LIGHT", "LAMP", "LED"],
    "照明": ["LIGHT", "LAMP"],
    "插座": ["SOCKET", "插座"],
    "开关": ["SWITCH", "开关"],
    "配电箱": ["POWER_BOX", "配电箱"],
    "配电柜": ["POWER_BOX", "配电柜"],
    "桥架": ["CABLE", "TRAY", "桥架"],
    "电缆": ["CABLE", "电缆"],
    "风管": ["DUCT", "风管"],
    "风口": ["DUCT", "风口", "AIR"],
    "阀门": ["VALVE", "阀"],
    "管道": ["PIPE", "管道"],
    "水管": ["PIPE", "WATER", "管道"],
    "给水": ["WATER", "给水", "PIPE"],
    "排水": ["DRAIN", "排水", "PIPE"],
    "消火栓": ["FIRE_HYDRANT", "消火栓"],
    "喷头": ["SPRINKLER", "喷头", "喷淋"],
    "水泵": ["WATER_PUMP", "水泵", "PUMP"],
    "风机": ["FAN", "风机"],
    "空调": ["AHU", "FCU", "空调"],
    "门": ["DOOR", "门"],
    "窗": ["WINDOW", "窗"],
    "墙": ["WALL", "墙"],
    "柱": ["COLUMN", "柱"],
    "梁": ["BEAM", "梁"],
    "板": ["SLAB", "板"],
    "楼梯": ["STAIR", "楼梯"],
    "轴线": ["AXIS", "轴线"],
    "家具": ["FURNITURE", "家具"],
    "洁具": ["SANITARY", "洁具"],
}
def _token_match(needle: str, haystack: str) -> bool:
    """按词边界匹配，避免 LIGHT_LED_PANEL 里的 PANEL 被误判为配电箱。
    规则：needle 作为独立 token 出现（被 _ - 空格 或字符串边界包围）才算命中。
    """
    n = needle.upper().strip()
    h = haystack.upper()
    if not n:
        return False
    # 纯中文关键词：直接子串匹配（中文无词边界概念）
    if re.search(r"[\u4e00-\u9fff]", n):
        return n in h
    # 英文/数字关键词：要求词边界
    pattern = r"(?<![A-Z0-9])" + re.escape(n) + r"(?![A-Z0-9])"
    return re.search(pattern, h) is not None
def _expand_keywords(kw: str) -> list:
    """将用户关键词扩展为同义词列表（含原词）。
    匹配策略：用户词与词典键做「精确相等」或「词典键完整包含在用户词中」，
    避免双向包含导致的语义污染（如"配电箱"误匹配到"灯"）。
    """
    k = kw.strip()
    out = [k]
    for zh, syns in SYNONYM_MAP.items():
        # 精确相等，或词典键是用户词的一部分（如"五孔插座"包含"插座"）
        if k == zh or zh in k:
            out.extend(syns)
    # 去重保序
    seen = set()
    uniq = []
    for x in out:
        if x.upper() not in seen:
            seen.add(x.upper())
            uniq.append(x)
    return uniq
# ---------------------------------------------------------------- 查询引擎
class GraphQuery:
    def __init__(self, G: nx.DiGraph):
        self.G = G
    def _find_nodes(self, keyword: str, node_types=None) -> list:
        """按关键词查找节点（支持中英同义词扩展）。
        匹配策略：按字段优先级分层匹配，避免 label 里的长文本污染匹配结果。
        优先级：block_name / category / sub_category / name（结构化字段）
                > label / value（自由文本字段）
        """
        kws = [k.upper() for k in _expand_keywords(keyword)]
        # 分层：结构化字段（精确）与自由文本字段（宽松）
        struct_keys = ("block_name", "category", "sub_category", "name", "key")
        free_keys = ("label", "value")
        strict_hits, loose_hits = [], []
        for n, d in self.G.nodes(data=True):
            if node_types and d.get("node_type") not in node_types:
                continue
            struct_blob = " ".join(str(d.get(k, "")) for k in struct_keys)
            if any(_token_match(k, struct_blob) for k in kws):
                strict_hits.append(n)
                continue
            free_blob = " ".join(str(d.get(k, "")) for k in free_keys)
            if any(_token_match(k, free_blob) for k in kws):
                loose_hits.append(n)
        # 结构化命中优先；若结构化有结果，不再混入自由文本命中
        return strict_hits if strict_hits else loose_hits
    def query(self, question: str) -> dict:
        """自然语言问题 → 图遍历推理。"""
        q = question.strip()
        # 意图1：查找与 X 相连的 Y
        m = re.search(r"与(.+?)(?:相连|连接|关联)的(.+?)(?:有哪些|是什么|有哪几个|列表)?$", q)
        if m:
            anchor_kw, target_kw = m.group(1).strip(), m.group(2).strip()
            return self._query_connected(anchor_kw, target_kw)
        # 意图2：查找某类构件
        m = re.search(r"(.+?)(?:有哪些|是什么|有多少|列表|清单)", q)
        if m:
            kw = m.group(1).strip()
            return self._query_find(kw)
        # 意图3：统计某类构件数量
        m = re.search(r"(.+?)的数量|有多少(.+)", q)
        if m:
            kw = (m.group(1) or m.group(2)).strip()
            return self._query_count(kw)
        # 兜底：关键词检索
        return self._query_find(q)
    def _query_connected(self, anchor_kw: str, target_kw: str) -> dict:
        anchors = self._find_nodes(anchor_kw)
        if not anchors:
            return {"answer": f"未找到与「{anchor_kw}」相关的构件。",
                    "nodes": [], "paths": []}
        results = []
        for a in anchors:
            # 多跳遍历（最多 3 跳）
            for depth in range(1, 4):
                try:
                    paths = nx.single_source_shortest_path_length(self.G, a, cutoff=depth)
                except Exception:
                    continue
                for n, dist in paths.items():
                    if dist == 0:
                        continue
                    d = self.G.nodes[n]
                    blob = " ".join(str(d.get(k, "")) for k in
                                    ("name", "label", "category", "sub_category", "block_name")).upper()
                    tkws = [k.upper() for k in _expand_keywords(target_kw)]
                    # 结构化字段优先匹配
                    sblob = " ".join(str(d.get(k, "")) for k in
                                     ("block_name", "category", "sub_category", "name"))
                    if any(_token_match(k, sblob) for k in tkws):
                        results.append({
                            "anchor": self.G.nodes[a].get("label") or anchor_kw,
                            "anchor_id": a,
                            "target": d.get("label") or d.get("name") or n,
                            "target_id": n,
                            "hops": dist,
                            "relation_path": self._describe_path(a, n),
                        })
        if not results:
            return {"answer": f"未找到与「{anchor_kw}」相连的「{target_kw}」。",
                    "nodes": [], "paths": []}
        # 去重
        seen = set()
        uniq = []
        for r in results:
            key = (r["anchor_id"], r["target_id"])
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        uniq.sort(key=lambda r: r["hops"])
        lines = [f"找到 {len(uniq)} 个与「{anchor_kw}」相连的「{target_kw}」："]
        for r in uniq[:20]:
            lines.append(f"  · {r['target']}（{r['hops']} 跳，路径：{r['relation_path']}）")
        return {"answer": "\n".join(lines), "nodes": uniq, "paths": [r["relation_path"] for r in uniq]}
    def _describe_path(self, src, dst) -> str:
        try:
            path = nx.shortest_path(self.G, src, dst)
            rels = []
            for i in range(len(path) - 1):
                rel = self.G.edges[path[i], path[i + 1]].get("relation", "?")
                rels.append(rel)
            return " → ".join(rels)
        except Exception:
            return "?"
    def _query_find(self, kw: str) -> dict:
        hits = self._find_nodes(kw)
        if not hits:
            return {"answer": f"未找到与「{kw}」相关的节点。", "nodes": [], "paths": []}
        lines = [f"找到 {len(hits)} 个与「{kw}」相关的节点："]
        for n in hits[:30]:
            d = self.G.nodes[n]
            label = d.get("label") or d.get("name") or n
            lines.append(f"  · [{d.get('node_type')}] {label}"
                         f"{'（' + d.get('category', '') + '）' if d.get('category') else ''}")
        return {"answer": "\n".join(lines), "nodes": hits, "paths": []}
    def _query_count(self, kw: str) -> dict:
        hits = self._find_nodes(kw, node_types=["Component"])
        by_cat = Counter(self.G.nodes[n].get("category", "UNKNOWN") for n in hits)
        lines = [f"「{kw}」相关构件共 {len(hits)} 个："]
        for cat, cnt in by_cat.most_common():
            lines.append(f"  · {cat}: {cnt}")
        return {"answer": "\n".join(lines), "nodes": hits, "paths": []}
# ---------------------------------------------------------------- 统计
def graph_stats(G: nx.DiGraph) -> dict:
    node_types = Counter(d.get("node_type", "?") for _, d in G.nodes(data=True))
    relations = Counter(d.get("relation", "?") for _, _, d in G.edges(data=True))
    categories = Counter(d.get("category", "?") for _, d in G.nodes(data=True)
                         if d.get("node_type") == "Component")
    return {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "node_types": dict(node_types),
        "relations": dict(relations),
        "component_categories": dict(categories.most_common(15)),
    }
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="工程知识图谱构建与查询")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="从 CIO 构建图谱")
    p_build.add_argument("cio", help="CIO JSON 路径")
    p_build.add_argument("--out", default=None, help="图谱输出路径")
    p_query = sub.add_parser("query", help="查询图谱")
    p_query.add_argument("graph", help="图谱 JSON 路径")
    p_query.add_argument("--ask", required=True, help="自然语言问题")
    p_stats = sub.add_parser("stats", help="图谱统计")
    p_stats.add_argument("graph", help="图谱 JSON 路径")
    args = ap.parse_args()
    if args.cmd == "build":
        with open(args.cio, encoding="utf-8") as f:
            cio = json.load(f)
        G = build_graph(cio)
        out = args.out or os.path.splitext(args.cio)[0] + "_图谱.json"
        save_graph(G, out)
        print(f"[OK] 知识图谱: {out}")
        st = graph_stats(G)
        print("\n===== 图谱统计 =====")
        print(f"节点总数  : {st['total_nodes']}")
        print(f"边总数    : {st['total_edges']}")
        print(f"节点类型  : {st['node_types']}")
        print(f"关系类型  : {st['relations']}")
        print(f"构件分类  : {st['component_categories']}")
    elif args.cmd == "query":
        G = load_graph(args.graph)
        q = GraphQuery(G)
        res = q.query(args.ask)
        print("\n" + res["answer"])
    elif args.cmd == "stats":
        G = load_graph(args.graph)
        st = graph_stats(G)
        print(json.dumps(st, ensure_ascii=False, indent=2))
if __name__ == "__main__":
    main()