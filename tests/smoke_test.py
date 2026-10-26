#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图智通冒烟测试 —— 纯 stdlib unittest，无 pytest 依赖。

运行：
    python tests/smoke_test.py
    python -m unittest discover -s tests -p "smoke_test.py" -v

测试数据在临时目录现场生成（tests/fixtures/make_mini_dxf.py），不写入版本库。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

from make_mini_dxf import build  # noqa: E402


def run(script: str, *args) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(SCRIPTS, script)] + [str(a) for a in args]
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )


def load(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class SmokeTest(unittest.TestCase):
    """8 条端到端断言：清点 → 目录 → 专业 → 量算 → WBS → 汇总 → 流水线。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tzp_smoke_")
        cls.dxf = build(os.path.join(cls.tmp, "mini.dxf"))
        cls.work = os.path.join(cls.tmp, "work")
        os.makedirs(cls.work, exist_ok=True)

        cls.inventory_json = os.path.join(cls.work, "inventory.json")
        cls.catalog_json = os.path.join(cls.work, "catalog.json")
        cls.classified_json = os.path.join(cls.work, "catalog_classified.json")
        cls.quantity_json = os.path.join(cls.work, "quantities.json")
        cls.wbs_json = os.path.join(cls.work, "wbs.json")
        cls.excel = os.path.join(cls.work, "工程量汇总统计.xlsx")
        cls.report = os.path.join(cls.work, "分析报告.md")
        cls.pipeline_work = os.path.join(cls.tmp, "pipeline_work")
        cls.summary_json = os.path.join(cls.pipeline_work, "summary.json")

        cls._ok("inspect_dxf.py", "--inventory", cls.tmp, "--out", cls.inventory_json)
        cls._ok("extract_catalog.py", cls.dxf, "--out", cls.catalog_json)
        cls._ok("classify_discipline.py", cls.catalog_json, "--out", cls.classified_json)
        cls._ok("measure_dxf.py", cls.dxf, "--out", cls.quantity_json)
        cls._ok("map_wbs.py", cls.quantity_json,
                "--catalog", cls.classified_json, "--out", cls.wbs_json)
        cls._ok("aggregate.py", cls.work, "--out", cls.excel, "--report", cls.report)
        cls._ok("run_pipeline.py", cls.dxf, "--work", cls.pipeline_work,
                "--profile", "standard", "--quiet")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _ok(cls, script, *args):
        result = run(script, *args)
        if result.returncode != 0:
            raise AssertionError(
                f"{script} 退出码 {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return result

    def test_01_inventory(self):
        data = load(self.inventory_json)
        self.assertGreaterEqual(data["total_files"], 1)

    def test_02_convert_detect(self):
        result = run("cad_convert.py", "--detect")
        self.assertIn(result.returncode, (0, 2), result.stderr)

    def test_03_catalog(self):
        data = load(self.catalog_json)
        self.assertTrue(data["items"])

    def test_04_classify(self):
        data = load(self.classified_json)
        self.assertTrue(data["items"])
        for item in data["items"]:
            self.assertIn("专业", item)

    def test_05_measure(self):
        data = load(self.quantity_json)
        self.assertGreater(data["summary"]["block_total"], 0)

    def test_06_wbs(self):
        data = load(self.wbs_json)
        self.assertGreater(data["division_count"], 0)

    def test_07_aggregate_excel(self):
        from openpyxl import load_workbook

        wb = load_workbook(self.excel)
        self.assertGreaterEqual(len(wb.sheetnames), 2)

    def test_08_pipeline(self):
        summary = load(self.summary_json)
        self.assertEqual(summary["failed"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
