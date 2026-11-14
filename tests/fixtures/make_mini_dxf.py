#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成冒烟测试用最小 DXF（运行时生成，不纳入版本库）

内容：1 个房间（闭合矩形）+ 2 个门块 + 1 段管线 + 图纸目录表文字。
用法：
    python tests/fixtures/make_mini_dxf.py <输出路径.dxf>
"""
from __future__ import annotations

import os
import sys


def build(path: str) -> str:
    """生成 mini.dxf 并返回路径。"""
    import ezdxf

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # 毫米（ezdxf 默认 6=米，与本图坐标不符）
    msp = doc.modelspace()

    for name, color in (
        ("建筑-墙体", 1),
        ("建筑-门窗", 2),
        ("给排水-给水管", 4),
        ("图纸目录", 7),
    ):
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    # 1 个房间：5000 × 4000，闭合 LWPOLYLINE
    msp.add_lwpolyline(
        [(0, 0), (5000, 0), (5000, 4000), (0, 4000)],
        close=True,
        dxfattribs={"layer": "建筑-墙体"},
    )

    # 门块定义 + 2 个门参照
    door = doc.blocks.new(name="门")
    door.add_line((0, 0), (0, 900), dxfattribs={"layer": "建筑-门窗"})
    door.add_arc((0, 0), 900, 0, 90, dxfattribs={"layer": "建筑-门窗"})
    msp.add_blockref("门", (1000, 0), dxfattribs={"layer": "建筑-门窗"})
    msp.add_blockref("门", (3000, 0), dxfattribs={"layer": "建筑-门窗"})

    # 1 段管线：长度 5000
    msp.add_line((0, -800), (5000, -800),
                 dxfattribs={"layer": "给排水-给水管", "lineweight": 30})

    # 图纸目录表：标题与表头同一行（行聚类后作为表头被跳过）
    col_x = [5200, 6000, 6800, 7800, 9200]
    rows = [
        (2800, ["图纸目录", "序号", "图号", "图名", "规格"]),
        (2700, ["", "1", "建施-01", "一层平面图", "1:100"]),
        (2600, ["", "2", "水施-01", "给排水平面图", "1:100"]),
    ]
    for y, cells in rows:
        for x, text in zip(col_x, cells):
            if not text:
                continue
            msp.add_text(text, height=250,
                         dxfattribs={"layer": "图纸目录", "insert": (x, y)})

    doc.saveas(path)
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = argv[0] if argv else "mini.dxf"
    print(build(out))


if __name__ == "__main__":
    main()
