#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DWG → DXF 转换脚本（工作流步骤②：格式归一）
用法:
    # 仅探测转换工具
    python convert_dwg.py --detect
    # 执行转换
    python convert_dwg.py <drawing.dwg> --out output.dxf
功能:
    1. 探测系统是否安装 DWG→DXF 转换工具（ODA File Converter / LibreDWG / Teigha）
    2. 找到工具则自动转换，返回 DXF 路径
    3. 未找到则给出明确的安装指引，不尝试硬解析 DWG 二进制
设计说明:
    DWG 是闭源二进制格式，没有可靠的纯 Python 解析方案。本模块的价值在于
    「自动探测 + 自动调用」，把人工介入降到最低；探测不到时坦诚告知用户。
"""
from __future__ import annotations
import argparse
import os
import platform
import shutil
import subprocess
import sys

# ---------------------------------------------------------------- 工具探测
def _find_oda_converter() -> str | None:
    """探测 ODA File Converter（跨平台，最可靠的 DWG→DXF 工具）。"""
    system = platform.system()
    for name in ("ODAFileConverter", "ODAFileConverter.exe"):
        p = shutil.which(name)
        if p:
            return p
    candidates = []
    if system == "Windows":
        candidates = [
            r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        ]
        for base in (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA"):
            if os.path.isdir(base):
                for sub in os.listdir(base):
                    p = os.path.join(base, sub, "ODAFileConverter.exe")
                    if os.path.isfile(p):
                        candidates.append(p)
    elif system == "Darwin":
        candidates = ["/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"]
    else:
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
    """用 ODA File Converter 转换。ODA 要求输入/输出为目录。"""
    in_dir = os.path.dirname(os.path.abspath(dwg_path))
    out_dir = os.path.dirname(os.path.abspath(out_dxf))
    os.makedirs(out_dir, exist_ok=True)
    cmd = [oda_path, in_dir, out_dir, "ACAD2018", "DXF", "0", "1", "*.DWG"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "转换超时（>300秒）"
    except Exception as e:
        return False, f"调用失败: {e}"
    base = os.path.splitext(os.path.basename(dwg_path))[0]
    produced = os.path.join(out_dir, base + ".dxf")
    if os.path.isfile(produced):
        if os.path.abspath(produced) != os.path.abspath(out_dxf):
            try:
                shutil.move(produced, out_dxf)
            except Exception:
                return True, produced
        return True, out_dxf
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
    """确保输入是 DXF。若是 DWG 则自动转换。返回可直接解析的 DXF 路径。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        return path
    if ext != ".dwg":
        raise ValueError(f"不支持的文件格式: {ext}（仅支持 .dxf / .dwg）")
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
    ap = argparse.ArgumentParser(description="DWG → DXF 自动转换（工作流步骤②）")
    ap.add_argument("dwg", nargs="?", help="DWG 文件路径")
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
            print("  1. ODA File Converter（免费，推荐）https://www.opendesign.com/guestfiles/oda_file_converter")
            print("  2. LibreDWG（开源）https://www.gnu.org/software/libredwg/")
            print("  3. 用 CAD 软件打开后另存为 DXF")
        return
    if not args.dwg:
        ap.error("请指定 DWG 文件路径，或使用 --detect 仅探测工具")
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
