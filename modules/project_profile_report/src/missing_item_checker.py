#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缺项检查 —— PPR 模块配套能力（只读概况，不臆造）。

规则：
- 字段 value 为空 → MISSING；required 字段为 high，其余为 medium；
- 字段 confidence 低于 min_confidence_for_report → LOW_CONFIDENCE；
- 所有进入清单的字段都需要人工复核（needs_review=true）。

用法：
    python missing_item_checker.py <project_profile.json> [--out missing_items.json]
接口：
    check_missing_items(profile, config) -> dict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from profile_extractor import (  # noqa: E402
    DEFAULT_MIN_CONFIDENCE,
    load_field_config,
    load_module_config,
)

MISSING_SCHEMA = "tzp.missing_items/1.0"


def _required_ids(common_fields: list) -> set:
    return {f.get("id") for f in common_fields if f.get("required")}


def _make_item(field_id: str, item: dict, scope: str, status: str,
               severity: str, reason: str) -> dict:
    return {
        "field_id": field_id,
        "name": item.get("name") or field_id,
        "scope": scope,
        "status": status,
        "severity": severity,
        "needs_review": True,
        "reason": reason,
        "confidence": item.get("confidence", 0.0),
    }


def check_missing_items(profile: dict, config: dict | None = None) -> dict:
    cfg = config if config is not None else load_module_config()
    min_conf = float(cfg.get("min_confidence_for_report", DEFAULT_MIN_CONFIDENCE))
    common_fields, _ = load_field_config()
    required = _required_ids(common_fields)

    items = []

    for fid, item in (profile.get("common") or {}).items():
        value = item.get("value")
        if value in (None, ""):
            items.append(_make_item(
                fid, item, "common", "MISSING",
                "high" if fid in required else "medium",
                "图纸中未检索到该字段，禁止臆造"))
        elif float(item.get("confidence") or 0) < min_conf:
            items.append(_make_item(
                fid, item, "common", "LOW_CONFIDENCE",
                "high" if fid in required else "medium",
                f"置信度 {item.get('confidence')} 低于阈值 {min_conf}"))

    for code, block in (profile.get("by_discipline") or {}).items():
        for fid, item in (block.get("fields") or {}).items():
            value = item.get("value")
            if value in (None, ""):
                items.append(_make_item(
                    fid, item, code, "MISSING", "medium",
                    "该专业扩展字段在图纸中未检索到"))
            elif float(item.get("confidence") or 0) < min_conf:
                items.append(_make_item(
                    fid, item, code, "LOW_CONFIDENCE", "medium",
                    f"置信度 {item.get('confidence')} 低于阈值 {min_conf}"))

    order = {"high": 0, "medium": 1, "low": 2}
    status_order = {"MISSING": 0, "LOW_CONFIDENCE": 1}
    items.sort(key=lambda x: (status_order.get(x["status"], 9),
                              order.get(x["severity"], 9), x["field_id"]))

    missing = sum(1 for i in items if i["status"] == "MISSING")
    low = sum(1 for i in items if i["status"] == "LOW_CONFIDENCE")
    return {
        "schema": MISSING_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total": len(items),
        "summary": {
            "missing": missing,
            "low_confidence": low,
            "needs_review": len(items),
        },
        "items": items,
    }


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="PPR 缺项检查")
    ap.add_argument("profile", help="project_profile.json 路径")
    ap.add_argument("--out", default=None, help="输出 missing_items.json 路径")
    args = ap.parse_args(argv)

    with open(args.profile, "r", encoding="utf-8") as f:
        profile = json.load(f)

    result = check_missing_items(profile)
    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.profile)),
        "missing_items.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    s = result["summary"]
    print(f"[OK] 缺项检查  缺项 {s['missing']}  低置信度 {s['low_confidence']}  → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
