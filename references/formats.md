# 图纸格式决策矩阵与依赖方案

## 一、格式决策矩阵

| 格式 | 解析通道 | 精度 | 依赖 | 适用场景 |
|---|---|---|---|---|
| **DXF** | ezdxf 直接解析 | ★★★★★ | ezdxf | 矢量图纸，几何量算首选 |
| **DWG** | ODA File Converter → DXF | ★★★★★ | ODA（已装）| 闭源格式，必须先转换 |
| **PDF（矢量）** | pdfplumber 文字/表格提取 | ★★★★☆ | pdfplumber | 含文字层的 PDF，表格提取首选 |
| **PDF（扫描）** | 渲染 PNG → 视觉识别 | ★★☆☆☆ | pypdfium2 + VLM | 纯图片 PDF，低置信度需人工核对 |
| **PDF（混合）** | 逐页判定，逐张选通道 | 视页而定 | pdfplumber + pypdfium2 | 部分矢量部分扫描 |

## 二、依赖安装方案

```bash
# 核心依赖
pip install ezdxf>=1.4 networkx>=3.0 openpyxl>=3.1 python-docx>=1.1 PyYAML>=6.0

# PDF 处理
pip install pdfplumber>=0.11 pypdfium2>=4.0

# 渲染
pip install matplotlib>=3.7 reportlab>=4.0

# 校验
pip install jsonschema>=4.0
```

## 三、DWG 转换方案

### ODA File Converter（推荐，已装）
- 下载地址：https://www.opendesign.com/guestfiles/oda_file_converter
- 命令行：`ODAFileConverter <输入目录> <输出目录> ACAD2018 DXF 0 1 *.DWG`
- 注意：ODA 要求输入/输出为**目录**，不接受单文件
- 版本输出：ACAD2018（兼容性最好）

### LibreDWG（备选）
- `dwg2dxf -o output.dxf input.dwg`
- 对复杂 DWG 兼容性略差

## 四、PDF 矢量/扫描判定

判定规则：单页可提取文字字符数 > 20 视为矢量页。

| 特征 | 矢量 PDF | 扫描 PDF |
|---|---|---|
| 文字层 | 有（可复制文字） | 无（文字是图片像素） |
| 表格 | 可用 pdfplumber 直接提取 | 需渲染后视觉识别 |
| 图元 | 线条是矢量对象 | 线条是栅格图像 |
| 精度 | 高 | 低（依赖渲染 DPI） |

判定脚本：`render_pdf.py --detect-channel <file.pdf>`

## 五、版本兼容注意事项

1. **DXF 版本**：ezdxf 支持 R12 到 R2018。太新的 DXF（2021+）可能需用 ODA 另存为旧版
2. **DWG 版本**：ODA File Converter 支持到最新版，输出 ACAD2018 兼容性最佳
3. **PDF 加密**：加密 PDF 无法提取文字，需先解密
4. **大图纸**：>50MB 或实体数>5万时启用切片策略，单块实体数控制在 1 万以内
