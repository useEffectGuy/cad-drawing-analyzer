#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目概况抽取 —— PPR 模块能力一（只读消费现有解析结果）。

设计约束：
- 不解析 CAD、不重算工程量，只读取 work/ 下既有产物；
- 每个字段输出 value / source / confidence，缺失一律 null，禁止臆造；
- 字段与专业扩展全部来自 config/，主流程不硬编码专业；
- 模块开关关闭时不产生任何文件。

用法：
    python profile_extractor.py <work目录> [--out project_profile.json]
接口：
    extract_project_profile(parsed_drawings, existing_extraction_result, config)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

PROFILE_SCHEMA = "tzp.project_profile/1.0"

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(MODULE_DIR))
CONFIG_DIR = os.path.join(MODULE_DIR, "config")

DEFAULT_SOURCE_CONFIDENCE = 0.5
DEFAULT_MIN_CONFIDENCE = 0.6


# ---------------------------------------------------------------- 配置加载
def _load_json(path: str):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_module_config() -> dict:
    return _load_yaml(os.path.join(CONFIG_DIR, "module.yaml"))


def load_field_config() -> tuple[list, dict]:
    """返回（通用字段列表, 专业扩展字段表）。"""
    common = _load_yaml(os.path.join(CONFIG_DIR, "profile_fields_common.yaml"))
    by_disc = _load_yaml(os.path.join(CONFIG_DIR, "profile_fields_by_discipline.yaml"))
    return common.get("fields") or [], by_disc.get("disciplines") or {}


def load_sections() -> list:
    data = _load_yaml(os.path.join(CONFIG_DIR, "report_sections.yaml"))
    return data.get("sections") or []


def load_disciplines() -> list:
    data = _load_yaml(os.path.join(ROOT_DIR, "config", "disciplines.yaml"))
    return data.get("disciplines") or []


def is_enabled(config: dict | None = None) -> bool:
    """模块开关：兼容 enable_project_profile_report / enabled / module.enabled 三种写法。"""
    cfg = config if config is not None else load_module_config()
    module = cfg.get("module") or {}
    if "enable_project_profile_report" in cfg:
        return bool(cfg["enable_project_profile_report"])
    if "enabled" in cfg:
        return bool(cfg["enabled"])
    if "enabled" in module:
        return bool(module["enabled"])
    return True


def _confidence_for(source: dict | None, config: dict) -> float:
    table = config.get("source_confidence") or {}
    stype = (source or {}).get("type") or "UNKNOWN"
    try:
        return float(table.get(stype, DEFAULT_SOURCE_CONFIDENCE))
    except (TypeError, ValueError):
        return DEFAULT_SOURCE_CONFIDENCE


# ---------------------------------------------------------------- 输入装载
def _normalize_texts(raw) -> list:
    """统一文本来源格式：[{text, source:{type, ref}}]。"""
    items = raw.get("texts") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, str):
            entry = {"text": it, "source": {"type": "TEXT", "ref": "texts.json"}}
        elif isinstance(it, dict):
            text = it.get("text") or it.get("content") or ""
            source = it.get("source") or {"type": "TEXT", "ref": "texts.json"}
            if isinstance(source, str):
                source = {"type": "TEXT", "ref": source}
            entry = {"text": text, "source": source}
        else:
            continue
        if str(entry["text"]).strip():
            out.append(entry)
    return out


def _scan_cio(work_dir: str, names: list) -> tuple[list, list]:
    """扫描 *_CIO.json，返回（图签/说明文本, project_meta 列表）。"""
    texts, metas = [], []
    for name in names:
        if not name.endswith("_CIO.json"):
            continue
        cio = _load_json(os.path.join(work_dir, name))
        if not cio:
            continue
        pm = cio.get("project_meta") or {}
        if pm:
            metas.append(dict(pm, source_file=name))
        for ent in cio.get("parsed_entities") or []:
            attrs = ent.get("attributes") or {}
            label = str(attrs.get("label_text") or "").strip()
            if not label:
                continue
            is_frame = ent.get("layer_role") == "FRAME"
            texts.append({
                "text": label,
                "source": {
                    "type": "TITLE_BLOCK" if is_frame else "CIO_TEXT",
                    "ref": f"{name}:{attrs.get('layer_name', '')}",
                },
            })
    return texts, metas


def load_work_sources(work_dir: str) -> dict:
    """读取现有 Skill 产物，聚合成概况抽取的输入。"""
    work_dir = os.path.abspath(work_dir)
    names = sorted(os.listdir(work_dir)) if os.path.isdir(work_dir) else []

    catalog = _load_json(os.path.join(work_dir, "catalog_classified.json"))
    if not catalog:
        catalog = _load_json(os.path.join(work_dir, "catalog.json"))

    texts_path = os.path.join(work_dir, "texts.json")
    texts = _normalize_texts(_load_json(texts_path)) if os.path.isfile(texts_path) else []

    cio_texts, metas = _scan_cio(work_dir, names)
    texts.extend(cio_texts)

    materials = {}
    for name in names:
        if "材料识别数据" in name and name.endswith(".json"):
            materials = _load_json(os.path.join(work_dir, name)) or materials

    return {
        "work_dir": work_dir,
        "files": [n for n in names if os.path.isfile(os.path.join(work_dir, n))],
        "catalog": catalog,
        "quantities": _load_json(os.path.join(work_dir, "quantities.json")),
        "wbs": _load_json(os.path.join(work_dir, "wbs.json")),
        "inventory": _load_json(os.path.join(work_dir, "inventory.json")),
        "texts": texts,
        "materials": materials,
        "project_metas": metas,
    }


# ---------------------------------------------------------------- 字段匹配
def _iter_lines(entries: list):
    for entry in entries:
        source = entry.get("source") or {"type": "UNKNOWN", "ref": ""}
        for line in str(entry.get("text", "")).replace("\r", "\n").split("\n"):
            line = line.strip()
            if line:
                yield line, source


def _clean_value(raw: str) -> str:
    value = raw.strip()
    # 图签字列之间常用 3 个以上空格分隔，超出部分属于下一列
    value = re.split(r"\s{3,}", value)[0].strip()
    return value.strip(" \t;；,，")


def _match_aliases(field: dict, lines: list) -> tuple[str | None, dict | None]:
    for alias in field.get("aliases") or []:
        pattern = re.compile(re.escape(alias) + r"\s*[:：]\s*(.+)")
        for line, source in lines:
            m = pattern.search(line)
            if m:
                value = _clean_value(m.group(1))
                if value:
                    return value, source
    return None, None


def _match_pattern(field: dict, lines: list) -> tuple[str | None, dict | None]:
    pattern = field.get("value_pattern")
    if not pattern:
        return None, None
    rx = re.compile(pattern)
    for line, source in lines:
        m = rx.search(line)
        if m:
            value = (m.group(1) if m.groups() else m.group(0)).strip()
            if value:
                return value, source
    return None, None


def _field_result(field: dict, value: str | None, source: dict | None,
                  config: dict) -> dict:
    result = {
        "name": field.get("name") or field.get("id"),
        "value": value,
        "source": None,
        "confidence": 0.0,
    }
    if value is None:
        return result
    result["source"] = {
        "type": (source or {}).get("type") or "UNKNOWN",
        "ref": (source or {}).get("ref", ""),
    }
    result["confidence"] = _confidence_for(result["source"], config)
    return result


def _derived(field: dict, value, ref: str, config: dict) -> dict:
    return {
        "name": field.get("name") or field.get("id"),
        "value": value,
        "source": {"type": "DERIVED", "ref": ref},
        "confidence": _confidence_for({"type": "DERIVED"}, config),
    }


# ---------------------------------------------------------------- 专业识别
def _detect_disciplines(sources: dict) -> list:
    registry = load_disciplines()
    name_to_code = {d.get("name"): d.get("code") for d in registry}
    order = [d.get("code") for d in registry]
    valid = set(order)

    found = []
    catalog = sources.get("catalog") or {}
    names = catalog.get("disciplines") or list((catalog.get("discipline_stats") or {}).keys())
    for name in names:
        code = name_to_code.get(name)
        if code and code != "UNKNOWN" and code not in found:
            found.append(code)
    for pm in sources.get("project_metas") or []:
        code = pm.get("discipline")
        if code in valid and code != "UNKNOWN" and code not in found:
            found.append(code)
    found.sort(key=lambda c: order.index(c) if c in order else len(order))
    name_of = {d.get("code"): d.get("name") for d in registry}
    return [{"code": c, "name": name_of.get(c, c)} for c in found]


# ---------------------------------------------------------------- 主流程
def _empty_profile(config: dict) -> dict:
    return {
        "schema": PROFILE_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "module": {
            "name": config.get("module_name", "project_profile_report"),
            "version": config.get("version", "1.0"),
            "enabled": False,
        },
        "sources": {"work_dir": "", "files": [], "text_count": 0, "cio_count": 0},
        "detected_disciplines": [],
        "common": {},
        "by_discipline": {},
        "stats": {"field_total": 0, "filled": 0, "missing": 0,
                  "low_confidence": 0, "detected_discipline_count": 0},
    }


def extract_project_profile(parsed_drawings, existing_extraction_result=None,
                            config: dict | None = None) -> dict:
    """抽取项目概况。

    parsed_drawings：work 目录路径，或 load_work_sources() 的返回字典；
    existing_extraction_result：可选，补充 quantities / wbs / materials 等只读结果。
    """
    cfg = config if config is not None else load_module_config()
    if not is_enabled(cfg):
        return _empty_profile(cfg)

    if isinstance(parsed_drawings, str):
        sources = load_work_sources(parsed_drawings)
    else:
        sources = dict(parsed_drawings or {})
    for key, value in (existing_extraction_result or {}).items():
        if not sources.get(key) and value:
            sources[key] = value

    common_fields, disc_fields_cfg = load_field_config()
    min_conf = float(cfg.get("min_confidence_for_report", DEFAULT_MIN_CONFIDENCE))
    lines = list(_iter_lines(sources.get("texts") or []))
    catalog = sources.get("catalog") or {}
    disciplines = _detect_disciplines(sources)

    common = {}
    filled = missing = low = 0

    for field in common_fields:
        fid = field.get("id")
        derive = field.get("derive")
        value, source = None, None
        if not derive:
            value, source = _match_aliases(field, lines)
            if value is None:
                value, source = _match_pattern(field, lines)

        if derive == "disciplines":
            names = [d["name"] for d in disciplines]
            if names:
                common[fid] = _derived(
                    field, "、".join(names),
                    os.path.basename(os.path.join(sources.get("work_dir", ""), "catalog_classified.json")),
                    cfg)
            else:
                common[fid] = _field_result(field, None, None, cfg)
        elif derive == "catalog.total_sheets":
            total = catalog.get("total_sheets")
            if total:
                common[fid] = _derived(field, f"{total} 张", "catalog.total_sheets", cfg)
            else:
                common[fid] = _field_result(field, None, None, cfg)
        else:
            common[fid] = _field_result(field, value, source, cfg)

        item = common[fid]
        if item["value"] in (None, ""):
            missing += 1
        else:
            filled += 1
            if 0 < item["confidence"] < min_conf:
                low += 1

    by_discipline = {}
    disc_total = 0
    for disc in disciplines:
        cfg_disc = disc_fields_cfg.get(disc["code"]) or {}
        fields = {}
        for field in cfg_disc.get("fields") or []:
            value, source = _match_aliases(field, lines)
            if value is None:
                value, source = _match_pattern(field, lines)
            fields[field.get("id")] = _field_result(field, value, source, cfg)
            item = fields[field.get("id")]
            disc_total += 1
            if item["value"] in (None, ""):
                missing += 1
            else:
                filled += 1
                if 0 < item["confidence"] < min_conf:
                    low += 1
        by_discipline[disc["code"]] = {"name": disc["name"], "fields": fields}

    if not by_discipline:
        # 未识别专业：只输出通用概况
        by_discipline = {}

    field_total = len(common_fields) + disc_total
    return {
        "schema": PROFILE_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "module": {
            "name": cfg.get("module_name", "project_profile_report"),
            "version": cfg.get("version", "1.0"),
            "enabled": True,
        },
        "sources": {
            "work_dir": sources.get("work_dir", ""),
            "files": sources.get("files") or [],
            "text_count": len(sources.get("texts") or []),
            "cio_count": len(sources.get("project_metas") or []),
        },
        "detected_disciplines": disciplines,
        "common": common,
        "by_discipline": by_discipline,
        "stats": {
            "field_total": field_total,
            "filled": filled,
            "missing": missing,
            "low_confidence": low,
            "detected_discipline_count": len(disciplines),
        },
    }


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="项目概况抽取（PPR 模块）")
    ap.add_argument("work_dir", help="work 目录路径（含 catalog/quantities 等既有产物）")
    ap.add_argument("--out", default=None, help="输出 project_profile.json 路径")
    args = ap.parse_args(argv)

    cfg = load_module_config()
    if not is_enabled(cfg):
        print("[--] PPR 模块已关闭（enable_project_profile_report: false），未生成概况。")
        return 0

    profile = extract_project_profile(args.work_dir, config=cfg)
    out_path = args.out or os.path.join(args.work_dir, cfg.get("profile_filename", "project_profile.json"))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    s = profile["stats"]
    print(f"[OK] 概况抽取  {s['filled']}/{s['field_total']} 字段"
          f"  缺失 {s['missing']}  低置信度 {s['low_confidence']}  → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
