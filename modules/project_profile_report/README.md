# PPR 模块：项目概况与报告生成

Project Profile & Report，简称 PPR。是「图智通」现有 Skill 的可插拔子模块，
只新增“概况抽取 + 报告组装”两个能力，不改变 CAD 解析、图块识别与工程量提取主流程。

## 定位与边界

| | 内容 |
|---|---|
| 负责 | 从图签、图纸目录、设计总说明、CIO 文本中抽取项目概况；字段带来源与置信度；把概况与原有工程量组装成完整报告 |
| 不负责 | 不解析 CAD、不识别图块、不计算工程量、不修改 `quantities.json` |
| 位置 | 现有流水线之后，作为可选后处理模块运行 |

```text
图纸接入 → 图纸解析 → 工程量提取
                         ↓
        PPR：概况抽取 + 报告组装（本模块）
                         ↓
       完整项目报告 + 概况 JSON + 缺项清单
```

## 用法

```powershell
# 1) 只抽概况
python modules/project_profile_report/src/profile_extractor.py work `
       --out work/project_profile.json

# 2) 只做缺项检查（输入已有概况）
python modules/project_profile_report/src/missing_item_checker.py `
       work/project_profile.json --out work/missing_items.json

# 3) 一键生成报告包（内部自动抽概况 + 缺项检查）
python modules/project_profile_report/src/report_assembler.py work `
       --out work/report_bundle

# 可选：指定已有概况 / 自定义模板
python modules/project_profile_report/src/report_assembler.py work `
       --out work/report_bundle --profile work/project_profile.json `
       --template modules/project_profile_report/templates/report_template.md
```

报告包内容：

- `project_report.md`
- `project_profile.json`
- `missing_items.json`
- `engineering_quantities.csv`
- `bundle_manifest.json`

## 开关与向后兼容

开关位于 `config/module.yaml`：

```yaml
enable_project_profile_report: true
```

- `false` 时：`profile_extractor` 打印提示并以 0 退出、不写文件；
  `report_assembler` 返回 `{"enabled": false, "files": []}` 且不创建目录。
- 现有 `run_pipeline.py` 未接入本模块，关闭开关时既有产物与测试完全不受影响。
- 函数调用同时兼容 `enable_project_profile_report: false` 与 `enabled: false` 两种写法。

## 配置

| 文件 | 作用 |
|---|---|
| `config/module.yaml` | 开关、输出文件名、置信度阈值、来源置信度、免责声明 |
| `config/profile_fields_common.yaml` | 通用概况字段与别名、`required` 标记、派生字段 |
| `config/profile_fields_by_discipline.yaml` | 十一类专业扩展字段，key 对齐 `config/disciplines.yaml` |
| `config/report_sections.yaml` | 报告章节顺序与开关 |

字段规则：

- 按 `aliases` 声明顺序在“标签：值”文本行中检索；
- 无标签时只用 `value_pattern` 做确定性匹配；
- 未检索到的字段一律 `null`，进入缺项清单，禁止臆造；
- `source.type` 置信度：`DERIVED` 0.95、`TITLE_BLOCK` 0.9、`TEXT` 0.85、
  `CATALOG` 0.8、`CIO_TEXT` 0.75、`UNKNOWN` 0.5；
- 低于 `min_confidence_for_report`（默认 0.6）的字段在报告中标注“需复核”。

## Python 接口

```python
from profile_extractor import extract_project_profile, load_work_sources
from missing_item_checker import check_missing_items
from report_assembler import assemble_project_report

sources = load_work_sources("work")
profile = extract_project_profile(sources)
missing = check_missing_items(profile)
result = assemble_project_report(profile, sources, out_dir="work/report_bundle")
```

`existing_extraction_result` 为只读字典，支持键：
`catalog`、`quantities`、`wbs`、`inventory`、`texts`、`materials`、`project_metas`、`work_dir`。

## 输入产物依赖

| 产物 | 必需 | 说明 |
|---|---|---|
| `catalog_classified.json` / `catalog.json` | 是 | 图纸张数、专业范围 |
| `quantities.json` | 建议 | 工程量汇总与附表 |
| `wbs.json` | 否 | 报告附录引用 |
| `inventory.json` | 否 | 来源文件统计 |
| `texts.json` | 否 | 外部汇总文本，格式 `{"texts":[{text, source:{type, ref}}]}` |
| `*_CIO.json` | 否 | 图签与 CIO 文本；`layer_role=FRAME` 记为 `TITLE_BLOCK` |
| `*_材料识别数据.json` | 否 | 材料设备汇总章节 |

## 测试

```powershell
python tests/smoke_test.py
```

覆盖：概况字段来源/置信度、缺项与低置信度清单、报告包文件完整性、
章节与免责声明、原有 `quantities.json` 未被修改、模块开关关闭不写文件。

## 验收对应

- 现有工程量提取结果不受影响：模块只读，测试对原文件做前后比对；
- 字段有来源与置信度，缺失进缺项清单；
- 专业字段配置化，未识别专业只输出通用概况；
- 报告含概况、工程量、缺项、附录；
- 模块可独立开关，向后兼容；
- 低置信度与缺项在报告中显式标注“需复核”。
