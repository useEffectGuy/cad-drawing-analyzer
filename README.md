# 图智通 tuzhitong —— CAD 图纸识别与分析技能使用说明

> "图"+"智"+"通"：看图、懂图、通图。（工程标识：cad-drawing-analyzer）

## 一句话介绍
把 CAD 图纸（DXF）变成**标准化、可推理**的工程数据，自动提取**专业归属、部位/空间、工序类型、材料信息**四大工程语义要素。
## 本次更新（2026.10.26-v2 重构）
- **唯一编排入口** `run_pipeline.py`：`standard`（清点→目录→专业→算量/表格→WBS→汇总）与 `deep`（CIO→空间→工序→材料→审图）两条链，支持 `--resume` / `--force` / `--only` / `--skip`，产出 `summary.json` 作为 LLM 唯一读取入口。
- **公共基础设施** `cad_common.py`：DXF 工具函数、YAML 加载、Excel 样式收敛到一处，删除 20+ 脚本中的重复实现。
- **规则数据外置**：新增 `rules/discipline_dict.yaml`（专业词典唯一来源）与 `rules/gb50300_map.yaml`（WBS 映射唯一来源），代码只查表，不再与文档双源漂移。
- **PDF 矢量几何量算**：新增 `measure_pdf.py`，统计线条长度、矩形面积与图元数量，产物 `pdf_quantities.json`；`parse_tables.py --geometry` 可一并产出，且 `tables.json` 既有结构不变。
- **专业覆盖补齐**：新增智能建筑、建筑节能、电梯、消防 4 篇专业指引，以及消防专项规则库 `rules/fire_rules.yaml`（6 条规则）。
- **冒烟测试**：`tests/smoke_test.py`（26 条端到端断言）与测试用 DXF 生成器，`python tests/smoke_test.py` 即可运行，无需 pytest。
## 系统架构
```
DXF 图纸
   ↓ ① 接入层    DWG自动转换 + 图框检测、图幅识别、图签提取
   ↓ ② 解析层    矢量解析 + 专业识别 + 图例解析 + CIO 契约
   ↓ ③ 识别层    部位识别 + 工序识别 + 材料识别
   ↓ ④ 语义层    规则引擎 + 知识图谱
   ↓ ⑤ 应用层    工程量清单 / 审图报告 / 材料清单 / 问答 / Word·PDF·Excel 一键报告
```
## 核心能力
### 编排与基础设施
| 模块 | 能力 |
|---|---|
| `run_pipeline.py` | 唯一编排入口：`standard` / `deep` / `all` 三种 profile，断点续跑，输出 `summary.json` |
| `cad_common.py` | 公共基础设施：DXF 属性读取、YAML 加载、专业词典查询、Excel 样式 |
### 解析与算量
| 模块 | 能力 |
|---|---|
| `inspect_dxf.py` | 输入清点（文件数、格式、图层、图框概览） |
| `extract_catalog.py` | 图纸目录表结构化提取 |
| `classify_discipline.py` | 按图号/图名/图层关键词判定专业 |
| `measure_dxf.py` | DXF 几何算量（线长、面积、图块计数；按图层角色过滤 + 比例校准） |
| `entity_pool.py` | 唯一 DXF 图元读取入口，按 MEASURABLE/FRAME/ANNOTATION… 解析图层角色 |
| `unit_scale.py` | 图纸比例三级校准（$INSUNITS → 图签比例 → 图框/纸幅推断） |
| `parse_tables.py` | PDF 表格提取（可选 `--geometry` 同步几何量算） |
| `measure_pdf.py` | 矢量 PDF 长度/面积/计数，输出 `pdf_quantities.json` |
| `map_wbs.py` | GB 50300 分部分项划分，映射表来自 `rules/gb50300_map.yaml` |
| `aggregate.py` | 汇总出 Excel（分专业 sheet + 总表）与分析报告 |
### 基础能力
| 模块 | 能力 |
|---|---|
| `cad_inspect.py` | 图纸概览、图层清单、实体统计、图块统计、文字提取、标注统计、规范检查 |
| `cad_analyze.py` | 专业识别、图例解析、跨图层关联、工程量汇总（含 Excel） |
| `cad_cio.py` | CIO 标准数据契约转换 + Schema 校验 |
| `cad_rules.py` | YAML 规则引擎审图（19 个算子，13 条基础 + 2 条跨专业；另有消防专项 6 条） |
| `cad_graph.py` | 知识图谱构建 + 多跳推理问答 |
| `cad_render.py` | 图纸渲染预览 |
| `cad_convert.py` | DWG 自动转换（探测 ODA/LibreDWG 并自动调用） |
### 可选模块（可插拔，默认开关控制）
| 模块 | 能力 |
|---|---|
| `modules/project_profile_report/` | PPR 项目概况与报告生成：从图签/总说明抽取概况（带来源与置信度），组装含概况、工程量、缺项、附录的完整报告；只读复用现有产物，不重算工程量 |
### 识别引擎
| 模块 | 能力 |
|---|---|
| `cad_space.py` | 📋 图框检测（图幅规格+比例+图签）、🏠 封闭区域识别（房间）、🔗 构件空间归属、📐 标高识别 |
| `cad_process.py` | 🔨 工序识别（构件→工序映射 + 施工顺序推理） |
| `cad_material.py` | 🧱 材料识别（8 大类材料提取 + Excel 清单） |
### 文档交付
| 模块 | 能力 |
|---|---|
| `doc_report.py` | 一键流水线：解析→CIO→空间→工序→材料→审图，产出 Word + PDF + Excel 全套报告 |
| `doc_word.py` | Word 文档工具箱（创建/读取/编辑/转 PDF，符合 GB/T 44720-2024 排版） |
| `doc_table.py` | Excel 表格工具箱（增删改查、CSV 互转、多工作表） |
| `doc_convert.py` | Excel ↔ Word ↔ Markdown ↔ CSV ↔ JSON 格式互转 |
## 怎么用
### 方式一：直接对话（推荐）
- "帮我分析这张 CAD 图纸"
- "这图是什么专业的？有几个房间？"
- "统计门窗数量，导出 Excel"
- "这图用了什么材料？"
- "施工工序是什么？"
- "跟配电箱相连的插座有哪些？"
### 方式二：命令行
```bash
# 一键流水线（推荐；只读 work/summary.json）
python scripts/run_pipeline.py 图纸目录 --work work
python scripts/run_pipeline.py 图纸目录 --work work --profile deep   # 语义深链
python scripts/run_pipeline.py 图纸目录 --work work --resume         # 断点续跑
# DWG 自动转换（探测工具）
python scripts/cad_convert.py 图纸.dwg --detect
python scripts/cad_convert.py 图纸.dwg --out 输出.dxf
# 基础解析（直接传 DWG 也会自动转换）
python scripts/cad_inspect.py 图纸.dxf --out 输出目录 --json
# 智能分析
python scripts/cad_analyze.py 图纸.dxf --out 输出目录 --excel
# CIO 标准契约
python scripts/cad_cio.py 图纸.dxf --out cio.json --validate
# 部位/空间识别
python scripts/cad_space.py 图纸.dxf --out 输出目录 --json
# 工序识别
python scripts/cad_process.py cio.json --out 输出目录
# 材料识别
python scripts/cad_material.py cio.json --out 输出目录 --excel
# 规则化审图（默认 13 条；加 --enable-cross-discipline 启用跨专业规则共 15 条）
python scripts/cad_rules.py cio.json --out 输出目录
python scripts/cad_rules.py cio.json --out 输出目录 --enable-cross-discipline
# 知识图谱问答
python scripts/cad_graph.py build cio.json --out 图谱.json
python scripts/cad_graph.py query 图谱.json --ask "灯具有哪些"
# 渲染预览
python scripts/cad_render.py 图纸.dxf --out 预览.png
# 一键生成正式报告（Word + PDF + Excel 全套交付物，推荐）
python scripts/doc_report.py 图纸.dxf --out 输出目录 --format both --pdf
```
## 支持的格式
| 格式 | 支持 |
|---|---|
| `.dxf` | ✅ 直接解析（R12 ~ R2018） |
| `.dwg` | ✅ 自动探测 ODA/LibreDWG 并转换；未装工具时给出安装指引 |
| `.pdf` | ✅ 矢量页直解文字/表格/线条，另做长度面积计数；扫描页渲染切片后用视觉模型读取 |
| 图片 | ✅ 渲染切片 + 视觉识别 |
## 依赖
`ezdxf`、`networkx`、`openpyxl`、`python-docx`、`reportlab`、`PyYAML`、`matplotlib`、`jsonschema`、`pdfplumber`、`pypdfium2`

一键安装：`pip install -r requirements.txt`
## 目录结构
```
cad-drawing-analyzer/
├── SKILL.md                      # 技能主文件（主链路 + 深链路由）
├── README.md
├── requirements.txt              # Python 依赖
├── scripts/
│   ├── run_pipeline.py           # 唯一编排入口（standard / deep / all）
│   ├── contracts.py              # 契约/配置统一入口（--check 自检）
│   ├── cad_common.py             # 公共基础设施（DXF/YAML/Excel）
│   ├── inspect_dxf.py            # 输入清点
│   ├── extract_catalog.py        # 图纸目录提取
│   ├── classify_discipline.py    # 专业分类
│   ├── measure_dxf.py            # DXF 算量（图层角色过滤 + 比例校准）
│   ├── entity_pool.py            # 图元池与图层角色解析（唯一 DXF 读取入口）
│   ├── unit_scale.py             # 图纸比例三级校准
│   ├── parse_tables.py           # PDF 表格提取
│   ├── measure_pdf.py            # PDF 矢量几何量算
│   ├── map_wbs.py                # GB 50300 分部分项映射
│   ├── aggregate.py              # 汇总出 Excel / Markdown
│   ├── cad_inspect.py            # 基础解析与统计
│   ├── cad_analyze.py            # 智能分析
│   ├── cad_cio.py                # CIO 数据契约转换器
│   ├── cad_space.py              # 部位/空间识别 + 图框检测
│   ├── cad_process.py            # 工序识别
│   ├── cad_material.py           # 材料识别
│   ├── cad_rules.py              # 规则引擎（19 个算子）
│   ├── cad_graph.py              # 知识图谱
│   ├── cad_render.py             # 图纸渲染
│   ├── cad_convert.py            # DWG 自动转换
│   ├── convert_dwg.py            # 兼容薄封装
│   ├── render_pdf.py             # PDF 通道判定与切片
│   ├── detect_titleblock.py      # 图签/图框识别
│   ├── compare_revisions.py      # 版本对比
│   ├── doc_report.py             # 一键报告流水线（Word+PDF+Excel）
│   ├── doc_word.py               # Word 文档工具箱
│   ├── doc_table.py              # Excel 表格工具箱
│   └── doc_convert.py            # 文档格式互转
├── schemas/
│   ├── cio_schema.json           # CIO 数据契约 Schema
│   ├── entity.schema.json        # L0 图元
│   ├── perception_bundle.schema.json  # L1 感知结果
│   ├── label_chain.schema.json   # L2 四级标签链
│   ├── quantity_line.schema.json # L3 工程量行
│   ├── material_line.schema.json # L4 材料行
│   └── drawing_index.schema.json # L0 图纸索引
├── config/
│   ├── units.yaml                # 计量单位与精度（唯一真相源）
│   ├── thresholds.yaml           # 置信度/几何容差/洞口阈值/比例校准阈值
│   ├── deduction_priority.yaml   # 构件扣减优先级
│   └── disciplines.yaml          # 专业注册与验收体系
├── knowledge/
│   └── layer_standards/
│       └── base.yaml             # 图层角色库（MEASURABLE/FRAME/ANNOTATION…）
├── rules/
│   ├── discipline_dict.yaml      # 专业词典唯一来源
│   ├── gb50300_map.yaml          # WBS 映射唯一来源
│   ├── default_rules.yaml        # 审图规则（13 基础 + 2 跨专业）
│   ├── fire_rules.yaml           # 消防专项规则（6 条）
│   ├── process_rules.yaml        # 工序规则库（19 条）
│   └── material_dict.yaml        # 材料词典（21 条模式）
├── references/
│   ├── formats.md / catalog.md / discipline-codes.md
│   ├── quantity-rules.md / gb50300-division.md / reporting.md
│   ├── dxf_entities.md / analysis_dimensions.md
│   └── disciplines/              # 9 个专业指引（含智能建筑/节能/电梯/消防）
├── modules/
│   └── project_profile_report/   # PPR 可插拔模块：项目概况抽取 + 报告组装
│       ├── README.md             # 模块定位、接口、开关与验收
│       ├── config/               # module / 通用字段 / 专业字段 / 报告章节
│       ├── prompts/              # 提示词增量（概况抽取、报告组装）
│       ├── src/                  # profile_extractor / missing_item_checker / report_assembler
│       ├── templates/            # report_template.md
│       └── tests/sample_cases/   # 示例用例（catalog / quantities / wbs / texts）
└── tests/
    ├── smoke_test.py             # 26 条端到端冒烟断言
    └── fixtures/
        ├── make_mini_dxf.py      # 基础测试 DXF 生成器
        ├── make_quantity_dxf.py  # 工程量测试图生成器（1:100 图框/墙/门窗/梁筋/苗木）
        └── quantity_expected.json # 工程量金标准（回归基线 + 目标口径）
```
## 实测效果
### 测试一：结构平面图（本次新增能力验证）
- ✅ **图框检测**：识别出 A3 图幅（1:100），提取图号"结施-03"、图名"三层结构平面图"
- ✅ **封闭区域**：识别出 3 个房间（核心筒、办公区A、办公区B），总面积 780㎡
- ✅ **构件归属**：12 根框架柱正确归属到对应房间
- ✅ **标高识别**：4 个不同标高值
- ✅ **工序识别**：8 个构件匹配到工序，输出"钢筋绑扎→模板支设→混凝土浇筑→养护"序列
- ✅ **材料识别**：22 处材料标注，4 大类（钢材 15、混凝土 3、砌体 2、其他 2）
- ✅ **图层前缀**：S-WALL/S-COLUMN 正确判定为结构专业（置信度 70.9%）
### 测试二：电气照明平面图
- ✅ 专业识别：判定「电气」，置信度 82.1%
- ✅ 图例解析、工程量统计、CIO 契约、规则校验、知识图谱问答全部通过
### 测试三：建筑平面图
- ✅ 识别 6 樘窗、4 樘门，提取门编号和宽度
### 回归验证
- 13 项端到端测试全部通过 ✅
- `python tests/smoke_test.py -v`：26 条冒烟断言全部通过 ✅（清点 / 转换探测 / 目录 / 专业 / 算量 / WBS / Excel / 流水线 / 深链 / resume / quiet / PDF 量算 / YAML 缺失 / 文档一致性 / 工程量金标准 / 契约自检 / 版本戳 / 配置语义 / 契约校验 / 面积引擎 / 比例校准 / 图层角色 / 未识别图层 / 概况抽取 / 报告打包 / 模块开关）
- `python scripts/contracts.py --check`：7 份 Schema + 4 份配置全量自检通过 ✅
