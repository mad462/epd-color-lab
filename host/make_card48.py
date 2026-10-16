#!/usr/bin/env python3
"""生成 48 色方案的「候选色卡」：每个候选索引一块。

比看照片更直接 —— 屏上应该出现 N 个色块，和预览图**逐块**对应。
如果屏上的块数和预览一致、顺序一致，说明
  索引 -> 两平面 -> 相序 -> 波形  整条链路都对。

用法：
    python make_card48.py -o out --profile qual
"""

import argparse
import csv
import os
import sys

from PIL import Image

WIDTH, HEIGHT = 720, 480
HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")


def load(profile):
    path = os.path.join(DOCS, f"palette_{profile}.csv")
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}，先跑 extract_palette.py")
    pal, cand = [None] * 64, []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            i = int(row["index"])
            pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
            if row["used"] == "Y":
                cand.append(i)
    return pal, cand


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--profile", choices=("qual", "fast"), default="qual")
    ap.add_argument("--cols", type=int, default=13, help="每行几块")
    args = ap.parse_args()

    pal, cand = load(args.profile)
    n = len(cand)
    rows = (n + args.cols - 1) // args.cols

    # 背景先用第 0 号候选色（通常是暗色），再铺色块
    idx = bytearray([cand[0]]) * (WIDTH * HEIGHT)
    cw = WIDTH // args.cols
    ch = HEIGHT // rows

    boxes = []
    for k, i in enumerate(cand):
        r, c = divmod(k, args.cols)
        x0, x1 = c * cw + 1, (c + 1) * cw - 2
        y0, y1 = r * ch + 1, (r + 1) * ch - 2
        for y in range(y0, y1 + 1):
            base = y * WIDTH
            for x in range(x0, x1 + 1):
                idx[base + x] = i
        boxes.append((k, i, x0, y0))

    os.makedirs(args.outdir, exist_ok=True)
    name = f"card48_{args.profile}"
    bin_path = os.path.join(args.outdir, name + ".bin8")
    with open(bin_path, "wb") as fh:
        fh.write(idx)

    prev = Image.new("RGB", (WIDTH, HEIGHT))
    prev.putdata([pal[v] for v in idx])
    prev_path = os.path.join(args.outdir, name + "_preview.png")
    prev.save(prev_path)

    print(f"候选色 {n} 个，排 {rows} 行 x {args.cols} 列")
    print(f"索引图 {bin_path}")
    print(f"预览   {prev_path}")
    print("\n块序（从左到右、从上到下）：")
    for r in range(rows):
        row = [f"{cand[r * args.cols + c] if r * args.cols + c < n else '--':>3}"
               for c in range(args.cols)]
        print("   " + " ".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
