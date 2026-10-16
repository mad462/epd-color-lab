#!/usr/bin/env python3
"""生成固件用的色板对照表。

输出 <fw>/palette_table.c / .h，包含：
  - palette_qual[64][3] / palette_fast[64][3]      标称 RGB
  - palette_qual_active[] / palette_fast_active[]  有效槽位（校准按这个列表编号）
  - 各列表长度 *_len

固件用标称色板做两件事：
  1. 判断用户导出 BMP 时选的是哪张 .act（比对 BMP 自带颜色表）
  2. 校准模式：逐个索引刷满屏，并把标称颜色打出来给仪器对照

用法：
    python make_palette_table.py --fw-dir C:\\FUCKEDA\\e6-48color\\main
"""

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")


def load(profile, csv_path=None):
    """返回 (64 项 RGB, 有效槽位列表)"""
    path = csv_path or os.path.join(DOCS, f"palette_{profile}.csv")
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}")

    pal = [None] * 64
    active = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            i = int(row["index"])
            pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
            if row.get("used") == "Y":
                active.append(i)
    if any(p is None for p in pal):
        sys.exit("色板不完整")
    return pal, active


def emit_rgb(name, pal, fh):
    fh.write(f"const uint8_t {name}[{len(pal)}][3] = {{\n")
    for i in range(0, len(pal), 4):
        row = ", ".join("{0x%02X,0x%02X,0x%02X}" % pal[i + k] for k in range(4))
        fh.write(f"    {row},   /* {i:2d}..{i + 3:2d} */\n")
    fh.write("};\n\n")


def emit_active(name, active, fh):
    fh.write(f"const uint8_t {name}[] = {{\n")
    for i in range(0, len(active), 16):
        row = ", ".join(f"{v:2d}" for v in active[i:i + 16])
        fh.write(f"    {row},\n")
    fh.write("};\n")
    fh.write(f"const int {name}_len = {len(active)};\n\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fw-dir", required=True)
    ap.add_argument("--qual-csv", default=None,
                    help="默认 docs/palette_qual.csv；传实测 CSV 就用实测值")
    ap.add_argument("--fast-csv", default=None)
    args = ap.parse_args()

    qual, qual_active = load("qual", args.qual_csv)
    fast, fast_active = load("fast", args.fast_csv)
    print(f"qual: {len(qual_active)} 个有效槽位")
    print(f"fast: {len(fast_active)} 个有效槽位")

    os.makedirs(args.fw_dir, exist_ok=True)

    with open(os.path.join(args.fw_dir, "palette_table.h"), "w", encoding="utf-8") as fh:
        fh.write("/* 自动生成，不要手改 —— 源：docs/palette_{qual,fast}.csv */\n")
        fh.write("#ifndef PALETTE_TABLE_H\n#define PALETTE_TABLE_H\n\n")
        fh.write("#include <stdint.h>\n\n")
        fh.write("#define PALETTE_ENTRIES 64\n\n")
        fh.write("extern const uint8_t palette_qual[PALETTE_ENTRIES][3];\n")
        fh.write("extern const uint8_t palette_fast[PALETTE_ENTRIES][3];\n\n")
        fh.write("/* 有效槽位（占位槽不参与量化，校准也按这个列表编号） */\n")
        fh.write("extern const uint8_t palette_qual_active[];\n")
        fh.write("extern const int palette_qual_active_len;\n")
        fh.write("extern const uint8_t palette_fast_active[];\n")
        fh.write("extern const int palette_fast_active_len;\n\n")
        fh.write("#endif\n")

    with open(os.path.join(args.fw_dir, "palette_table.c"), "w", encoding="utf-8") as fh:
        fh.write("/* 自动生成，不要手改。\n")
        fh.write(" * 索引位置 = 面板波形索引。色值来自 FrameFilm（GPL-3.0）convert.js 数组。\n */\n\n")
        fh.write('#include "palette_table.h"\n\n')
        emit_rgb("palette_qual", qual, fh)
        emit_rgb("palette_fast", fast, fh)
        emit_active("palette_qual_active", qual_active, fh)
        emit_active("palette_fast_active", fast_active, fh)

    print(f"写出 {args.fw_dir}\\palette_table.c / .h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
