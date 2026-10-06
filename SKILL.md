---
name: cad-drawing-analyzer
description: 识别、阅读并分析 CAD 图纸（DXF/DWG/PDF），提取专业归属、部位/空间、工序类型、材料信息等工程语义，输出 CIO 标准数据契约、工程量统计、分部分项划分（GB 50300）、规则化审图、知识图谱问答。当用户提到 CAD 图纸、CAD 图、DXF、DWG、PDF 图纸、施工图、建筑图、结构图、机电图、电气图、给排水图、暖通图、平面图、立面图、剖面图、大样图、竣工图、图纸审查、审图、读图、看图、图纸解析、图纸统计、工程量统计、算量、图块统计、图层统计、房间识别、空间识别、部位识别、工序识别、施工工序、材料识别、材料清单、材料表、图框识别、图签提取、图纸目录、图纸专业识别、图例识别、分部分项、GB50300、分部工程、分项工程、图纸问答、构件关联查询、生成图纸分析报告、创建工程量表格、编辑文档、导出Word/PDF/Excel时，务必使用本技能。即使用户只说"帮我看看这张图纸""这张图里有什么""统计一下图纸里的门窗数量""这图是什么专业的""算一下工程量""这图有几个房间""用了什么材料""施工工序是什么""这套图纸有多少张""提取图纸目录"，只要涉及 CAD 图纸文件（DXF/DWG/PDF），也应使用本技能。不适用于：纯图片格式的图纸照片（应用视觉理解）、SketchUp/Revit 模型文件、3D 打印模型文件。
---
# CAD 图纸识别与工程量统计分析

## 一、Skill 总体定位

本技能面向**工程图纸识别与工程量统计分析**，核心能力：

- **十大分部全覆盖**：地基基础、主体结构、建筑装饰装修、屋面、建筑给水排水及供暖、通风与空调、建筑电气、智能建筑、建筑节能、电梯（GB 50300-2013 附录 B）
- **消防标注**：消火栓、喷淋、火灾报警等消防专项识别与统计
- **市政/公路/水利三套划分标准补充**：作为扩展标准库，按项目类型选用
- **PDF 为主通道**（DXF/DWG 保留）：矢量 PDF 直接解析，扫描 PDF 视觉兜底
- **通用 Excel 模板**：分专业 sheet + 总汇总，统一表头与样式
- **GB 50300-2013 为主标准**：分部分项/工序划分以此为准绳
- **百张以内批量**：支持整套图纸批量处理
- **ODA 已装**：DWG→DXF 自动转换

## 二、触发场景

用户要求读取/识别/分析工程图纸（DXF/DWG/PDF），识别图号图名、提取图纸目录、分专业统计工程量、按规范划分分部分项/工序并汇总。

## 三、七步核心工作流

```
① 输入清点 → ② 格式归一 → ③ 目录识别 → ④ 专业分类 → ⑤ 工程量提取 → ⑥ 分部分项映射 → ⑦ 汇总输出
```

| 步骤 | 任务 | 产出 |
|---|---|---|
| ① 输入清点 | 统计文件格式/数量/大小；大图纸制定切片策略 | 输入清单 |
| ② 格式归一 | DWG→DXF（ODA 转换）；PDF 判定矢量/扫描 | 统一解析对象 |
| ③ 目录识别 | 优先定位"图纸目录"表格并结构化；无目录则逐张图框识别补建 | catalog.json |
| ④ 专业分类 | 图号规则 + 图名关键词双重判定，打专业标签、排序 | 分类后目录 |
| ⑤ 工程量提取 | DXF 几何量算 + PDF 表格提取 + 扫描件视觉兜底 | quantities.json |
| ⑥ 分部分项映射 | 图纸内容 × GB 50300 划分标准 → 分部/子分部/分项/工序树 | wbs.json |
| ⑦ 汇总输出 | Excel（分专业 sheet + 总汇总）+ Markdown 报告 | 汇总统计表 |

### 第 0 步：确认输入文件

- **`.dxf`** — 可直接解析
- **`.dwg`** — 脚本自动探测 ODA File Converter / LibreDWG，找到则自动转换；未找到时给出安装指引
  ```bash
  python scripts/convert_dwg.py <图纸.dwg> --out <输出.dxf>
  python scripts/convert_dwg.py --detect          # 仅探测转换工具
  ```
- **`.pdf`** — 主通道。自动判定矢量/扫描，逐张选通道（矢量→pdfplumber 解析；扫描→渲染切片视觉兜底）
  ```bash
  python scripts/render_pdf.py <图纸.pdf> --out <输出目录> --dpi 200 --tile
  ```

### 第 1 步：输入清点（批量必做）

```bash
python scripts/inspect_dxf.py --inventory <目录或文件列表> --out <输出目录>/inventory.json
```
统计文件格式/数量/大小，识别超大图纸（>50MB 或实体数>5万）并制定切片策略。

### 第 2 步：格式归一

- DWG → DXF：`convert_dwg.py`（ODA 转换，ACAD2018 版本）
- PDF 判定：`render_pdf.py` 逐页检测文字层，有可提取文字→矢量通道；纯图片→扫描通道

### 第 3 步：目录识别（优先原则）

```bash
python scripts/extract_catalog.py <图纸文件或目录> --out <输出目录>/catalog.json
```
- 优先在整套图纸中检索"图纸目录/图纸索引"表格，结构化解析出（序号、图号、图名、规格、备注）
- 目录与逐张识别结果互相校验：图号对得上以目录为准，对不上的标"目录外图纸"
- 无目录时，逐张检测图框（默认右下/右上角）补建目录

### 第 4 步：专业分类

```bash
python scripts/classify_discipline.py catalog.json --out <输出目录>/catalog_classified.json
```
- 图号前缀规则（建施 J、结施 G、水施 S/水、暖施 N、电施 D、设施 F…）+ 图名关键词双重判定
- 打专业标签并按专业排序

### 第 5 步：工程量提取

```bash
# DXF 几何量算（面积/长度/计数）
python scripts/measure_dxf.py <图纸.dxf> --out <输出目录>/quantities.json
# PDF 表格提取（门窗表/材料表/设备表）
python scripts/parse_tables.py <图纸.pdf> --out <输出目录>/tables.json
```
- DXF：封闭区域面积、线长、构件（INSERT）计数、带属性块提取型号规格
- PDF 矢量：pdfplumber 提取表格
- PDF 扫描：渲染高清切片 → 视觉模型转录，低置信度字段标注"待人工核对"

### 第 6 步：分部分项映射（GB 50300）

```bash
python scripts/map_wbs.py quantities.json --standard gb50300 --out <输出目录>/wbs.json
```
映射逻辑：专业分类 → 分部 → 子分部 → 分项 → 工序
- 例：给排水 → 建筑给水排水及供暖 → 室内给水系统 → 给水管道及配件安装 → 切管/套丝/连接/试压
- 映射规则全部落在 `references/gb50300-division.md` 和 `references/disciplines/` 各文件中，脚本只查表不硬编码

### 第 7 步：汇总输出

```bash
python scripts/aggregate.py <输出目录> --out <输出目录>/工程量汇总统计.xlsx --report <输出目录>/分析报告.md
```
- Excel：分专业 sheet + 总汇总 sheet，表头/样式统一（见 `assets/report-template/`）
- Markdown 报告：图纸概览 / 目录 / 分专业工程量 / 分部分项划分 / 结论与建议

## 四、中间产物目录（每次任务在 workspace 生成）

```
work/
├── catalog.json            # 图纸目录（含图号/图名/专业/规格/版本）
├── sheets/<图号>/
│   ├── meta.json           # 单张图元数据
│   ├── entities.json       # 提取的文字/表格/图元
│   └── preview.png         # 渲染预览
├── quantities.json         # 工程量明细（分专业）
├── wbs.json                # 分部分项/工序划分树
└── 工程量汇总统计.xlsx / 分析报告.md
```

## 五、关键设计决策

### 1. 图纸目录优先原则
先检索"图纸目录"表格结构化解析 → catalog.json。目录与逐张识别结果互相校验。无目录时逐张检测图框补建。

### 2. PDF 双通道读取
- **矢量 PDF**：pdfplumber 直接提取文字/线条/表格，精度最高
- **扫描 PDF**：渲染成高清切片图 → 视觉模型读图，低置信度字段标注"待人工核对"
- **混合文件**逐张判定，逐张选通道

### 3. 分部分项划分依据
GB 50300-2013 附录 B 十大分部为主线。映射规则落在参考文档中，脚本只查表。

### 4. 专业覆盖
初版覆盖五个核心专业：**建筑、结构、给排水、暖通、电气**；智能建筑/节能/电梯/消防作为扩展位预留。每个专业独立指引文件，按需加载。

## 六、保留的增强能力（按需使用）

以下能力在原有架构中实现，可按需调用：

| 能力 | 脚本 | 说明 |
|---|---|---|
| CIO 标准数据契约 | `cad_cio.py` | CadInsightObject 统一数据结构，JSON Schema 校验 |
| 部位/空间识别 | `cad_space.py` | 图框检测、封闭区域（房间）识别、构件空间归属、标高楼层 |
| 工序识别 | `cad_process.py` | 构件类型×属性→工序，按施工顺序排列 |
| 材料识别 | `cad_material.py` | 文字标注 NER 提取材料信息，生成材料清单 |
| 规则化审图 | `cad_rules.py` | 13 条基础规则 + 15 条跨专业规则（默认关闭） |
| 知识图谱问答 | `cad_graph.py` | 构件关联查询、数量统计、多跳遍历 |
| 图纸渲染 | `cad_render.py` | DXF→PNG 预览，支持图层过滤 |
| 一键报告 | `doc_report.py` | Word+PDF+Excel 全套交付物 |
| 文档工具箱 | `doc_table.py` / `doc_word.py` / `doc_convert.py` | 表格/文档/格式互转 |

## 七、参考文档索引

| 文件 | 内容 |
|---|---|
| `references/formats.md` | DXF/DWG/PDF 格式决策矩阵、依赖与安装方案、版本兼容 |
| `references/catalog.md` | 图纸目录识别规范：版式、字段、无目录兜底流程 |
| `references/discipline-codes.md` | 图号规则与专业代码（建施/结施/水施/暖施/电施…） |
| `references/quantity-rules.md` | 工程量计算口径：面积/长度/计数/扣减规则 |
| `references/gb50300-division.md` | 分部分项划分标准（十大分部→子分部→分项映射表） |
| `references/disciplines/architecture.md` | 建筑专业：建筑面积计算规则（GB/T 50353）要点 |
| `references/disciplines/structure.md` | 结构专业：混凝土/模板/砌体量取要点 |
| `references/disciplines/plumbing.md` | 给排水专业 |
| `references/disciplines/hvac.md` | 暖通专业 |
| `references/disciplines/electrical.md` | 电气专业 |
| `references/reporting.md` | 输出报告与 Excel 模板结构说明 |
| `references/dxf_entities.md` | DXF 实体属性速查表 |
| `references/analysis_dimensions.md` | 分析维度与专业判断要点 |
| `schemas/cio_schema.json` | CIO 数据契约 JSON Schema |
| `rules/default_rules.yaml` | 审图规则库 |
| `rules/process_rules.yaml` | 工序识别规则库 |
| `rules/material_dict.yaml` | 工程材料词典 |

## 八、注意事项

- **不要编造数据**。所有数字必须来自脚本实际解析结果，缺失就如实写"未提取到"。
- **大图纸控制输出量**。明细可能上万条，报告只展示前 N 条并注明"完整数据见 JSON"。
- **DWG 转换是硬门槛**。脚本自动探测 ODA/LibreDWG，未找到时给出安装指引，不硬解析 DWG 二进制。
- **专业识别是概率判断**。置信度低于 50% 时提示可能是多专业综合图或图层命名不规范。
- **PDF 扫描件低置信度字段**必须标注"待人工核对"，不得当作确定数据汇总。
- **CIO 契约不可破坏**。任何模块输出都必须符合 `schemas/cio_schema.json`。
- **分部分项映射规则与代码分离**。规则在 `references/` 中维护，脚本只查表。
