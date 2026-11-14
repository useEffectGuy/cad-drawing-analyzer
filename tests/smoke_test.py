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
from make_quantity_dxf import build as build_quantity  # noqa: E402


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
        cls.quantity_dxf = build_quantity(os.path.join(cls.tmp, "quantity.dxf"))
        cls.quantity_golden_json = os.path.join(cls.tmp, "quantity_golden_q.json")

        cls._ok("inspect_dxf.py", "--inventory", cls.tmp, "--out", cls.inventory_json)
        cls._ok("extract_catalog.py", cls.dxf, "--out", cls.catalog_json)
        cls._ok("classify_discipline.py", cls.catalog_json, "--out", cls.classified_json)
        cls._ok("measure_dxf.py", cls.dxf, "--out", cls.quantity_json)
        cls._ok("measure_dxf.py", cls.quantity_dxf, "--out", cls.quantity_golden_json)
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

    # ---- Phase 0/1 追加 6 条 ----

    def test_15_quantity_fixture_golden(self):
        """工程量测试图：回归基线逐项对齐金标准，面积通道不再静默归零"""
        data = load(self.quantity_golden_json)
        expected = load(os.path.join(HERE, "fixtures", "quantity_expected.json"))
        reg = expected["regression"]

        self.assertEqual(data["summary"], reg["summary"])
        self.assertEqual(
            {b["block_name"]: b["quantity"] for b in data["block_table"]},
            reg["block_counts"])
        self.assertEqual(
            {r["layer"]: r["total_length_m"] for r in data["length_table"]},
            reg["layer_length_m"])
        self.assertEqual(
            {r["layer"]: r["total_area_m2"] for r in data["area_table"]},
            reg["layer_area_m2"])

        target = expected["target"]
        wall_area = next(r["total_area_m2"] for r in data["area_table"]
                         if r["layer"] == "A-WALL")
        self.assertEqual(wall_area, target["room_area_m2"])
        pipe_len = next(r["total_length_m"] for r in data["length_table"]
                        if r["layer"] == "P-PIPE")
        self.assertEqual(pipe_len, target["pipe_length_m"])

    def test_16_contracts_check(self):
        """schemas/ 与 config/ 全量自检通过（含跨配置一致性）"""
        result = run("contracts.py", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[OK]", result.stdout)

    def test_17_intermediate_schema_stamp(self):
        """每个中间产物必须携带 schema 版本字段"""
        cases = (
            (self.inventory_json, "tzp.inventory/1.0"),
            (self.catalog_json, "tzp.catalog/1.0"),
            (self.quantity_json, "tzp.quantities/1.0"),
            (self.wbs_json, "tzp.wbs/1.0"),
            (self.summary_json, "tzp.pipeline.summary/1.0"),
        )
        for path, expected in cases:
            self.assertEqual(load(path).get("schema"), expected,
                             f"{os.path.basename(path)} 缺少 schema={expected}")

    def test_18_config_semantics(self):
        """配置语义：扣减优先级闭合、洞口阈值两处一致、市政/园林不套 GB50300"""
        import contracts

        units = contracts.require_config_keys("units")
        self.assertEqual(units["length"]["output"], "m")
        self.assertEqual(units["precision"]["area_m2"], 3)

        th = contracts.require_config_keys("thresholds")
        dp = contracts.require_config_keys("deduction_priority")
        self.assertEqual(th["deduction"]["opening_min_area_m2"],
                         dp["opening_rule"]["min_area_m2"])
        self.assertEqual(dp["order"], ["基础", "柱", "梁", "墙", "板"])
        for member, rule in dp["rules"].items():
            for dep in rule.get("deduct") or []:
                self.assertGreater(dp["order"].index(dep), -1,
                                   f"{member}.deduct 引用了未登记构件 {dep}")

        disc = contracts.require_config_keys("disciplines")
        by_code = {d["code"]: d for d in disc["disciplines"]}
        self.assertEqual(len(by_code), len(disc["disciplines"]), "专业代码重复")
        self.assertEqual(by_code["MUNI"]["acceptance_standard"], "MUNICIPAL")
        self.assertEqual(by_code["LAND"]["acceptance_standard"], "LANDSCAPE")
        self.assertEqual(by_code["PLUMB"]["boq_profession"], "03")

    def test_19_contract_validation(self):
        """正式产物契约：合法样本通过，缺特征/非法来源级别/无证据被拒"""
        import contracts

        chain = {
            "discipline": {"code": "STRU", "name": "结构"},
            "unit_work": {"name": "主体结构工程"},
            "division": {"name": "主体结构", "gb50300_code": "03"},
            "subitem": {"name": "混凝土"},
            "boq": {"project_code": "010502001", "project_name": "矩形柱",
                    "feature_template": "混凝土强度等级:{grade}", "unit": "m3"},
            "acceptance_standard": "GB50300",
            "confidence": 0.9,
            "evidence": [{"type": "LAYER", "ref": "S-COLUMN"}],
        }
        line = {
            "line_id": "Q0001",
            "label_chain": chain,
            "project_code": "010502001",
            "name": "矩形柱",
            "feature": "C30；500×500",
            "unit": "m3",
            "quantity": 1.25,
            "source_level": "DIRECT",
            "evidence": [{"type": "ENTITY", "ref": "建施-01:E000123"}],
            "confidence": 1.0,
            "formula": "0.5×0.5×5.0",
        }
        self.assertEqual(contracts.validate(line, "quantity_line"), [])

        bad_level = dict(line, source_level="GUESS")
        self.assertTrue(contracts.validate(bad_level, "quantity_line"))
        no_evidence = dict(line, evidence=[])
        self.assertTrue(contracts.validate(no_evidence, "quantity_line"))
        empty_feature = dict(line, feature="")
        self.assertTrue(contracts.validate(empty_feature, "quantity_line"),
                        "项目特征不得为空串")

        entity = {"entity_id": "建施-01:E1", "dxf_type": "LWPOLYLINE",
                  "layer": "A-WALL", "layer_role": "MEASURABLE",
                  "bbox": [0, 0, 8000, 5000], "source": "VECTOR", "confidence": 1.0}
        self.assertEqual(contracts.validate(entity, "entity"), [])

        index = {
            "schema": "tzp.drawing_index/1.0",
            "sheets": [{"sheet_id": "建施-01", "frame_bbox": [0, 0, 42000, 29700],
                        "status": "OK", "scale_ratio": 100}],
            "units": {"drawing_unit": "mm", "factor_to_mm": 1.0,
                      "calibrated_by": "INSUNITS", "confidence": 1.0},
        }
        self.assertEqual(contracts.validate(index, "drawing_index"), [])
        bad_index = json.loads(json.dumps(index))
        bad_index["sheets"][0]["status"] = "DONE"
        self.assertTrue(contracts.validate(bad_index, "drawing_index"))

    def test_20_area_engine_deterministic(self):
        """闭合多段线与 HATCH 的面积必须算得出（防回归：曾静默归零）"""
        import ezdxf
        from measure_dxf import entity_area

        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        poly = msp.add_lwpolyline([(0, 0), (8000, 0), (8000, 5000), (0, 5000)],
                                  close=True)
        hatch = msp.add_hatch()
        hatch.paths.add_polyline_path([(0, 0), (4000, 0), (4000, 3000), (0, 3000)],
                                      is_closed=True)
        self.assertAlmostEqual(entity_area(poly), 40_000_000, places=3)
        self.assertAlmostEqual(entity_area(hatch), 12_000_000, places=3)

    # ---- Phase 2 追加 3 条 ----

    def test_21_unit_scale_calibration(self):
        """1:100 / A3 / INSUNITS=mm → MODEL_1_TO_1；无证据 → UNKNOWN + PENDING_ASSUMED"""
        import ezdxf

        from entity_pool import DXFEntityPool
        from unit_scale import calibrate

        result = calibrate(DXFEntityPool(self.quantity_dxf))
        expected = load(os.path.join(HERE, "fixtures", "quantity_expected.json"))
        for key, value in expected["regression"]["scale"].items():
            if key == "factor_to_mm":
                self.assertAlmostEqual(result[key], value, places=6)
            else:
                self.assertEqual(result[key], value, f"scale.{key} 不符")
        bbox = result["frame_bbox"]
        self.assertAlmostEqual(bbox[2] - bbox[0], 42000, places=3)
        self.assertAlmostEqual(bbox[3] - bbox[1], 29700, places=3)
        self.assertEqual(result["pending"], [])

        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 0  # 无单位：必须按 mm 假定并标记 PENDING
        doc.modelspace().add_line((0, 0), (1000, 0),
                                  dxfattribs={"layer": "A-WALL"})
        path = os.path.join(self.tmp, "no_scale.dxf")
        doc.saveas(path)
        unknown = calibrate(path)
        self.assertEqual(unknown["geometry_state"], "UNKNOWN")
        self.assertEqual(unknown["status"], "PENDING_ASSUMED")
        self.assertEqual(unknown["factor_to_mm"], 1.0)
        self.assertTrue(unknown["pending"])

    def test_22_frame_not_measured(self):
        """图框只做比例校准依据，不得进入线长/面积量算"""
        data = load(self.quantity_golden_json)
        self.assertNotIn("A-FRAME", [r["layer"] for r in data["length_table"]])
        self.assertNotIn("A-FRAME", [r["layer"] for r in data["area_table"]])
        self.assertIn("A-FRAME", data["excluded"]["FRAME"]["layers"])
        self.assertEqual(data["layer_roles"]["A-FRAME"], "FRAME")
        self.assertEqual(data["layer_roles"]["A-WALL"], "MEASURABLE")
        self.assertEqual(data["scale"]["geometry_state"], "MODEL_1_TO_1")

    def test_23_unknown_layer_not_silently_dropped(self):
        """未识别图层必须进 unclassified 与 pending，不得静默丢弃或混入量算"""
        import ezdxf

        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 4
        doc.layers.add("MYSTERY-LAYER")
        msp = doc.modelspace()
        msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "MYSTERY-LAYER"})
        msp.add_line((0, 0), (2000, 0), dxfattribs={"layer": "A-WALL"})
        path = os.path.join(self.tmp, "unknown_layer.dxf")
        doc.saveas(path)

        out = os.path.join(self.tmp, "unknown_layer_q.json")
        self._ok("measure_dxf.py", path, "--out", out)
        data = load(out)
        self.assertEqual(data["layer_roles"]["MYSTERY-LAYER"], "UNKNOWN")
        self.assertEqual([r["layer"] for r in data["length_table"]], ["A-WALL"])
        self.assertEqual(data["length_table"][0]["total_length_m"], 2.0)
        self.assertEqual(data["unclassified"]["entity_count"], 1)
        self.assertIn("MYSTERY-LAYER", data["unclassified"]["layers"])
        self.assertTrue(data["pending"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
