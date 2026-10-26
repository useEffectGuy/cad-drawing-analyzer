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
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

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
    """14 条端到端断言：清点 → 目录 → 专业 → 量算 → WBS → 汇总 → 流水线 + 深链 + resume + quiet + PDF 量算 + YAML 缺失 + 文档一致性。"""

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

    # ---- Batch H 追加 6 条 ----

    def test_09_pipeline_resume(self):
        """二次 --resume 后所有已跑 stage 的 status == skipped"""
        w = os.path.join(self.tmp, "resume_work")
        self._ok("run_pipeline.py", self.dxf, "--work", w,
                 "--profile", "standard", "--quiet")
        self._ok("run_pipeline.py", self.dxf, "--work", w,
                 "--profile", "standard", "--resume", "--quiet")
        summary = load(os.path.join(w, "summary.json"))
        self.assertTrue(summary["stages"], "resume 后 summary 应有 stages")
        for e in summary["stages"]:
            self.assertEqual(
                e["status"], "skipped",
                f"{e['id']} resume 后应为 skipped，实际 {e['status']}")

    def test_10_pipeline_deep(self):
        """--profile deep 整条链 failed == []"""
        w = os.path.join(self.tmp, "deep_work")
        self._ok("run_pipeline.py", self.dxf, "--work", w,
                 "--profile", "deep", "--quiet")
        summary = load(os.path.join(w, "summary.json"))
        self.assertEqual(summary["failed"], [])

    def test_11_pipeline_quiet(self):
        """--quiet 时 stdout 只有 1 行（产物路径）"""
        w = os.path.join(self.tmp, "quiet_work")
        result = run("run_pipeline.py", self.dxf, "--work", w,
                     "--profile", "standard", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1,
                         f"--quiet 应只输出 1 行，实际 {len(lines)}:\n{result.stdout}")

    def test_12_measure_pdf_vector(self):
        """matplotlib 渲染 PDF 后 measure_pdf 能读取 page 统计"""
        pdf_path = os.path.join(self.tmp, "mini_rendered.pdf")
        self._ok("cad_render.py", self.dxf, "--out", pdf_path)
        pdf_json = os.path.join(self.tmp, "pdf_q.json")
        self._ok("measure_pdf.py", pdf_path, "--out", pdf_json)
        data = load(pdf_json)
        totals = data["totals"]
        self.assertEqual(totals["pages"], 1)
        self.assertGreaterEqual(totals["scanned_pages"], 0)
        self.assertGreaterEqual(totals["text_char_count"], 0)

    def test_13_yaml_missing(self):
        """rules/discipline_dict.yaml 缺失时 classify_discipline 抛错（非静默）"""
        yaml_path = os.path.join(ROOT, "rules", "discipline_dict.yaml")
        yaml_bak = yaml_path + ".bak"
        try:
            os.rename(yaml_path, yaml_bak)
            # 清掉 lru_cache（cad_common 加载时缓存）
            sys.path.insert(0, SCRIPTS)
            import cad_common as _cc
            if hasattr(_cc.load_discipline_dict, "cache_clear"):
                _cc.load_discipline_dict.cache_clear()
            result = run("classify_discipline.py",
                         self.catalog_json, "--out",
                         os.path.join(self.tmp, "should_fail.json"))
            self.assertNotEqual(
                result.returncode, 0,
                f"YAML 缺失时应抛错退出，实际 rc=0\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}")
        finally:
            if os.path.exists(yaml_bak):
                os.rename(yaml_bak, yaml_path)

    def test_14_markdown_yaml_consistency(self):
        """gb50300_map.yaml divisions.keys() 与 gb50300-division.md 分部集合相等"""
        yaml_path = os.path.join(ROOT, "rules", "gb50300_map.yaml")
        with open(yaml_path, encoding="utf-8") as f:
            y = yaml.safe_load(f)
        yaml_divisions = set(y["divisions"].keys())

        md_path = os.path.join(ROOT, "references", "gb50300-division.md")
        with open(md_path, encoding="utf-8") as f:
            md_text = f.read()
        # 表格格式：| 序号 | 分部工程 | 涵盖专业 |
        md_divisions = set(
            re.findall(r'^\|\s*\d+\s*\|\s*([^|]+?)\s*\|', md_text, re.MULTILINE)
        )
        self.assertEqual(
            yaml_divisions, md_divisions,
            f"YAML 分部 {sorted(yaml_divisions)} != MD 分部 {sorted(md_divisions)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
