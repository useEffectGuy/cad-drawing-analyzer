---
name: cad-drawing-analyzer
description: 图智通：解析 DXF/DWG/PDF 工程图纸，识别目录、专业、空间、工序、材料，统计工程量并划分分部分项，输出 Excel/Word/PDF 报告与审图问答。触发词：CAD 图纸、DXF、DWG、施工图、竣工图、建筑/结构/机电/给排水/暖通/电气图、审图、算量、工程量、房间识别、图纸问答。
---
# 图智通（CAD 图纸识别与工程量统计分析）

> **图智通 tuzhitong** —— "图"+"智"+"通"：看图、懂图、通图。

## 一、定位与触发

面向**工程图纸识别与工程量统计分析**：十大分部（GB 50300-2013 附录 B）+ 消防专项 + 市政/公路/水利扩展标准；DXF/DWG/PDF 全格式，PDF 矢量直解、扫描兜底；Excel 分专业 sheet + 总汇总；百张以内批量。

触发：读取/识别/分析工程图纸，提取图号图名与图纸目录、分专业统计工程量、按规范划分分部分项/工序、出汇总表或审图问答。

## 二、场景路由（先查表，再执行）

```text
输入（DXF / DWG / PDF / 目录）
  └─ scripts/run_pipeline.py          ← 唯一编排入口
       ├─ standard：inventory → normalize → catalog → classify
       │              → measure / tables → wbs → aggregate
       └─ deep    ：inventory → normalize → inspect → cio
                      → space → process → material → rules
```

**默认先跑主链路**，然后只读 `work/summary.json`；阶段缺项或需单点复算时，再查单步命令。

### 2.1 主链路

| 场景 | 命令 | 产出 |
|---|---|---|
| 读整套图 / 出工程量（默认） | `python scripts/run_pipeline.py <输入> --work work` | `summary.json`、`工程量汇总统计.xlsx`、`分析报告.md` |
| 语义深链：CIO/空间/工序/材料/审图 | 同上 `--profile deep` | `<图名>_CIO.json`、`_空间识别数据.json`、`_工序识别数据.json`、`_材料识别数据.json`、`_审图结果.json` |
| 两条链一次跑完 | 同上 `--profile all` | 以上全部 + `summary.json` |
| 断点续跑 / 强制重算 / 只跑指定阶段 | `--resume` / `--force` / `--only catalog,wbs` / `--skip tables` | 命中缓存的阶段显示 `skipped` |

### 2.2 单步命令（按需调用）

| 场景 | 命令 | 产出 |
|---|---|---|
| 输入清点（批量必做） | `python scripts/inspect_dxf.py --inventory <目录或文件...> --out work/inventory.json` | `inventory.json` |
| DWG→DXF / 探测转换器 | `python scripts/cad_convert.py <图.dwg> --out <图.dxf>`；`cad_convert.py --detect` | `.dxf` / 探测结果 |
| PDF 通道判定 / 渲染切片 | `python scripts/render_pdf.py <图.pdf> --detect-channel`；`--out work/pdf --tile --dpi 200` | 通道判定 / PNG 切片 |
| 图纸目录识别 | `python scripts/extract_catalog.py <输入> --out work/catalog.json` | `catalog.json` |
| 图签 / 图框识别 | `python scripts/detect_titleblock.py <图.dxf> --out work/meta.json` | `meta.json` |
| 专业分类 | `python scripts/classify_discipline.py work/catalog.json --out work/catalog_classified.json` | `catalog_classified.json` |
| 工程量（DXF 几何） | `python scripts/measure_dxf.py <图.dxf> --out work/quantities.json` | `quantities.json` |
| 表格提取（PDF） | `python scripts/parse_tables.py <图.pdf> --out work/tables.json --excel work/表格.xlsx` | `tables.json` / xlsx |
| PDF 几何量算（矢量线条/面积/计数） | `python scripts/measure_pdf.py <图.pdf> --out work/pdf_quantities.json --scale 100`；或 `parse_tables.py ... --geometry` 一并产出 | `pdf_quantities.json` |
| 分部分项 WBS | `python scripts/map_wbs.py work/quantities.json --catalog work/catalog_classified.json --out work/wbs.json` | `wbs.json` |
| 汇总出表 | `python scripts/aggregate.py work --out work/工程量汇总统计.xlsx --report work/分析报告.md` | xlsx + md |
| CIO 数据契约 | `python scripts/cad_cio.py <图.dxf> --out work/<图名>_CIO.json --validate` | `_CIO.json` |
| 空间 / 房间识别 | `python scripts/cad_space.py <图.dxf> --out work --json --snap 50` | `_空间识别数据.json` |
| 工序 / 材料 / 审图 | `cad_process.py`、`cad_material.py`、`cad_rules.py`（入参均为 `<图名>_CIO.json`，`--out work --json`） | `_工序识别数据.json`、`_材料识别数据.json`、`_审图结果.json` |
| 图谱问答 | `python scripts/cad_graph.py build <图名>_CIO.json --out work/graph.json`；`cad_graph.py query work/graph.json --ask "一层有多少个门"` | `graph.json` / 查询结果 |
| 预览 / 版本对比 | `python scripts/cad_render.py <图.dxf> --out preview.png`；`compare_revisions.py <旧.dxf> <新.dxf> --out work/diff.json` | PNG / `diff.json` |
| 单图快速分析 | `python scripts/cad_analyze.py <图.dxf> --out <目录> --excel` | 分析报告 + 工程量 xlsx |
| 全套交付物 | `python scripts/doc_report.py <图.dxf> --out <目录> --format both --pdf` | docx / PDF / xlsx |
| 文档二次加工 | `doc_table.py`（create/read/edit）、`doc_word.py`（create/to-pdf）、`doc_convert.py <src> --to xlsx` | 表格 / 文档 / 转换结果 |

**DWG 硬门槛**：`cad_convert.py --detect` 自动探测 ODA File Converter / LibreDWG；均缺失时输出安装指引，不硬解析 DWG 二进制。

## 三、中间产物目录（每次任务在 workspace 生成）

```text
work/
├── inventory.json / catalog.json / catalog_classified.json
├── normalized/*.dxf
├── quantities.json / tables.json / pdf_quantities.json / wbs.json
├── <图名>_CIO.json、_空间识别数据.json、_工序识别数据.json、_材料识别数据.json、_审图结果.json
├── 工程量汇总统计.xlsx / 分析报告.md
├── summary.json              # 阶段状态 + 计数 + 交付物清单，LLM 只读这个
└── .pipeline/manifest.json   # 输入/脚本指纹，--resume 用
```

## 四、关键设计决策

### 1. 图纸目录优先原则
先检索"图纸目录"表格结构化解析 → catalog.json。目录与逐张识别结果互相校验。无目录时逐张检测图框补建。

### 2. PDF 双通道读取
- **矢量 PDF**：pdfplumber 直接提取文字/线条/表格，精度最高
- **扫描 PDF**：渲染成高清切片图 → 视觉模型读图，低置信度字段标注"待人工核对"
- **矢量几何量算**：pdfplumber 读取 `page.lines / page.rects / page.curves`，统计线条长度、矩形面积与图元数量；图纸比例用 `--scale` 换算（1:100 传 100），扫描页自动跳过并标记
- **混合文件**逐张判定，逐张选通道

### 3. 分部分项划分依据
GB 50300-2013 附录 B 十大分部为主线。映射规则落在参考文档中，脚本只查表。

### 4. 专业覆盖
五个核心专业：**建筑、结构、给排水、暖通、电气**；扩展专业 **智能建筑、建筑节能、电梯、消防** 的指引已补齐（`references/disciplines/`），消防另有专项规则库 `rules/fire_rules.yaml`。每个专业独立指引文件，按需加载。

## 五、参考文档索引

| 文件 | 何时读 |
|---|---|
| `references/formats.md` | DWG/PDF 打不开、依赖缺失、格式与版本兼容判断 |
| `references/catalog.md` | 目录表缺失、图框识别效果差 |
| `references/discipline-codes.md` | 专业误判、图号前缀规则 |
| `references/quantity-rules.md` | 算量口径、扣减争议 |
| `references/gb50300-division.md` | WBS 分部/子分部/分项层级核对 |
| `references/disciplines/architecture.md` | 建筑面积、装饰装修口径 |
| `references/disciplines/structure.md` | 混凝土/模板/砌体量取 |
| `references/disciplines/plumbing.md` | 给排水专业口径 |
| `references/disciplines/hvac.md` | 暖通专业口径 |
| `references/disciplines/electrical.md` | 电气专业口径 |
| `references/disciplines/intelligent-building.md` | 智能建筑（弱电）专业口径 |
| `references/disciplines/energy-efficiency.md` | 建筑节能专业口径 |
| `references/disciplines/elevator.md` | 电梯专业口径 |
| `references/disciplines/fire-protection.md` | 消防专业口径、消防系统与验收规范 |
| `references/reporting.md` | 改 Excel/报告模板结构 |
| `references/dxf_entities.md` | 新增 DXF 实体解析 |
| `references/analysis_dimensions.md` | 扩展分析维度与判断要点 |
| `schemas/cio_schema.json` | 改 CIO 字段或校验规则 |
| `rules/discipline_dict.yaml` | 改专业词典与图层关键词 |
| `rules/gb50300_map.yaml` | 改 WBS 映射表 |
| `rules/{default_rules,process_rules,material_dict}.yaml` | 改审图/工序/材料规则 |
| `rules/fire_rules.yaml` | 改消防专项审图规则（净距/碰撞/重复图元等） |

## 六、注意事项

- **不要编造数据**。所有数字必须来自脚本实际解析结果，缺失就如实写"未提取到"。
- **大图纸控制输出量**。明细可能上万条，报告只展示前 N 条并注明"完整数据见 JSON"；LLM 优先读 `summary.json`。
- **DWG 转换是硬门槛**。脚本自动探测 ODA/LibreDWG，未找到时给出安装指引，不硬解析 DWG 二进制。
- **专业识别是概率判断**。置信度低于 50% 时提示可能是多专业综合图或图层命名不规范。
- **PDF 扫描件低置信度字段**必须标注"待人工核对"，不得当作确定数据汇总。
- **CIO 契约不可破坏**。任何模块输出都必须符合 `schemas/cio_schema.json`。
- **分部分项映射规则与代码分离**。规则在 `rules/` 与 `references/` 中维护，脚本只查表。
