# 提示词增量：项目概况抽取（PPR 模块）

> 本文件是现有 Skill 主提示词的可选增量，仅在启用 `enable_project_profile_report` 时追加。
> 不改变 CAD 解析、图块识别与工程量提取规则。

## 触发条件

- 已存在图纸解析结果（图签、图纸目录、总说明文本、图纸分类、图号/版次）；
- 已存在工程量提取结果（专业、构件、单位、数量、来源图号）；
- 模块开关为 `true`。

## 执行要求

1. 只读取既有解析结果与工程量结果，不重新解析 CAD、不重算工程量。
2. 按 `config/profile_fields_common.yaml` 抽取通用字段，按
   `config/profile_fields_by_discipline.yaml` 加载已识别专业的扩展字段。
3. 字段匹配以图签/总说明中的“标签：值”为准，别名按配置顺序优先；
   无标签时只用 `value_pattern` 做确定性匹配。
4. 每个字段必须输出 `value`、`source`、`confidence`：
   - `source.type` 取值：`DERIVED` / `TITLE_BLOCK` / `TEXT` / `CATALOG` / `CIO_TEXT` / `UNKNOWN`；
   - `source.ref` 记录来源文件或图层，无法确定则留空。
5. 未检索到的字段一律 `null`，禁止依据经验、常识或同类项目补值。
6. 未识别到专业时，只输出通用概况，不得猜测专业。

## 输出

- `project_profile.json`，schema 为 `tzp.project_profile/1.0`；
- 字段结构：`common{field_id:{name,value,source,confidence}}`、
  `by_discipline{CODE:{name,fields{...}}}`；
- 统计：字段总数、已填、缺失、低置信度。
