#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts —— 契约（schemas/）与配置（config/）的统一入口。

设计约束：
- config/ 与 schemas/ 是唯一真相源，业务脚本不得再内置默认值兜底；
- 校验依赖 jsonschema，缺失即报错，不做降级；
- 本模块只做加载/校验/版本戳，不 import 任何业务脚本。

CLI：
    python scripts/contracts.py --check                      # 全量契约自检
    python scripts/contracts.py --config units               # 打印配置
    python scripts/contracts.py --validate x.json --schema quantity_line
    python scripts/contracts.py --stamp tzp.wbs/1.0          # 打印中间产物 schema id
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from functools import lru_cache

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
SCHEMA_DIR = os.path.join(ROOT_DIR, "schemas")

# 正式产物契约名 → schema 文件
CONTRACTS = {
    "entity": "entity.schema.json",
    "perception_bundle": "perception_bundle.schema.json",
    "label_chain": "label_chain.schema.json",
    "quantity_line": "quantity_line.schema.json",
    "material_line": "material_line.schema.json",
    "drawing_index": "drawing_index.schema.json",
    "cio": "cio_schema.json",
}

# 中间产物 → schema 版本标识（Phase 0 起每个产物必须携带）
INTERMEDIATE = {
    "inventory": "tzp.inventory/1.0",
    "catalog": "tzp.catalog/1.0",
    "quantities": "tzp.quantities/1.0",
    "wbs": "tzp.wbs/1.0",
    "space": "tzp.space/1.0",
    "process": "tzp.process/1.0",
    "material": "tzp.material/1.0",
    "review": "tzp.review/1.0",
    "pdf_quantities": "tzp.pdf.quantities/1.0",
    "summary": "tzp.pipeline.summary/1.0",
    "manifest": "tzp.pipeline.manifest/1.0",
    "cio": "tzp.cio/1.0.0",
    "drawing_index": "tzp.drawing_index/1.0",
    "perception_bundle": "tzp.perception_bundle/1.0",
}

# 必需配置文件及其最小顶层键
CONFIG_CONTRACTS = {
    "units": ("version", "length", "area", "volume", "weight", "precision"),
    "thresholds": ("version", "confidence", "geometry", "deduction", "pending", "scale"),
    "deduction_priority": ("version", "order", "rules", "opening_rule"),
    "disciplines": ("version", "disciplines", "default_acceptance_standard"),
}

ACCEPTANCE_STANDARDS = {"GB50300", "MUNICIPAL", "LANDSCAPE", "OTHER"}


# ---------------------------------------------------------------- 配置
def config_path(name: str) -> str:
    return os.path.join(CONFIG_DIR, f"{name}.yaml")


@lru_cache(maxsize=None)
def _load_config_cached(name: str, mtime: float) -> dict:
    import yaml

    with open(config_path(name), "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"配置文件顶层必须是字典：{config_path(name)}")
    return data


def load_config(name: str, required_keys=()) -> dict:
    """加载 config/<name>.yaml。缺失或键不全直接抛错，不做内置兜底。"""
    path = config_path(name)
    if not os.path.isfile(path):
        raise RuntimeError(f"配置文件缺失：{path}")
    data = _load_config_cached(name, os.path.getmtime(path))
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise RuntimeError(f"配置文件缺少键 {missing}：{path}")
    return data


def require_config_keys(name: str) -> dict:
    """按 CONFIG_CONTRACTS 校验并返回配置。"""
    return load_config(name, CONFIG_CONTRACTS.get(name, ()))


# ---------------------------------------------------------------- Schema
def schema_path(name: str) -> str:
    filename = CONTRACTS.get(name, name if name.endswith(".json") else f"{name}.schema.json")
    return os.path.join(SCHEMA_DIR, filename)


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    path = schema_path(name)
    if not os.path.isfile(path):
        raise RuntimeError(f"Schema 缺失：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _all_schemas() -> dict:
    """按 $id 建立引用库，供跨文件 $ref（如 quantity_line → label_chain）解析。"""
    store = {}
    if not os.path.isdir(SCHEMA_DIR):
        return store
    for filename in sorted(os.listdir(SCHEMA_DIR)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(SCHEMA_DIR, filename), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("$id"):
            store[data["$id"]] = data
    return store


def validate(data, name: str) -> list:
    """按契约校验数据，返回错误信息列表（空列表表示通过）。"""
    import jsonschema
    from referencing import Registry, Resource

    schema = load_schema(name)
    registry = Registry().with_resources(
        (sid, Resource.from_contents(s)) for sid, s in _all_schemas().items()
    )
    validator = jsonschema.Draft7Validator(schema, registry=registry)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def assert_valid(data, name: str) -> None:
    errors = validate(data, name)
    if errors:
        raise RuntimeError(f"契约校验失败（{name}）：" + "；".join(errors[:5]))


def validate_schema_files() -> list:
    """校验 schemas/ 下所有 JSON Schema 自身是否合法。"""
    import jsonschema

    errors = []
    for filename in sorted(os.listdir(SCHEMA_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(SCHEMA_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                jsonschema.Draft7Validator.check_schema(json.load(f))
        except Exception as e:
            errors.append(f"{filename}: {e}")
    return errors


# ---------------------------------------------------------------- 版本戳
def schema_id(stage: str) -> str:
    if stage not in INTERMEDIATE:
        raise KeyError(f"未登记的中间产物：{stage}")
    return INTERMEDIATE[stage]


def stamp(data: dict, stage: str) -> dict:
    """为中间产物写入 schema 版本字段并原样返回。"""
    if not isinstance(data, dict):
        raise TypeError(f"stamp 只接受 dict，收到 {type(data).__name__}")
    data["schema"] = schema_id(stage)
    return data


# ---------------------------------------------------------------- 自检
def check_all() -> list:
    """全量契约自检：schema 合法性、配置完整性、跨配置一致性。"""
    errors = list(validate_schema_files())

    for name in CONTRACTS:
        try:
            load_schema(name)
        except RuntimeError as e:
            errors.append(str(e))

    for name in CONFIG_CONTRACTS:
        try:
            require_config_keys(name)
        except RuntimeError as e:
            errors.append(str(e))

    # 跨配置一致性：洞口扣减阈值两处必须一致
    try:
        th = require_config_keys("thresholds")
        dp = require_config_keys("deduction_priority")
        a = (th.get("deduction") or {}).get("opening_min_area_m2")
        b = (dp.get("opening_rule") or {}).get("min_area_m2")
        if a != b:
            errors.append(f"阈值不一致：thresholds.deduction.opening_min_area_m2={a} "
                          f"vs deduction_priority.opening_rule.min_area_m2={b}")
        order = dp.get("order") or []
        for member, rule in (dp.get("rules") or {}).items():
            if member not in order:
                errors.append(f"deduction_priority.rules 含未在 order 中登记的构件：{member}")
            for dep in (rule.get("deduct") or []):
                if dep not in order:
                    errors.append(f"deduction_priority: {member}.deduct 引用未登记构件 {dep}")
    except RuntimeError as e:
        errors.append(str(e))

    # 专业注册表：代码唯一、验收体系合法、词典存在
    try:
        disc = require_config_keys("disciplines")
        seen = set()
        for d in disc.get("disciplines") or []:
            code = d.get("code")
            if not code:
                errors.append("disciplines: 存在缺少 code 的条目")
                continue
            if code in seen:
                errors.append(f"disciplines: 专业代码重复 {code}")
            seen.add(code)
            std = d.get("acceptance_standard")
            if std not in ACCEPTANCE_STANDARDS:
                errors.append(f"disciplines: {code} 的 acceptance_standard 非法 {std}")
        dict_rel = disc.get("dictionary")
        if dict_rel:
            dict_path = dict_rel if os.path.isabs(dict_rel) else os.path.join(ROOT_DIR, dict_rel)
            if not os.path.isfile(dict_path):
                errors.append(f"disciplines.dictionary 指向的文件不存在：{dict_path}")
    except RuntimeError as e:
        errors.append(str(e))

    return errors


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="契约与配置自检")
    parser.add_argument("--check", action="store_true", help="全量自检")
    parser.add_argument("--config", help="打印指定配置")
    parser.add_argument("--validate", help="校验 JSON 文件")
    parser.add_argument("--schema", help="校验所用契约名")
    parser.add_argument("--stamp", help="打印中间产物的 schema 版本标识")
    args = parser.parse_args(argv)

    if args.check:
        errors = check_all()
        if errors:
            for e in errors:
                print(f"[FAIL] {e}")
            return 2
        print(f"[OK] 契约自检通过：{len(CONTRACTS)} 份 Schema / "
              f"{len(CONFIG_CONTRACTS)} 份配置")
        return 0

    if args.config:
        print(json.dumps(require_config_keys(args.config), ensure_ascii=False, indent=2))
        return 0

    if args.stamp:
        print(schema_id(args.stamp))
        return 0

    if args.validate:
        if not args.schema:
            print("[ERROR] --validate 需配合 --schema", file=sys.stderr)
            return 2
        with open(args.validate, "r", encoding="utf-8") as f:
            data = json.load(f)
        errors = validate(data, args.schema)
        if errors:
            for e in errors:
                print(f"[FAIL] {e}")
            return 2
        print(f"[OK] {os.path.basename(args.validate)} 通过 {args.schema} 校验")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
