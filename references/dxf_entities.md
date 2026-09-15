# DXF 实体属性速查表
写 CAD 分析代码时查阅。所有属性通过 `entity.dxf.<name>` 访问，几何属性通过实体方法访问。
## 目录
- [通用属性](#通用属性)
- [LINE 直线](#line-直线)
- [LWPOLYLINE 轻量多段线](#lwpolyline-轻量多段线)
- [POLYLINE 多段线](#polyline-多段线)
- [CIRCLE 圆](#circle-圆)
- [ARC 圆弧](#arc-圆弧)
- [ELLIPSE 椭圆](#ellipse-椭圆)
- [SPLINE 样条曲线](#spline-样条曲线)
- [TEXT 单行文字](#text-单行文字)
- [MTEXT 多行文字](#mtext-多行文字)
- [INSERT 块参照](#insert-块参照)
- [ATTRIB 块属性](#attrib-块属性)
- [DIMENSION 尺寸标注](#dimension-尺寸标注)
- [HATCH 填充](#hatch-填充)
- [POINT 点](#point-点)
- [常用查询模式](#常用查询模式)
## 通用属性
所有实体都具备：
```python
entity.dxftype()          # 实体类型字符串，如 "LINE"
entity.dxf.layer          # 所在图层名
entity.dxf.color          # 颜色号（256=随层，0=随块）
entity.dxf.linetype       # 线型名
entity.dxf.lineweight     # 线宽
entity.dxf.ltscale        # 线型比例
entity.dxf.paperspace     # 0=模型空间，1=图纸空间
entity.dxf.handle         # 实体句柄（唯一标识）
entity.dxf.owner          # 拥有者块记录句柄
```
## LINE 直线
```python
line.dxf.start            # 起点 Vec3
line.dxf.end              # 终点 Vec3
line.dxf.thickness        # 厚度（3D 拉伸）
line.dxf.extrusion        # 拉伸方向
# 长度计算
import math
length = math.dist((line.dxf.start.x, line.dxf.start.y),
                   (line.dxf.end.x, line.dxf.end.y))
# 或使用内置方法
length = line.dxf.start.distance(line.dxf.end)
```
## LWPOLYLINE 轻量多段线
最常用的多段线类型（R14+）。
```python
pline.dxf.elevation       # 标高
pline.dxf.const_width     # 全局宽度
pline.closed              # 是否闭合（bool）
pline.dxf.flags           # 标志位（1=闭合）
# 顶点访问
for x, y, start_width, end_width, bulge in pline.get_points():
    print(x, y)
# 仅取坐标
points = [(p[0], p[1]) for p in pline.get_points()]
# 顶点数
n = len(pline)
# 长度与面积（闭合时）
length = pline.length()   # 总长度
if pline.closed:
    area = pline.area()   # 面积（仅闭合多段线有效）
```
## POLYLINE 多段线
老式多段线（R12），结构不同于 LWPOLYLINE。
```python
polyline.is_2d_polyline   # 2D 多段线
polyline.is_3d_polyline   # 3D 多段线
polyline.is_closed        # 是否闭合
# 顶点
for vertex in polyline.vertices:
    print(vertex.dxf.location.x, vertex.dxf.location.y)
```
## CIRCLE 圆
```python
circle.dxf.center         # 圆心 Vec3
circle.dxf.radius         # 半径
circle.dxf.thickness      # 厚度
import math
circumference = 2 * math.pi * circle.dxf.radius
area = math.pi * circle.dxf.radius ** 2
```
## ARC 圆弧
```python
arc.dxf.center            # 圆心 Vec3
arc.dxf.radius            # 半径
arc.dxf.start_angle       # 起始角（度，逆时针）
arc.dxf.end_angle         # 终止角（度）
# 弧长
import math
span = (arc.dxf.end_angle - arc.dxf.start_angle) % 360
arc_length = math.radians(span) * arc.dxf.radius
```
## ELLIPSE 椭圆
```python
ellipse.dxf.center        # 中心 Vec3
ellipse.dxf.major_axis    # 长轴向量（长度=长半轴*2）
ellipse.dxf.ratio         # 短轴/长轴 比值
ellipse.dxf.start_param   # 起始参数（弧度）
ellipse.dxf.end_param     # 终止参数
major_radius = ellipse.dxf.major_axis.magnitude / 2
minor_radius = major_radius * ellipse.dxf.ratio
```
## SPLINE 样条曲线
```python
spline.control_points     # 控制点列表
spline.fit_points         # 拟合点列表
spline.dxf.degree         # 阶数
spline.closed             # 是否闭合
spline.dxf.flags          # 标志位
```
## TEXT 单行文字
```python
text.dxf.text             # 文字内容
text.dxf.insert           # 插入点 Vec3
text.dxf.align_point      # 对齐点
text.dxf.height           # 字高
text.dxf.rotation         # 旋转角（度）
text.dxf.style            # 文字样式名
text.dxf.width            # 宽度因子
text.dxf.oblique          # 倾斜角
text.dxf.halign           # 水平对齐
text.dxf.valign           # 垂直对齐
```
## MTEXT 多行文字
```python
mtext.dxf.text            # 原始文字（含格式代码）
mtext.plain_text()        # 纯文本（去除格式代码）★推荐
mtext.dxf.insert          # 插入点
mtext.dxf.char_height     # 字符高度
mtext.dxf.attachment_point # 附着点
mtext.dxf.width           # 参考矩形宽度
mtext.dxf.rotation        # 旋转角
```
`plain_text()` 会去掉 `\P`（换行）、`\f`（字体）、`{}`（分组）等格式代码，提取可读内容时优先用它。
## INSERT 块参照
```python
insert.dxf.name           # 块名
insert.dxf.insert         # 插入点 Vec3
insert.dxf.rotation       # 旋转角（度）
insert.dxf.xscale         # X 缩放
insert.dxf.yscale         # Y 缩放
insert.dxf.zscale         # Z 缩放
insert.dxf.attribs_follow # 是否跟随属性
# 属性访问
for attrib in insert.attribs:
    print(attrib.dxf.tag, attrib.dxf.text)
# 获取块定义
block_layout = insert.block()   # 返回 BlockLayout
# 递归展开虚拟实体（含嵌套块）
for virtual_entity in insert.virtual_entities():
    print(virtual_entity.dxftype())
# 变换矩阵
m = insert.matrix44()     # 4x4 变换矩阵
```
## ATTRIB 块属性
```python
attrib.dxf.tag            # 属性标签（如 "编号"）
attrib.dxf.text           # 属性值（如 "AL1"）
attrib.dxf.insert         # 插入点
attrib.dxf.height         # 字高
attrib.dxf.rotation       # 旋转角
```
## DIMENSION 尺寸标注
```python
dim.dxf.dimtype           # 标注类型码（低3位为类型）
dim.dxf.text              # 标注文字（空=使用测量值）
dim.dxf.defpoint          # 定义点
dim.dxf.defpoint2         # 定义点2
dim.dxf.defpoint3         # 定义点3
dim.dxf.text_midpoint     # 文字中点
dim.dxf.dimstyle          # 标注样式名
dim.dxf.actual_measurement # 实际测量值（部分版本）
# 获取测量值（推荐）
measurement = dim.get_measurement()
# 角度标注返回 (角度, 弧度) 元组，其他返回 float
```
标注类型码（`dimtype & 7`）：
| 码 | 类型 |
|---|---|
| 0 | 线性/旋转标注 |
| 1 | 对齐标注 |
| 2 | 角度标注 |
| 3 | 直径标注 |
| 4 | 半径标注 |
| 5 | 角度标注（3点） |
| 6 | 坐标标注 |
## HATCH 填充
```python
hatch.dxf.pattern_name    # 图案名（如 "ANSI31"）
hatch.dxf.solid_fill      # 是否实体填充
hatch.dxf.associative     # 是否关联边界
hatch.dxf.hatch_style     # 填充样式
hatch.dxf.angle           # 图案角度
hatch.dxf.scale           # 图案比例
# 边界路径
for path in hatch.paths:
    for vertex in path.vertices:
        print(vertex)
```
## POINT 点
```python
point.dxf.location        # 位置 Vec3
```
## 常用查询模式
### 按类型查询
```python
# 查询所有直线
lines = msp.query("LINE")
# 查询多种类型
curves = msp.query("LINE CIRCLE ARC")
# 按图层查询
walls = msp.query('LINE[layer=="WALL"]')
# 组合条件
doors = msp.query('INSERT[layer=="DOOR"]')
# 遍历全部
for e in msp:
    print(e.dxftype())
```
### 统计实体数量
```python
from collections import Counter
counter = Counter(e.dxftype() for e in msp)
print(counter.most_common())
```
### 获取图纸范围
```python
from ezdxf import bbox
extents = bbox.extents(msp, fast=True)
if extents.has_data:
    print(extents.extmin, extents.extmax)
    print("宽度:", extents.size.x, "高度:", extents.size.y)
```
### 遍历块定义中的实体
```python
for block in doc.blocks:
    if block.name.startswith("*"):   # 跳过 *Model_Space 等系统块
        continue
    print(f"块 {block.name}: {len(block)} 个实体")
    for e in block:
        print("  ", e.dxftype())
```
### 递归展开嵌套块
```python
def expand(insert, depth=0):
    """递归展开块参照为虚拟实体。"""
    if depth > 10:      # 防止循环引用
        return
    for ve in insert.virtual_entities():
        yield ve
        if ve.dxftype() == "INSERT":
            yield from expand(ve, depth + 1)
```
### 计算多段线总长度
```python
total = 0.0
for pline in msp.query("LWPOLYLINE"):
    total += pline.length()
print(f"多段线总长: {total}")
```
### 按图层汇总长度
```python
from collections import defaultdict
layer_len = defaultdict(float)
for e in msp:
    if e.dxftype() == "LINE":
        layer_len[e.dxf.layer] += e.dxf.start.distance(e.dxf.end)
    elif e.dxftype() == "LWPOLYLINE":
        layer_len[e.dxf.layer] += e.length()
for layer, length in sorted(layer_len.items(), key=lambda x: -x[1]):
    print(f"{layer}: {length:.2f}")