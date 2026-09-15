# CAD 图纸识别与分析技能 —— 使用说明
## 一句话介绍
把 CAD 图纸（DXF）变成**标准化、可推理**的工程数据，自动提取**专业归属、部位/空间、工序类型、材料信息**四大工程语义要素。
## 系统架构
```
DXF 图纸
   ↓ ① 接入层    DWG自动转换 + 图框检测、图幅识别、图签提取
   ↓ ② 解析层    矢量解析 + 专业识别 + 图例解析 + CIO 契约
   ↓ ③ 识别层    部位识别 + 工序识别 + 材料识别
   ↓ ④ 语义层    规则引擎 + 知识图谱
   ↓ ⑤ 应用层    工程量清单 / 审图报告 / 材料清单 / 问答
```
## 十大能力
### 基础能力
| 模块 | 能力 |
|---|---|
| `cad_inspect.py` | 图纸概览、图层清单、实体统计、图块统计、文字提取、标注统计、规范检查 |
| `cad_analyze.py` | 专业识别、图例解析、跨图层关联、工程量汇总（含 Excel） |
| `cad_cio.py` | CIO 标准数据契约转换 + Schema 校验 |
| `cad_rules.py` | YAML 规则引擎审图（16 个算子） |
| `cad_graph.py` | 知识图谱构建 + 多跳推理问答 |
| `cad_render.py` | 图纸渲染预览 |
| `cad_convert.py` | DWG 自动转换（探测 ODA/LibreDWG 并自动调用） |
### 识别引擎（本次新增）
| 模块 | 能力 |
|---|---|
| `cad_space.py` | 📋 图框检测（图幅规格+比例+图签）、🏠 封闭区域识别（房间）、🔗 构件空间归属、📐 标高识别 |
| `cad_process.py` | 🔨 工序识别（构件→工序映射 + 施工顺序推理） |
| `cad_material.py` | 🧱 材料识别（8 大类材料提取 + Excel 清单） |
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
```
## 支持的格式
| 格式 | 支持 |
|---|---|
| `.dxf` | ✅ 直接解析（R12 ~ R2018） |
| `.dwg` | ✅ 自动探测 ODA/LibreDWG 并转换；未装工具时给出安装指引 |
| `.pdf` / 图片 | ❌ 需用视觉识别能力 |
## 依赖
`ezdxf`、`matplotlib`、`openpyxl`、`pyyaml`、`networkx`、`jsonschema`
## 目录结构
```
cad-drawing-analyzer/
├── SKILL.md                      # 技能主文件
├── scripts/
│   ├── cad_inspect.py            # 基础解析与统计
│   ├── cad_analyze.py            # 智能分析
│   ├── cad_cio.py                # CIO 数据契约转换器
│   ├── cad_space.py              # 部位/空间识别 + 图框检测
│   ├── cad_process.py            # 工序识别
│   ├── cad_material.py           # 材料识别
│   ├── cad_rules.py              # 规则引擎
│   ├── cad_graph.py              # 知识图谱
│   ├── cad_render.py             # 图纸渲染
│   └── cad_convert.py            # DWG 自动转换
├── schemas/
│   └── cio_schema.json           # CIO 数据契约 Schema
├── rules/
│   ├── default_rules.yaml        # 审图规则库（13 条）
│   ├── process_rules.yaml        # 工序规则库（20 条）
│   └── material_dict.yaml        # 材料词典（16 类模式）
└── references/
    ├── dxf_entities.md           # DXF 实体属性速查表
    └── analysis_dimensions.md    # 分析维度与专业判断要点
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
13 项端到端测试全部通过 ✅
