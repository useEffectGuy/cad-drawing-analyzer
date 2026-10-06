#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DXF 体检脚本 —— 工作流步骤①输入清点 + 步骤②格式归一辅助
用法:
    # 单张图纸体检
    python inspect_dxf.py <图纸.dxf> --out <输出目录> --json
    # 批量输入清点（目录或文件列表）
    python inspect_dxf.py --inventory <目录> --out inventory.json
功能:
    1. 输入清点：统计文件格式/数量/大小，识别超大图纸并制定切片策略
    2. DXF 体检：图层/图元/块/文字/标注清单 + 规范性检查
依赖: ezdxf
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime

try:
    import ezdxf
except ImportError:
    print("[ERROR] 缺少依赖 ezdxf，请先执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)

# 复用 cad_inspect 的完整体检逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cad_inspect import inspect_dxf as _inspect_dxf_full
except ImportError:
    _inspect_dxf_full = None

# ---------------------------------------------------------------- 输入清点
SUPPORTED_EXTS = (".dxf", ".dwg", ".pdf")
LARGE_FILE_MB = 50
LARGE_ENTITY_COUNT = 50000

def _collect_files(inputs: list) -> list:
    """从目录或文件列表收集支持的图纸文件。"""
    files = []
    for item in inputs:
        if os.path.isdir(item):
            for root, _, fnames in os.walk(item):
                for f in fnames:
                    if f.lower().endswith(SUPPORTED_EXTS):
                        files.append(os.path.join(root, f))
        elif os.path.isfile(item) and item.lower().endswith(SUPPORTED_EXTS):
            files.append(item)
    return files

def inventory(inputs: list) -> dict:
    """输入清点：统计文件格式/数量/大小，识别超大图纸。"""
    files = _collect_files(inputs)
    fmt_counts = {}
    items = []
    for fp in sorted(files):
        ext = os.path.splitext(fp)[1].lower()
        size_mb = os.path.getsize(fp) / (1024 * 1024)
        is_large = size_mb > LARGE_FILE_MB
        # 对 DXF 检查实体数
        entity_count = None
        if ext == ".dxf":
            try:
                doc = ezdxf.readfile(fp)
                entity_count = len(list(doc.modelspace()))
                is_large = is_large or entity_count > LARGE_ENTITY_COUNT
            except Exception:
                entity_count = None
        slice_strategy = None
        if is_large:
            slice_strategy = "大图纸切片：按图框分块渲染/解析，单块实体数控制在 1 万以内"
        items.append({
            "path": fp,
            "filename": os.path.basename(fp),
            "format": ext.lstrip("."),
            "size_mb": round(size_mb, 2),
            "entity_count": entity_count,
            "is_large": is_large,
            "slice_strategy": slice_strategy,
        })
        fmt_counts[ext.lstrip(".")] = fmt_counts.get(ext.lstrip("."), 0) + 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_files": len(items),
        "format_counts": fmt_counts,
        "large_files": [i["filename"] for i in items if i["is_large"]],
        "items": items,
    }

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="DXF 体检与输入清点（工作流步骤①②）")
    ap.add_argument("drawing", nargs="?", help="单张 DXF/DWG 图纸路径")
    ap.add_argument("--inventory", nargs="+", help="批量输入清点：目录或文件列表")
    ap.add_argument("--out", required=True, help="输出路径（JSON 文件或目录）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    if args.inventory:
        result = inventory(args.inventory)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 输入清点完成：{result['total_files']} 个文件")
        print(f"     格式分布: {result['format_counts']}")
        if result["large_files"]:
            print(f"     大图纸（需切片）: {result['large_files']}")
        print(f"     输出: {args.out}")
        return

    if not args.drawing:
        ap.error("请指定图纸路径，或使用 --inventory 进行批量清点")

    # 单张体检：优先复用 cad_inspect 完整逻辑
    if _inspect_dxf_full is not None:
        # cad_inspect 的主逻辑；若接口不匹配则降级
        try:
            _inspect_dxf_full(args.drawing, args.out, args.json)
            return
        except Exception as e:
            print(f"[WARN] 完整体检失败，降级为基础体检: {e}", file=sys.stderr)

    # 降级：基础体检
    try:
        from convert_dwg import ensure_dxf
        dxf_path = ensure_dxf(args.drawing)
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        layers = [l.dxf.name for l in doc.layers]
        types = {}
        for e in msp:
            t = e.dxftype()
            types[t] = types.get(t, 0) + 1
        result = {
            "file": args.drawing,
            "dxf_version": doc.dxfversion,
            "layers": layers,
            "layer_count": len(layers),
            "entity_total": sum(types.values()),
            "entity_types": types,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 基础体检完成: {args.out}")
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
