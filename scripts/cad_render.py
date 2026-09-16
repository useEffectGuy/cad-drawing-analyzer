#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAD 图纸渲染预览脚本
用法:
    python cad_render.py <drawing.dxf> [--out preview.png] [--dpi 150] [--layer LAYERNAME]
功能:
    将 DXF 图纸渲染为 PNG 图片，便于快速"看图"。
    支持按图层过滤渲染、自定义分辨率。
依赖: ezdxf, matplotlib
"""
from __future__ import annotations
import argparse
import os
import sys
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    import ezdxf
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
except ImportError as e:
    print(f"[ERROR] 缺少依赖: {e}\n请执行: pip install ezdxf matplotlib", file=sys.stderr)
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

def _setup_cjk_font():
    """尝试配置中文字体，避免图中中文乱码。"""
    candidates = ["Microsoft YaHei", "SimHei", "Noto Sans SC", "PingFang SC", "WenQuanYi Zen Hei"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return None
def render(dxf_path: str, out_path: str, dpi: int = 150, layers: list | None = None,
           bg: str = "#FFFFFF", fg: str = "#000000"):
    font = _setup_cjk_font()
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    fig = plt.figure(figsize=(16, 12), dpi=dpi)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    ax.set_facecolor(bg)
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    frontend = Frontend(ctx, backend)
    if layers:
        # 仅渲染指定图层
        frontend.draw_entities(
            [e for e in msp if e.dxf.get("layer", "0") in layers]
        )
    else:
        frontend.draw_layout(msp)
    ax.set_aspect("equal")
    ax.autoscale(enable=True)
    ax.axis("off")
    title = os.path.basename(dxf_path)
    ax.set_title(title, fontsize=14, color=fg, pad=10)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, dpi=dpi, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    return out_path, font
def main():
    ap = argparse.ArgumentParser(description="DXF 图纸渲染预览")
    ap.add_argument("dxf", help="DXF 图纸路径")
    ap.add_argument("--out", default=None, help="输出 PNG 路径")
    ap.add_argument("--dpi", type=int, default=150, help="分辨率 DPI")
    ap.add_argument("--layer", action="append", default=None,
                    help="仅渲染指定图层（可多次指定）")
    args = ap.parse_args()
    # DWG 自动转换
    args.dxf = _ensure_dxf_input(args.dxf)
    base = os.path.splitext(os.path.basename(args.dxf))[0]
    out = args.out or f"{base}_预览.png"
    try:
        path, font = render(args.dxf, out, args.dpi, args.layer)
        print(f"[OK] 预览图: {os.path.abspath(path)}")
        print(f"[INFO] 中文字体: {font or '未找到，中文可能显示为方块'}")
    except Exception as e:
        print(f"[ERROR] 渲染失败: {e}", file=sys.stderr)
        sys.exit(2)
if __name__ == "__main__":
    main()