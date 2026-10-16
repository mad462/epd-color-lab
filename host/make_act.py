#!/usr/bin/env python3
"""生成 QuickShakePic 用的 48 色 .act 调色板。

关键设计：**64 项，颜色放在和波形索引完全一致的位置**。

  索引 i 的颜色 = convert.js 里 rgb_qual[i] / rgb_fast[i]

这样网站量化后，8bit BMP 里的**索引字节就等于固件要的波形索引**，
固件那边零映射表，直接读字节。

.act 格式：裸 RGB 三元组，无文件头。64 项 = 192 字节。
（QuickShakePic 的 parseActPalette 按 floor(长度/3) 读色数，所以 192 字节就是 64 色。）

用法：
    python make_act.py --act-dir <导出目录>\\act
"""

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")


def load(profile, csv_path=None):
    path = csv_path or os.path.join(DOCS, f"palette_{profile}.csv")
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}")
    pal = [None] * 64
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pal[int(row["index"])] = (int(row["R"]), int(row["G"]), int(row["B"]))
    if any(p is None for p in pal):
        sys.exit("色板不完整")
    return pal


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--act-dir", required=True)
    ap.add_argument("--qual-csv", default=None,
                    help="色值来源，默认 docs/palette_qual.csv；"
                         "传 docs/palette_qual_measured.csv 就用实测值")
    ap.add_argument("--fast-csv", default=None)
    args = ap.parse_args()

    os.makedirs(args.act_dir, exist_ok=True)

    for profile, fname, csv_path in (("qual", "48-color-qual.act", args.qual_csv),
                                     ("fast", "48-color-fast.act", args.fast_csv)):
        pal = load(profile, csv_path)
        raw = bytearray()
        for r, g, b in pal:
            raw += bytes((r, g, b))
        path = os.path.join(args.act_dir, fname)
        with open(path, "wb") as fh:
            fh.write(raw)
        print(f"{fname}: {len(raw)} 字节 / {len(pal)} 色 -> {path}"
              f"{'  [实测值]' if csv_path else ''}")

        # 打印索引 -> 颜色，方便核对
        for i in range(0, 64, 8):
            row = "  ".join(
                f"{i + k:2d}:#{pal[i + k][0]:02X}{pal[i + k][1]:02X}{pal[i + k][2]:02X}"
                for k in range(8))
            print("   " + row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
