#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unit_scale —— 图纸比例三级校准。

校准顺序（每级只做确定性判定，不引入视觉读数）：
    L1  $INSUNITS 头变量 → 图纸单位 → 单位换算系数
    L2  图签/标注文字中的比例（如 1:100）
    L3  图框尺寸 vs 标准纸幅（A0-A4）→ 反推比例并判定模型空间状态

几何状态 geometry_state：
    MODEL_1_TO_1  图框长边/纸幅长边 ≈ 图签比例 → 模型空间即真实尺寸，model_factor=1
    PAPER_SCALED  图框长边/纸幅长边 ≈ 1      → 模型空间为纸面尺寸，需 ×图签比例
    UNKNOWN       证据不足或互相冲突          → 按 mm 假定，status≠OK，进待确认

输出字段：factor_to_mm / scale_ratio / model_factor / calibrated_by / status。
factor_to_mm 表示“图纸坐标 1 单位对应的真实毫米数”，算量端一律用它换算。

CLI:
    python unit_scale.py <drawing.dxf> [--out scale.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from cad_common import dxf_attr, load_config, round_num  # noqa: E402
from entity_pool import DXFEntityPool, layer_role  # noqa: E402

# 只认 “1:N” 形式的制图比例，避免把坐标/标高里的冒号误当比例
_SCALE_RE = re.compile(r"(?<![\d.])1\s*[:：]\s*(\d+(?:\.\d+)?)")


def _text_entities(pool: DXFEntityPool):
    for entity in pool.entities:
        t = entity.dxftype()
        if t == "TEXT":
            yield entity, dxf_attr(entity, "text", "") or ""
        elif t == "MTEXT":
            try:
                yield entity, entity.plain_text()
            except Exception:
                yield entity, ""


def extract_scale_text(pool: DXFEntityPool) -> tuple:
    """从图签/标注文字提取比例，返回 (ratio, 原文, 图层角色)。

    多候选时按出现频次取众数；平手优先图框图层，其次首次出现。
    """
    candidates = []
    for entity, text in _text_entities(pool):
        if not text:
            continue
        role = layer_role(dxf_attr(entity, "layer", "0"))
        for m in _SCALE_RE.finditer(text):
            value = float(m.group(1))
            if value < 1:
                continue
            candidates.append({
                "ratio": round(value, 6),
                "text": m.group(0).replace(" ", ""),
                "role": role,
            })
    if not candidates:
        return None, None, None

    counts = Counter(c["ratio"] for c in candidates)
    top = max(counts.values())
    tied = [r for r, n in counts.items() if n == top]
    frame_ratios = {c["ratio"] for c in candidates if c["role"] == "FRAME"}
    chosen = next((r for r in tied if r in frame_ratios), tied[0])
    picked = next(c for c in candidates if c["ratio"] == chosen)
    ratio = int(chosen) if abs(chosen - round(chosen)) < 1e-9 else chosen
    return ratio, picked["text"], picked["role"]


def _match_paper(frame_long: float, frame_short: float, scale_ratio,
                 papers: dict, aspect_tol: float, ratio_tol: float) -> dict:
    """图框长宽比匹配标准纸幅。

    有图签比例时取“图框长边/纸幅长边”最接近比例的纸幅；
    无图签比例时取最大纸幅（A0）作为参照，仅用于粗判是否模型空间 1:1。
    """
    frame_aspect = frame_long / frame_short if frame_short else 0.0
    candidates = []
    for name, size in (papers or {}).items():
        try:
            paper_long, paper_short = float(size[0]), float(size[1])
        except (TypeError, ValueError, IndexError):
            continue
        if paper_long <= 0 or paper_short <= 0:
            continue
        aspect = paper_long / paper_short
        if abs(frame_aspect / aspect - 1.0) > aspect_tol:
            continue
        candidates.append({
            "name": name,
            "long": paper_long,
            "ratio": frame_long / paper_long,
        })
    if not candidates:
        return {"paper": None, "frame_paper_ratio": None, "aspect_matched": False}

    if scale_ratio:
        candidates.sort(key=lambda c: abs(c["ratio"] - float(scale_ratio)))
        best = candidates[0]
        matched = abs(best["ratio"] / float(scale_ratio) - 1.0) <= ratio_tol
        return {
            "paper": best["name"] if matched else None,
            "frame_paper_ratio": round_num(best["ratio"], 3),
            "aspect_matched": True,
        }
    best = max(candidates, key=lambda c: c["long"])
    return {
        "paper": best["name"],
        "frame_paper_ratio": round_num(best["ratio"], 3),
        "aspect_matched": True,
    }


def calibrate(source) -> dict:
    """校准图纸比例。source 可为 DXF 路径、ezdxf 文档或 DXFEntityPool。"""
    pool = source if isinstance(source, DXFEntityPool) else DXFEntityPool(source)
    units = load_config("units")
    thresholds = load_config("thresholds")
    scale_cfg = thresholds.get("scale") or {}
    if not scale_cfg:
        raise RuntimeError("thresholds.yaml 缺少 scale 配置")
    papers = scale_cfg.get("paper_sizes_mm") or {}
    if not papers:
        raise RuntimeError("thresholds.scale.paper_sizes_mm 未配置标准纸幅")
    ratio_tol = float(scale_cfg.get("frame_paper_tolerance", 0.05))
    aspect_tol = float(scale_cfg.get("paper_aspect_tolerance", 0.05))
    model_min_long = float(scale_cfg.get("model_min_frame_long_mm", 2378))

    insunits = int(pool.doc.header.get("$INSUNITS", 0) or 0)
    unit_name = (units.get("dxf_insunits") or {}).get(str(insunits), "unitless")
    unit_factor = float(
        (units.get("length", {}).get("factor_to_mm") or {}).get(unit_name, 1.0)
    )

    pending, notes = [], []
    status = "OK"
    confidence = 1.0
    calibrated_by = "INSUNITS"
    detail = "INSUNITS"
    geometry_state = "UNKNOWN"
    model_factor = 1.0
    paper = None
    frame_paper_ratio = None

    scale_ratio, scale_text, _role = extract_scale_text(pool)
    frame_bbox = pool.frame_bbox()

    if frame_bbox:
        frame_long = max(frame_bbox[2] - frame_bbox[0], frame_bbox[3] - frame_bbox[1])
        frame_short = min(frame_bbox[2] - frame_bbox[0], frame_bbox[3] - frame_bbox[1])
    else:
        frame_long = frame_short = 0.0

    if frame_bbox and scale_ratio:
        matched = _match_paper(frame_long, frame_short, scale_ratio,
                               papers, aspect_tol, ratio_tol)
        paper = matched["paper"]
        frame_paper_ratio = matched["frame_paper_ratio"]
        if paper and abs(frame_paper_ratio / float(scale_ratio) - 1.0) <= ratio_tol:
            geometry_state = "MODEL_1_TO_1"
            model_factor = 1.0
            calibrated_by = "TITLE_BLOCK"
            detail = "FRAME_VS_PAPER"
            confidence = 0.95
        elif matched["aspect_matched"] and abs(frame_paper_ratio - 1.0) <= ratio_tol:
            geometry_state = "PAPER_SCALED"
            model_factor = float(scale_ratio)
            calibrated_by = "TITLE_BLOCK"
            detail = "FRAME_VS_PAPER"
            confidence = 0.9
        else:
            status = "PENDING_CONFLICT"
            calibrated_by = "ASSUMED"
            detail = "FRAME_SCALE_CONFLICT"
            confidence = 0.2
            pending.append(
                f"图框/纸幅比 {frame_paper_ratio} 与图签比例 1:{scale_ratio} 不一致，比例未校准"
            )
    elif frame_bbox:
        if frame_long >= model_min_long:
            geometry_state = "MODEL_1_TO_1"
            model_factor = 1.0
            calibrated_by = "INSUNITS"
            detail = "FRAME_SIZE"
            confidence = 0.75
            notes.append("未找到图签比例文字，按图框尺寸推断为模型空间 1:1")
        else:
            status = "PENDING_ASSUMED"
            calibrated_by = "ASSUMED"
            detail = "FRAME_ONLY"
            confidence = 0.35
            pending.append("仅有图框、无图签比例，无法判定模型空间是否为 1:1")
    elif scale_ratio:
        status = "PENDING_ASSUMED"
        calibrated_by = "TITLE_BLOCK"
        detail = "TITLE_SCALE_TEXT_ONLY"
        confidence = 0.4
        pending.append("仅有图签比例文字，未定位到图框，无法判定模型空间是否为 1:1")
    else:
        status = "PENDING_ASSUMED"
        calibrated_by = "ASSUMED"
        detail = "NO_SCALE_EVIDENCE"
        confidence = 0.3
        pending.append("未找到图签比例与图框，比例未校准")

    if unit_name == "unitless":
        notes.append("$INSUNITS 缺失或为无单位，按 mm 处理")
        if status == "OK":
            status = "PENDING_ASSUMED"
        confidence = min(confidence, 0.6)

    factor_to_mm = round_num(unit_factor * model_factor, 6)

    return {
        "drawing_unit": unit_name,
        "unit_factor_to_mm": unit_factor,
        "scale_text": scale_text,
        "scale_ratio": scale_ratio,
        "geometry_state": geometry_state,
        "model_factor": model_factor,
        "factor_to_mm": factor_to_mm,
        "calibrated_by": calibrated_by,
        "calibrated_by_detail": detail,
        "status": status,
        "confidence": round_num(confidence, 2),
        "paper": paper,
        "frame_bbox": frame_bbox,
        "frame_paper_ratio": frame_paper_ratio,
        "pending": pending,
        "notes": notes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="图纸比例三级校准")
    parser.add_argument("drawing", help="DXF 图纸路径")
    parser.add_argument("--out", help="可选：输出 scale.json 路径")
    args = parser.parse_args(argv)

    result = calibrate(args.drawing)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
