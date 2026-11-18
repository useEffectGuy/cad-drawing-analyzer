#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L2 语义标签链构建 —— 把 WBS 分部分项与专业、单位工程串成四级标签链。

用法:
    python build_label_chain.py <work目录> [--out label_chains.json]
功能:
    读取 work/ 下已有 catalog_classified.json、wbs.json，按 GB 50300 分部树生成
    “专业 → 单位工程 → 分部 → 分项”标签链；每条链带置信度与证据，逐条按
    schemas/label_chain.schema.json 校验。
约束:
    - 只读消费既有产物，不改 quantities/wbs；
    - 不生成无证据的链接；未识别专业显式标记 UNKNOWN；
    - 不写 GB/T 50500 清单编码（无权威映射时不臆造）。
依赖: jsonschema（契约校验）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_common import Out, add_output_args, classify_layer  # noqa: E402
from contracts import require_config_keys, stamp, validate  # noqa: E402

BUNDLE_SCHEMA = "tzp.label_chains/1.0"
UNKNOWN_NAME = "未识别"


def _load_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _layer_of(entry: dict) -> str:
    """WBS 条目里的“图层”可能是 dict（构件）或 str（线长/面积）。"""
    layer = entry.get("图层")
    if isinstance(layer, dict):
        return next(iter(layer), "")
    return str(layer or "")


def _confidence(thresholds: dict, matched: bool) -> float:
    """thresholds 置信度为 0-100，标签链契约为 0-1。"""
    conf = thresholds.get("confidence") or {}
    score = conf.get("high" if matched else "low")
    if score is None:
        raise RuntimeError("config/thresholds.yaml 缺少 confidence.high/low")
    return round(float(score) / 100.0, 2)


def build_label_chains(work_dir: str) -> dict:
    catalog = _load_json(os.path.join(work_dir, "catalog_classified.json"))
    if not catalog:
        catalog = _load_json(os.path.join(work_dir, "catalog.json"))
    wbs = _load_json(os.path.join(work_dir, "wbs.json"))
    if not wbs:
        raise RuntimeError(f"缺少 wbs.json：{work_dir}")

    registry = require_config_keys("disciplines")
    thresholds = require_config_keys("thresholds")
    by_name = {d.get("name"): d for d in registry.get("disciplines") or []}
    default_std = registry.get("default_acceptance_standard", "OTHER")

    items = []
    for division, subs in (wbs.get("divisions") or {}).items():
        for sub, subitems in (subs or {}).items():
            for subitem, entries in (subitems or {}).items():
                entries = entries or []
                layers = [_layer_of(e) for e in entries]
                hit_names = [classify_layer(layer) for layer in layers if layer]
                counts = Counter(name for name in hit_names if name)
                disc_name = counts.most_common(1)[0][0] if counts else UNKNOWN_NAME
                matched = disc_name != UNKNOWN_NAME
                reg = by_name.get(disc_name) or {}
                conf = _confidence(thresholds, matched)
                sub_conf = conf if subitem != "未明确分项" else _confidence(thresholds, False)

                evidence = [{
                    "type": "RULE",
                    "ref": f"gb50300_map.yaml#{division}/{sub}/{subitem}",
                }]
                for layer in sorted({layer for layer in layers if layer})[:3]:
                    evidence.append({
                        "type": "LAYER",
                        "ref": layer,
                        "detail": f"{layers.count(layer)} 项",
                    })

                items.append({
                    "discipline": {
                        "code": reg.get("code", "UNKNOWN"),
                        "name": disc_name,
                        "confidence": conf,
                    },
                    "unit_work": {
                        "name": reg.get("unit_work") or "未分类工程",
                        "confidence": conf,
                    },
                    "division": {
                        "name": division,
                        "gb50300_code": None,
                        "confidence": conf,
                    },
                    "subitem": {
                        "name": subitem,
                        "gb50300_code": None,
                        "confidence": sub_conf,
                    },
                    "acceptance_standard": reg.get("acceptance_standard") or default_std,
                    "confidence": round(min(conf, sub_conf), 2),
                    "evidence": evidence,
                    "source_wbs": {"division": division, "sub": sub, "subitem": subitem},
                    "anchors": [
                        {
                            "name": e.get("名称", ""),
                            "unit": e.get("单位", ""),
                            "quantity": e.get("数量", 0),
                        }
                        for e in entries[:10]
                    ],
                })

    for idx, item in enumerate(items):
        errors = validate(item, "label_chain")
        if errors:
            raise RuntimeError(f"标签链契约校验失败（第 {idx} 条）：" + "；".join(errors[:3]))

    bundle = {
        "schema": BUNDLE_SCHEMA,
        "standard": wbs.get("standard", "GB 50300-2013"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {"work_dir": os.path.abspath(work_dir)},
        "total": len(items),
        "by_discipline": dict(Counter(i["discipline"]["name"] for i in items)),
        "items": items,
    }
    return stamp(bundle, "label_chains")


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="L2 语义标签链构建")
    ap.add_argument("work_dir", help="work 目录（含 catalog_classified.json 与 wbs.json）")
    ap.add_argument("--out", default=None, help="输出 label_chains.json 路径")
    add_output_args(ap)
    args = ap.parse_args()
    out = Out.from_args(args)

    bundle = build_label_chains(args.work_dir)
    out_path = args.out or os.path.join(args.work_dir, "label_chains.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    stats = "，".join(f"{k} {v}" for k, v in bundle["by_discipline"].items()) or "无"
    out.ok("标签链", out_path, f"{bundle['total']} 条", stats)


if __name__ == "__main__":
    main()
