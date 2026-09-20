---
name: cad-drawing-analyzer
description: 识别、阅读并分析 CAD 图纸（DXF），提取专业归属、部位/空间、工序类型、材料信息等工程语义，输出 CIO 标准数据契约、工程量统计、规则化审图、知识图谱问答。当用户提到 CAD 图纸、CAD 图、DXF、DWG、施工图、建筑图、结构图、机电图、电气图、给排水图、暖通图、平面图、立面图、剖面图、大样图、竣工图、图纸审查、审图、读图、看图、图纸解析、图纸统计、工程量统计、算量、图块统计、图层统计、房间识别、空间识别、部位识别、工序识别、施工工序、材料识别、材料清单、材料表、图框识别、图签提取、图纸专业识别、图例识别、图纸问答、构件关联查询、生成图纸分析报告、创建工程量表格、编辑文档、导出Word/PDF/Excel时，务必使用本技能。即使用户只说"帮我看看这张图纸""这张图里有什么""统计一下图纸里的门窗数量""这图是什么专业的""算一下工程量""这图有几个房间""用了什么材料""施工工序是什么"，只要涉及 CAD 图纸文件，也应使用本技能。不适用于：纯图片格式的图纸照片（应用视觉理解）、SketchUp/Revit 模型文件、3D 打印模型文件。
---
# CAD 图纸识别与分析
## 这个技能解决什么问题
CAD 图纸（DXF）本质是结构化的矢量数据库。本技能把图纸变成**标准化、可推理**的数据，覆盖工程语义四要素：**专业归属、部位/空间、工序类型、材料信息**。
## 系统架构
本技能实现「接入 → 解析 → 识别 → 语义 → 应用」五层架构中**纯 Python 可落地的完整闭环**：
```
DXF 图纸
   ↓ ① 接入层    cad_convert.py（DWG自动转换）+ cad_space.py（图框检测）
   ↓ ② 解析层    cad_inspect.py / cad_analyze.py / cad_cio.py
                 基础解析 + 专业识别 + 图例解析 + 跨图层关联 + CIO 契约
   ↓ ③ 识别层    cad_space.py / cad_process.py / cad_material.py
                 部位识别 + 工序识别 + 材料识别
   ↓ ④ 语义层    cad_rules.py / cad_graph.py
                 规则引擎 + 知识图谱
   ↓ ⑤ 应用层    doc_report.py（一键生成正式报告）+ doc_table.py / doc_word.py / doc_convert.py（文档工具箱）
                 工程量清单 / 审图报告 / 材料清单 / 问答 / Word报告 / PDF
```
## 核心工作流
### 第 0 步：确认输入文件
- **`.dxf`** — 可直接解析
- **`.dwg`** — 脚本会**自动探测**系统是否安装 ODA File Converter / LibreDWG，找到则自动转换；未找到时给出明确安装指引（不硬解析 DWG 二进制）
  ```bash
  # 单独探测转换工具
  python scripts/cad_convert.py <任意.dwg> --detect
  # 单独执行转换
  python scripts/cad_convert.py <图纸.dwg> --out <输出.dxf>
  ```
  其他脚本（cad_inspect / cad_analyze / cad_cio / cad_space / cad_render）已内置自动转换，直接传 DWG 路径即可
- **`.pdf` / 图片** — 非矢量文件，引导用户改用视觉理解能力
### 第 1 步：基础解析（必做）
```bash
python scripts/cad_inspect.py <图纸.dxf> --out <输出目录> --json
```
输出图纸"体检报告"：概览、图层清单、实体统计、图块统计、文字提取、标注统计、规范性检查。
### 第 2 步：智能分析
```bash
python scripts/cad_analyze.py <图纸.dxf> --out <输出目录> --excel
```
| 能力 | 说明 |
|---|---|
| 🏷️ 专业识别 | 图层前缀 + 图层名 + 图块名 + 文字四重加权投票 |
| 📖 图例解析 | 定位图例区，提取"符号→含义"条目 |
| 🔗 跨图层关联 | 空间邻近算法，找出图层间构件关系 |
| 📊 工程量汇总 | 图块数量、图层线长、闭合多段线面积，导出 Excel |
### 第 3 步：CIO 标准数据契约
```bash
python scripts/cad_cio.py <图纸.dxf> --out <输出目录>/cio.json --validate
```
统一的 **CadInsightObject** 结构，是所有模块间唯一数据流转契约。含 `project_meta` / `parsed_entities` / `global_context`，支持 JSON Schema 严格校验。
**分层职责**：感知层出 `geometry` → 解析层补 `attributes`+`topology` → 分析层只读追加 `validation_errors`。
**专业识别信号**（按权重）：
1. **图层前缀**（权重 5）：A=建筑、S=结构、P=给排水、M=暖通、E=电气、L=总图
2. **图层名关键词**（权重 3）
3. **图块名关键词**（权重 3）
4. **文字内容关键词**（权重 2）
### 第 4 步：部位/空间识别
```bash
python scripts/cad_space.py <图纸.dxf> --out <输出目录> --json
```
| 能力 | 说明 |
|---|---|
| 📋 图框检测 | 最大矩形检测，识别图幅规格（A0-A4 + 比例），提取图签 |
| 🏠 封闭区域检测 | 图论算法找墙线围合的最小环 → 房间/区域，计算面积 |
| 🔗 构件空间归属 | 射线法判断构件在哪个房间内 |
| 📐 标高识别 | 提取标高标注，划分楼层 |
**技术说明**：封闭区域检测采用「端点聚类 → 构建平面图 → 最小环基」思路，空间关系基于**几何计算**而非视觉推理，准确率接近 100%（对比 VLM 的 50-60%）。
`--snap` 参数控制端点吸附容差（默认 50），图纸尺度大时适当调大。
### 第 5 步：工序识别
```bash
python scripts/cad_process.py <cio.json> --rules rules/process_rules.yaml --out <输出目录>
```
基于「构件类型 × 属性特征 → 工序类型」规则库，为每个构件匹配施工工序，并按常规施工顺序（结构→砌筑→安装→调试）排列。
**关键设计**：构件本身属性里常没有材料信息（如柱图块只有编号），但材料/工艺说明以**独立文字实体**存在。因此工序识别会自动关联构件**邻近文字**（默认半径 8000）作为匹配依据。
规则库可编辑，工程师按项目工艺特点增删规则即可。
### 第 6 步：材料识别
```bash
python scripts/cad_material.py <cio.json> --dict rules/material_dict.yaml --out <输出目录> --excel
```
从图纸文字标注中提取材料信息（NER），生成材料清单。覆盖 8 大类：混凝土、钢材、管材、电缆、保温、砌体、装饰、其他。
**提取示例**：
| 原文 | 提取结果 |
|---|---|
| `C30P6防水混凝土` | 类别=混凝土，强度等级=C30，特殊类型=防水 |
| `DN100镀锌钢管` | 类别=管材，公称直径=DN100，管材=镀锌钢管 |
| `Φ12@200` | 类别=钢材，钢筋直径=Φ12，间距=200 |
| `YJV-4x25` | 类别=电缆，型号=YJV，规格=4x25 |
材料词典可扩展，在 `rules/material_dict.yaml` 加正则规则即可。
### 第 7 步：规则化审图
```bash
# 默认校验（13 条基础规则）
python scripts/cad_rules.py <cio.json> --rules rules/default_rules.yaml --out <输出目录> --json
# 启用跨专业规则（碰撞检测、专业一致性校验，共 15 条）
python scripts/cad_rules.py <cio.json> --out <输出目录> --enable-cross-discipline
```
**规则与代码分离**——工程师编辑 YAML 即可增删规则。内置 19 个算子，覆盖几何/图层/文字/图块/跨专业五类校验。
**跨专业规则默认关闭**：跨专业规则（`category: cross_discipline`）涉及专业间碰撞与一致性校验，需要项目上下文才能准确判断，默认关闭以避免误报。需要时加 `--enable-cross-discipline` 一键启用，无需手动改 YAML。
### 第 8 步：知识图谱问答
```bash
python scripts/cad_graph.py build <cio.json> --out <输出目录>/图谱.json
python scripts/cad_graph.py query <图谱.json> --ask "与配电箱相连的插座有哪些"
```
| 查询意图 | 示例 |
|---|---|
| 关联查询 | "与配电箱相连的插座有哪些" → 多跳图遍历，返回路径 |
| 构件查找 | "灯具有哪些" |
| 数量统计 | "插座的数量" |
内置中英同义词映射 + 词边界匹配。
### 第 9 步：渲染预览
```bash
python scripts/cad_render.py <图纸.dxf> --out <输出目录>/预览.png
```
### 第 10 步：一键生成正式报告（推荐）
```bash
# 生成 Word + PDF + Excel 全套交付物
python scripts/doc_report.py <图纸.dxf> --out <输出目录> --format both --pdf
```
自动跑完整流水线（解析→CIO→空间→工序→材料→审图），生成：
- **Word 报告** — 含封面、页眉页脚、页码、9 个章节、6 张表格
- **PDF 报告** — 由 Word 转换，中文正常
- **Excel 清单** — 5 个工作表（图块工程量/空间清单/工序清单/材料清单/审图问题）
### 第 11 步：文档工具箱（按需精细控制）
**表格工具箱** `doc_table.py`：
```bash
# 创建带样式的表格
python scripts/doc_table.py create --out 清单.xlsx --title "工程量清单" \
    --headers "序号,名称,规格,数量" --rows "1,门,M0921,12|2,窗,C1515,24"
# 读取现有表格
python scripts/doc_table.py read 清单.xlsx --json
# 编辑：增行/删行/改单元格/加sheet
python scripts/doc_table.py edit 清单.xlsx --add-row "3,柱,KZ1,8"
python scripts/doc_table.py edit 清单.xlsx --update-cell "B2=新名称"
python scripts/doc_table.py edit 清单.xlsx --delete-row 3
python scripts/doc_table.py edit 清单.xlsx --add-sheet "新表" --headers "A,B,C"
# CSV ↔ Excel
python scripts/doc_table.py from-csv 数据.csv --out 表格.xlsx
python scripts/doc_table.py to-csv 表格.xlsx --out 数据.csv
```
**文档工具箱** `doc_word.py`：
```bash
# 创建规范 Word 文档（封面+页眉页脚+页码）
python scripts/doc_word.py create --out 报告.docx --title "分析报告" --cover \
    --sections "一、概述|内容||二、详情|详细说明" --author "作者"
# 读取现有文档
python scripts/doc_word.py read 报告.docx --json
# 编辑：追加段落/替换文本/插入表格
python scripts/doc_word.py edit 报告.docx --append "新增内容"
python scripts/doc_word.py edit 报告.docx --replace "旧文本=新文本"
python scripts/doc_word.py edit 报告.docx --add-table "表头1,表头2|值1,值2"
# 导出 PDF
python scripts/doc_word.py to-pdf 报告.docx --out 报告.pdf
```
**格式互转** `doc_convert.py`：
```bash
python scripts/doc_convert.py 源文件 --to <md|csv|xlsx|docx|json|pdf> --out 输出
```
支持 Excel ↔ Word ↔ Markdown ↔ CSV ↔ JSON 任意互转。
### 其他输出
- **CIO 数据** — 供系统对接的标准 JSON
- **审图报告** — 规则校验结果，含问题清单与规范依据
## 关键分析维度
### 图层（Layer）—— 图纸的骨架
图层前缀是行业通用规范：A=建筑、S=结构、P=给排水、M=暖通、E=电气、L=总图。这是判定专业的**最强信号**。
### 图块（Block）—— 工程量统计的核心
**统计块参照（INSERT）数量就是统计设备/构件数量**。带属性的图块价值更高——属性里存着编号、规格型号。
### 实体分类优先级（重要）
遵循 **图块名 > 文字内容 > 图层名** 的优先级。原因：图块名最能反映构件本质，如 `SWITCH_SINGLE` 就是开关，即使它在 `ELEC_SOCKET` 图层上。关键词采用精确匹配，避免 `AXIS` 被 `SLAB` 的宽泛关键词误命中。
### 空间（Space）—— 部位识别的基础
通过墙线围合的封闭区域识别房间，用射线法判断构件归属。这是**几何计算**，比视觉推理可靠得多。
### 工序（Process）—— 施工顺序推理
构件类型 + 属性特征 → 工序序列。注意关联构件的**邻近文字**，因为材料/工艺说明常是独立文字实体。
### 材料（Material）—— 工程语义提取
材料信息散落在文字标注中，通过正则 + 命名实体提取转为结构化清单。
## 输出规范
各模块报告结构：
```
基础分析报告：概览 / 图层 / 实体统计 / 图块 / 文字 / 标注 / 规范检查 / 结论
空间识别报告：图框与图签 / 封闭区域 / 构件空间归属 / 标高与楼层
工序识别报告：识别汇总 / 工序清单（按施工顺序）/ 按构件类别统计 / 构件明细
材料识别报告：识别汇总 / 材料清单 / 材料明细
审图报告：    校验汇总 / 问题清单（按严重级别）/ 规则执行明细
```
**结论板块是加分项**：不要只罗列数据，要给出专业解读。比如"本图判定为结构专业（置信度 71%），识别出 3 个封闭区域共 780㎡，12 根框架柱，主要工序为钢筋绑扎→模板支设→混凝土浇筑→养护"。
## 文档排版规范
生成 Word 文档时遵循 GB/T 44720-2024：
- 正文：宋体小四号（12pt），1.5 倍行距，首行缩进 2 字符
- 一级标题：黑体三号（16pt）；二级标题：黑体四号（14pt）
- 页边距：上下 2.54cm，左右 3.17cm
- 页码用 `w:fldChar` + `PAGE` 域代码生成（禁止手写数字）
- 中文字体必须同时设置 `w:eastAsia`，否则中文不生效
- 表格：表头深蓝（#1F4E79）白字，隔行变色（#F2F2F2），带边框
## 参考文档
- `references/dxf_entities.md` — DXF 实体属性速查表
- `references/analysis_dimensions.md` — 分析维度与专业判断要点
- `schemas/cio_schema.json` — CIO 数据契约 JSON Schema
- `rules/default_rules.yaml` — 审图规则库
- `rules/process_rules.yaml` — 工序识别规则库
- `rules/material_dict.yaml` — 工程材料词典
## 注意事项
- **不要编造数据**。所有数字必须来自脚本实际解析结果，缺失就如实写"未提取到"。
- **大图纸控制输出量**。明细可能上万条，报告只展示前 N 条并注明"完整数据见 JSON"。
- **中文字体**。渲染图和图表中文需配置字体，脚本已内置探测；无字体时从 https://dl3.aipyaipy.com/bp/NotoSansSC-Regular.ttf 下载注册，任务结束删除。
- **DWG 转换是硬门槛**。脚本会自动探测 ODA File Converter / LibreDWG，找到则自动转换；未找到时给出明确安装指引，**不要硬解析 DWG 二进制**（DWG 是闭源格式，没有可靠的纯 Python 解析方案）。
- **专业识别是概率判断**。置信度低于 50% 时提示可能是多专业综合图或图层命名不规范。
- **CIO 契约不可破坏**。任何模块输出都必须符合 `schemas/cio_schema.json`。
- **本技能不含视觉识别通道**。YOLO/CADRNet/BFM-Net 等深度学习模型、Neo4j、VLM 推理需要 GPU 和模型服务，超出技能能力范围。扫描件/图片图纸请用视觉理解能力。