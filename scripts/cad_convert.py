#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DWG 自动转换模块 —— 探测系统转换工具并自动将 DWG 转为 DXF
用法:
    # 作为独立脚本
    python cad_convert.py <drawing.dwg> [--out output.dxf]
    # 作为模块被其他脚本调用
    from cad_convert import ensure_dxf
    dxf_path = ensure_dxf("drawing.dwg")   # 返回可解析的 DXF 路径
功能:
    1. 探测系统是否安装 DWG→DXF 转换工具（ODA File Converter / LibreDWG / Teigha）
    2. 找到工具则自动转换，返回 DXF 路径
    3. 未找到则给出明确的安装指引，不尝试硬解析 DWG 二进制
设计说明:
    DWG 是闭源二进制格式，没有可靠的纯 Python 解析方案。本模块的价值在于
    「自动探测 + 自动调用」，把人工介入降到最低；探测不到时坦诚告知用户，
    而不是假装能读或产出错误结果。
"""
from __future__ import annotations
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
# ---------------------------------------------------------------- 工具探测
def _find_oda_converter() -> str | None:
    """探测 ODA File Converter（跨平台，最可靠的 DWG→DXF 工具）。"""
    system = platform.system()
    # 1. 先看 PATH 里有没有
    for name in ("ODAFileConverter", "ODAFileConverter.exe"):
        p = shutil.which(name)
        if p:
            return p
    # 2. 常见安装路径
    candidates = []
    if system == "Windows":
        candidates = [
            r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        ]
        # 扫描 ODA 目录下的版本子目录
        for base in (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA"):
            if os.path.isdir(base):
                for sub in os.listdir(base):
                    p = os.path.join(base, sub, "ODAFileConverter.exe")
                    if os.path.isfile(p):
                        candidates.append(p)
    elif system == "Darwin":
        candidates = [
            "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        ]
    else:  # Linux
        candidates = ["/usr/bin/ODAFileConverter", "/usr/local/bin/ODAFileConverter"]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None
def _find_libredwg() -> str | None:
    """探测 LibreDWG 的 dwg2dxf 工具。"""
    for name in ("dwg2dxf", "dwg2dxf.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None
def detect_converter() -> dict:
    """探测可用的 DWG→DXF 转换工具。"""
    oda = _find_oda_converter()
    if oda:
        return {"found": True, "tool": "ODA File Converter", "path": oda, "type": "oda"}
    libredwg = _find_libredwg()
    if libredwg:
        return {"found": True, "tool": "LibreDWG (dwg2dxf)", "path": libredwg, "type": "libredwg"}
    return {"found": False, "tool": None, "path": None, "type": None}
# ---------------------------------------------------------------- 转换执行
def convert_with_oda(oda_path: str, dwg_path: str, out_dxf: str) -> tuple:
    """用 ODA File Converter 转换。
    ODA 的命令行接口要求输入/输出为目录，它会批量转换目录下所有 DWG。
    """
    in_dir = os.path.dirname(os.path.abspath(dwg_path))
    out_dir = os.path.dirname(os.path.abspath(out_dxf))
    os.makedirs(out_dir, exist_ok=True)
    # ODA 参数：输入目录 输出目录 输出版本 输出类型 递归 审计 过滤
    # 版本 ACAD2018，类型 DXF，不递归，审计=1，过滤=*.DWG
    cmd = [oda_path, in_dir, out_dir, "ACAD2018", "DXF", "0", "1", "*.DWG"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "转换超时（>300秒）"
    except Exception as e:
        return False, f"调用失败: {e}"
    # ODA 转换后文件名同源，扩展名变 .dxf
    base = os.path.splitext(os.path.basename(dwg_path))[0]
    produced = os.path.join(out_dir, base + ".dxf")
    if os.path.isfile(produced):
        if os.path.abspath(produced) != os.path.abspath(out_dxf):
            try:
                shutil.move(produced, out_dxf)
            except Exception:
                return True, produced   # 移动失败但文件已生成，返回原路径
        return True, out_dxf
    # 有时 ODA 输出大写扩展名
    alt = os.path.join(out_dir, base + ".DXF")
    if os.path.isfile(alt):
        return True, alt
    return False, f"转换完成但未找到输出文件（stderr: {r.stderr[:200]}）"
def convert_with_libredwg(tool_path: str, dwg_path: str, out_dxf: str) -> tuple:
    """用 LibreDWG 的 dwg2dxf 转换。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_dxf)), exist_ok=True)
    cmd = [tool_path, "-o", out_dxf, dwg_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "转换超时（>300秒）"
    except Exception as e:
        return False, f"调用失败: {e}"
    if os.path.isfile(out_dxf):
        return True, out_dxf
    return False, f"转换失败（stderr: {r.stderr[:200]}）"
# ---------------------------------------------------------------- 对外接口
def ensure_dxf(path: str, out_dir: str | None = None) -> str:
    """确保输入是 DXF。若是 DWG 则自动转换。
    返回可直接解析的 DXF 路径；无法转换时抛出异常并给出明确指引。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        return path
    if ext != ".dwg":
        raise ValueError(f"不支持的文件格式: {ext}（仅支持 .dxf / .dwg）")
    # 探测转换工具
    conv = detect_converter()
    if not conv["found"]:
        raise RuntimeError(
            "DWG 是闭源二进制格式，无法直接解析。请先转换为 DXF：\n"
            "  方案一（推荐）：安装 ODA File Converter（免费）\n"
            "    https://www.opendesign.com/guestfiles/oda_file_converter\n"
            "  方案二：安装 LibreDWG（开源）\n"
            "    https://www.gnu.org/software/libredwg/\n"
            "  方案三：用 AutoCAD / 中望CAD / 浩辰CAD 打开后「另存为」DXF\n"
            "安装后重新运行即可自动转换。"
        )
    # 执行转换
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))
    base = os.path.splitext(os.path.basename(path))[0]
    out_dxf = os.path.join(out_dir, base + ".dxf")
    if conv["type"] == "oda":
        ok, result = convert_with_oda(conv["path"], path, out_dxf)
    else:
        ok, result = convert_with_libredwg(conv["path"], path, out_dxf)
    if not ok:
        raise RuntimeError(f"DWG 转换失败：{result}")
    return result
# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="DWG → DXF 自动转换")
    ap.add_argument("dwg", help="DWG 文件路径")
    ap.add_argument("--out", default=None, help="输出 DXF 路径")
    ap.add_argument("--detect", action="store_true", help="仅探测转换工具，不执行转换")
    args = ap.parse_args()
    if args.detect:
        conv = detect_converter()
        print("===== DWG 转换工具探测 =====")
        if conv["found"]:
            print(f"[OK] 找到: {conv['tool']}")
            print(f"     路径: {conv['path']}")
        else:
            print("[MISS] 未找到 DWG→DXF 转换工具")
            print("\n安装建议：")
            print("  1. ODA File Converter（免费，推荐）")
            print("     https://www.opendesign.com/guestfiles/oda_file_converter")
            print("  2. LibreDWG（开源）")
            print("     https://www.gnu.org/software/libredwg/")
            print("  3. 用 CAD 软件打开后另存为 DXF")
        return
    try:
        dxf = ensure_dxf(args.dwg, os.path.dirname(args.out) if args.out else None)
        if args.out and os.path.abspath(dxf) != os.path.abspath(args.out):
            shutil.move(dxf, args.out)
            dxf = args.out
        print(f"[OK] 转换完成: {dxf}")
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
if __name__ == "__main__":
    main()