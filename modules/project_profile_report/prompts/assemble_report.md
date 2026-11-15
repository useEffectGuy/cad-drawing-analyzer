# 提示词增量：报告组装（PPR 模块）

> 本文件是现有 Skill 主提示词的可选增量，仅在启用 `enable_project_profile_report` 时追加。

## 输入

- `project_profile.json`：概况字段（含来源与置信度）；
- 现有工程量结果：`quantities.json`、`wbs.json`、`*_材料识别数据.json`；
- `missing_items.json`：缺项与低置信度清单；
- `config/report_sections.yaml`：章节顺序与开关；
- `templates/report_template.md`：报告模板。

## 执行要求

1. 工程量结果只读引用，不得修改、重算或覆盖原文件。
2. 按 `report_sections.yaml` 顺序组装章节，`enabled: false` 的章节整节跳过：
   报告摘要、项目概况、编制依据与图纸识别说明、工程量汇总与明细、
   主要材料设备汇总、指标分析与异常提示、缺项与复核清单、附录。
3. 报告须包含：
   - 封面信息：项目名称、报告状态、生成时间、模块版本、数据范围；
   - 概况字段表：值、来源、置信度；缺失值写“未识别（需复核）”；
   - 低置信度字段（低于 `min_confidence_for_report`）显式标注“需复核”；
   - 缺项清单表格：字段、范围、状态、严重度、原因、置信度；
   - 附录：概况 JSON、缺项清单、工程量 CSV 的说明。
4. 不得在报告中加入图纸未支持的结论、造价判断或审图意见；
   指标分析保持轻量，只做计数与异常提示。
5. 报告末尾必须注明：基于图纸自动识别，不替代正式预算、结算和审图结论。

## 输出

`report_bundle/`：

- `project_report.md`
- `project_profile.json`
- `missing_items.json`
- `engineering_quantities.csv`
- `bundle_manifest.json`
