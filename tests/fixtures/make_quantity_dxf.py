#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成工程量金标准测试用 DXF（运行时生成，不纳入版本库）

图纸内容（坐标单位 mm，图框按 1:100 画成 42000×29700）：
- A-FRAME      图框闭合矩形（1:100，A3）
- 图签          建施-01 / 一层平面图 / 1:100
- A-WALL       外墙闭合多段线 8000×5000 + 一道隔墙（形成 2 个房间）
- S-COLUMN     4 个 KZ1 柱块（500×500）
- A-DOOR       2 个 M1021 门块（洞口 1000×2100）
- A-DOOR       2 个 C1515 窗块（洞口 1500×1500）
- S-BEAM       2 根梁（8000 / 5000），梁长合计 13000
- S-REBAR      梁配筋文字（KL1(2) 300×600、%%1328@200）
- P-PIPE       给水管 1 段，长度 8000
- 门窗表 / 图例 / 苗木表   三张表文字（结构化为二期解析对象）
- L-PLANT      3 株乔木块

用法：
    python tests/fixtures/make_quantity_dxf.py <输出路径.dxf>
"""
from __future__ import annotations

import os
import sys

LAYERS = (
    ("A-WALL", 1),
    ("A-DOOR", 2),
    ("A-FRAME", 7),
    ("S-COLUMN", 3),
    ("S-BEAM", 4),
    ("S-REBAR", 5),
    ("P-PIPE", 6),
    ("L-PLANT", 3),
    ("门窗表", 7),
    ("图例", 7),
    ("苗木表", 7),
)

FRAME_W, FRAME_H = 42000, 29700


def _add_block(doc, name: str, layer: str, points, closed: bool = True):
    blk = doc.blocks.new(name=name)
    blk.add_lwpolyline(points, close=closed, dxfattribs={"layer": layer})
    return blk


def build(path: str) -> str:
    """生成 quantity.dxf 并返回路径。"""
    import ezdxf

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # 毫米（真实施工图惯例）
    msp = doc.modelspace()
    for name, color in LAYERS:
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    # ---- 图框 + 图签（A3 1:100 画成 42000×29700）----
    msp.add_lwpolyline(
        [(0, 0), (FRAME_W, 0), (FRAME_W, FRAME_H), (0, FRAME_H)],
        close=True, dxfattribs={"layer": "A-FRAME"},
    )
    for y, text in ((1000, "建施-01"), (1500, "一层平面图"), (2000, "1:100")):
        msp.add_text(text, height=250,
                     dxfattribs={"layer": "A-FRAME", "insert": (30000, y)})

    # ---- 墙体：外墙 8000×5000 + 中隔墙 ----
    msp.add_lwpolyline(
        [(0, 0), (8000, 0), (8000, 5000), (0, 5000)],
        close=True, dxfattribs={"layer": "A-WALL"},
    )
    msp.add_line((4000, 0), (4000, 5000), dxfattribs={"layer": "A-WALL"})

    # ---- 柱：KZ1 500×500 × 4 ----
    _add_block(doc, "KZ1", "S-COLUMN",
               [(-250, -250), (250, -250), (250, 250), (-250, 250)])
    for pt in ((0, 0), (8000, 0), (0, 5000), (8000, 5000)):
        msp.add_blockref("KZ1", pt, dxfattribs={"layer": "S-COLUMN"})

    # ---- 门 M1021 × 2（洞口 1000×2100）----
    _add_block(doc, "M1021", "A-DOOR", [(0, 0), (1000, 0), (1000, 2100), (0, 2100)])
    for pt in ((1500, 0), (6500, 0)):
        msp.add_blockref("M1021", pt, dxfattribs={"layer": "A-DOOR"})

    # ---- 窗 C1515 × 2（洞口 1500×1500）----
    _add_block(doc, "C1515", "A-DOOR", [(0, 0), (1500, 0), (1500, 240), (0, 240)])
    for pt in ((2000, 5000), (6000, 5000)):
        msp.add_blockref("C1515", pt, dxfattribs={"layer": "A-DOOR"})

    # ---- 梁：KL1 8000 + KL2 5000 ----
    msp.add_line((0, 2500), (8000, 2500), dxfattribs={"layer": "S-BEAM"})
    msp.add_line((4000, 0), (4000, 5000), dxfattribs={"layer": "S-BEAM"})

    # ---- 梁配筋文字（%%132 = HRB400 符号）----
    msp.add_text("KL1(2) 300×600", height=200,
                 dxfattribs={"layer": "S-REBAR", "insert": (500, 2600)})
    msp.add_text("%%1328@200", height=200,
                 dxfattribs={"layer": "S-REBAR", "insert": (500, 2400)})

    # ---- 给水管：长度 8000 ----
    msp.add_line((0, -800), (8000, -800),
                 dxfattribs={"layer": "P-PIPE", "lineweight": 30})

    # ---- 门窗表（表格文字，二期结构化）----
    schedule = [
        (20000, ["编号", "洞口尺寸", "数量", "备注"]),
        (19500, ["M1021", "1000×2100", "2", "成品木门"]),
        (19000, ["C1515", "1500×1500", "2", "断桥铝合金窗"]),
    ]
    for y, cells in schedule:
        for x, text in zip((2000, 4500, 7500, 9500), cells):
            msp.add_text(text, height=250,
                         dxfattribs={"layer": "门窗表", "insert": (x, y)})

    # ---- 图例（符号→含义，二期结构化）----
    legend = [
        (16000, ["图例", ""]),
        (15500, ["消火栓", "DN65"]),
        (15000, ["喷淋头", "DN15"]),
    ]
    for y, cells in legend:
        for x, text in zip((2000, 4500), cells):
            if text:
                msp.add_text(text, height=250,
                             dxfattribs={"layer": "图例", "insert": (x, y)})

    # ---- 苗木表 ----
    plants = [
        (12000, ["苗木表", "", ""]),
        (11500, ["香樟", "胸径15-18", "8"]),
        (11000, ["草坪", "m²", "320"]),
    ]
    for y, cells in plants:
        for x, text in zip((2000, 4500, 7000), cells):
            if text:
                msp.add_text(text, height=250,
                             dxfattribs={"layer": "苗木表", "insert": (x, y)})

    # ---- 乔木块 × 3 ----
    tree = doc.blocks.new(name="香樟")
    tree.add_circle((0, 0), 1200, dxfattribs={"layer": "L-PLANT"})
    for pt in ((10000, 1000), (13000, 1000), (16000, 1000)):
        msp.add_blockref("香樟", pt, dxfattribs={"layer": "L-PLANT"})

    doc.saveas(path)
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = argv[0] if argv else "quantity.dxf"
    print(build(out))


if __name__ == "__main__":
    main()
