#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图纸版本差异比对脚本（预留）—— 工作流扩展
用法:
    python compare_revisions.py <原版.dxf> <新版.dxf> --out diff.json
功能:
    对比两版图纸的图层、图块、文字、标注差异，输出变更清单。
    预留接口，初版实现基础的图层/图块/实体数量对比。
依赖: ezdxf
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import ezdxf
except ImportError:
    print("[ERROR] 缺少依赖 ezdxf，请先执行: pip install ezdxf", file=sys.stderr)
    sys.exit(1)

def _snapshot(dxf_path: str) -> dict:
    """对图纸做快照：图层、图块、实体类型计数、文字内容。"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    layers = [l.dxf.name for l in doc.layers]
    entity_types = Counter()
    blocks = Counter()
    texts = []
    for e in msp:
        entity_types[e.dxftype()] += 1
        if e.dxftype() == "INSERT":
            try:
                blocks[e.dxf.name] += 1
            except Exception:
                pass
        if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
            try:
                if e.dxftype() == "MTEXT":
                    texts.append(e.text.strip())
                else:
                    texts.append(e.dxf.text.strip())
            except Exception:
                pass
    return {
        "file": os.path.basename(dxf_path),
        "layers": set(layers),
        "entity_types": dict(entity_types),
        "blocks": dict(blocks),
        "texts": set(t for t in texts if t),
    }

def compare(old_path: str, new_path: str) -> dict:
    """对比两版图纸。"""
    try:
        from convert_dwg import ensure_dxf
        old_path = ensure_dxf(old_path)
        new_path = ensure_dxf(new_path)
    except Exception as e:
        return {"error": str(e)}

    old = _snapshot(old_path)
    new = _snapshot(new_path)

    diff = {
        "old_file": old["file"],
        "new_file": new["file"],
        "layers": {
            "added": sorted(new["layers"] - old["layers"]),
            "removed": sorted(old["layers"] - new["layers"]),
            "common": sorted(old["layers"] & new["layers"]),
        },
        "blocks": {
            "added": {k: v for k, v in new["blocks"].items() if k not in old["blocks"]},
            "removed": {k: v for k, v in old["blocks"].items() if k not in new["blocks"]},
            "count_changed": {
                k: {"old": old["blocks"][k], "new": new["blocks"][k]}
                for k in old["blocks"] if k in new["blocks"] and old["blocks"][k] != new["blocks"][k]
            },
        },
        "texts": {
            "added_count": len(new["texts"] - old["texts"]),
            "removed_count": len(old["texts"] - new["texts"]),
            "added_samples": sorted(new["texts"] - old["texts"])[:20],
            "removed_samples": sorted(old["texts"] - new["texts"])[:20],
        },
        "entity_types": {
            "old": old["entity_types"],
            "new": new["entity_types"],
        },
    }
    return diff

# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="图纸版本差异比对（预留功能）")
    ap.add_argument("old", help="原版图纸路径")
    ap.add_argument("new", help="新版图纸路径")
    ap.add_argument("--out", required=True, help="输出 diff.json 路径")
    args = ap.parse_args()

    diff = compare(args.old, args.new)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(diff, f, ensure_ascii=False, indent=2)
    print(f"[OK] 差异比对完成: {args.out}")
    print(f"     图层新增: {len(diff['layers']['added'])}，删除: {len(diff['layers']['removed'])}")
    print(f"     图块新增: {len(diff['blocks']['added'])}，删除: {len(diff['blocks']['removed'])}")

if __name__ == "__main__":
    main()
