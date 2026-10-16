#!/usr/bin/env python3
"""生成和 QuickShakePic 网站导出**完全一致**的 8bit 索引 BMP。

用途：不用开浏览器就能造测试图，直接拷到 TF 卡上看板子。

格式对齐 assets/js/image-processing.js 的 exportIndexedBMP()：
  - 14 字节文件头 + 40 字节 DIB（BI_RGB，8bpp）
  - 颜色表 palette_size 项 × 4 字节（B,G,R,0）
  - 像素自下而上（正高度），行 4 字节对齐
  - pixel_data_offset = 14 + 40 + palette_size*4

因为 .act 的 64 个颜色位置 = 面板波形索引，所以 BMP 里的像素字节
**就是固件要的索引**，固件端零映射。

用法：
    python make_bmp.py 照片.jpg -o out --profile qual --dither atkinson
    python make_bmp.py 照片.jpg -o out --profile fast --dither floyd-steinberg
"""

import argparse
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quantize_48 import WIDTH, HEIGHT, fit, load_palette, rgb_to_lab  # noqa: E402

# 和网站 ERROR_DIFFUSION_MAP 一致
KERNELS = {
    "none": [],
    "floyd-steinberg": [(1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)],
    "false-floyd-steinberg": [(1, 0, 3 / 8), (0, 1, 3 / 8), (1, 1, 1 / 4)],
    "atkinson": [(1, 0, 1 / 8), (2, 0, 1 / 8), (-1, 1, 1 / 8),
                 (0, 1, 1 / 8), (1, 1, 1 / 8), (0, 2, 1 / 8)],
    "stucki": [(1, 0, 8 / 42), (2, 0, 4 / 42), (-2, 1, 2 / 42), (-1, 1, 4 / 42),
               (0, 1, 8 / 42), (1, 1, 4 / 42), (2, 1, 2 / 42), (-2, 2, 1 / 42),
               (-1, 2, 2 / 42), (0, 2, 4 / 42), (1, 2, 2 / 42), (2, 2, 1 / 42)],
    "burkes": [(1, 0, 8 / 32), (2, 0, 4 / 32), (-2, 1, 2 / 32), (-1, 1, 4 / 32),
               (0, 1, 8 / 32), (1, 1, 4 / 32), (2, 1, 2 / 32)],
    "sierra": [(1, 0, 5 / 32), (2, 0, 3 / 32), (-2, 1, 2 / 32), (-1, 1, 4 / 32),
               (0, 1, 5 / 32), (1, 1, 4 / 32), (2, 1, 2 / 32),
               (-1, 2, 2 / 32), (0, 2, 3 / 32), (1, 2, 2 / 32)],
    "sierra-lite": [(1, 0, 2 / 4), (-1, 1, 1 / 4), (0, 1, 1 / 4)],
}
DEFAULT_KERNEL = "atkinson"


def quantize(img, candidates, palette, kernel_name, serpentine=False, metric="lab"):
    """和网站一样的误差扩散量化，返回每像素索引的 bytearray。

    metric: 'lab' = D65 CIELAB + 明度权重 2 倍（48 色用这个）
            'rgb' = 原来的纯 RGB 欧氏距离（网站旧行为）
    """
    kernel = KERNELS[kernel_name]
    use_lab = (metric == "lab")
    pal_lab = [rgb_to_lab(palette[i]) for i in candidates] if use_lab else None
    w, h = img.size
    px = img.load()

    # 用浮点工作缓冲，和网站的 Float32Array 一个思路
    work = [[0.0, 0.0, 0.0] for _ in range(w * h)]
    for y in range(h):
        for x in range(w):
            work[y * w + x] = list(px[x, y])

    out = bytearray(w * h)

    def add(x, y, dr, dg, db, f):
        if 0 <= x < w and 0 <= y < h:
            c = work[y * w + x]
            c[0] += dr * f
            c[1] += dg * f
            c[2] += db * f

    for y in range(h):
        reverse = serpentine and (y % 2 == 1)
        xs = range(w - 1, -1, -1) if reverse else range(w)
        for x in xs:
            r, g, b = work[y * w + x]
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))

            best_i, best_d = candidates[0], 1e30
            if use_lab:
                L, A, B = rgb_to_lab((r, g, b))
                for k, idx in enumerate(candidates):
                    pl, pa, pb = pal_lab[k]
                    dl, da, db = L - pl, A - pa, B - pb
                    d = 4.0 * dl * dl + da * da + db * db   # 明度权重 2 倍
                    if d < best_d:
                        best_d, best_i = d, idx
            else:
                for idx in candidates:
                    pr, pg, pb = palette[idx]
                    dr, dg, db = r - pr, g - pg, b - pb
                    d = dr * dr + dg * dg + db * db
                    if d < best_d:
                        best_d, best_i = d, idx
            out[y * w + x] = best_i

            tr, tg, tb = palette[best_i]
            dr, dg, db = r - tr, g - tg, b - tb
            for (dx, dy, f) in kernel:
                add(x + (-dx if reverse else dx), y + dy, dr, dg, db, f)

    return out


def write_website_bmp(path, indices, width, height, palette):
    """按 exportIndexedBMP 的结构写 8bit 索引 BMP。"""
    palette_size = len(palette)
    row_size = ((width * 8 + 31) >> 5) << 2
    pixel_array_size = row_size * height
    palette_bytes = palette_size * 4
    pixel_offset = 14 + 40 + palette_bytes
    file_size = pixel_offset + pixel_array_size

    buf = bytearray()
    # BITMAPFILEHEADER
    buf += b"BM" + struct.pack("<IHHI", file_size, 0, 0, pixel_offset)
    # BITMAPINFOHEADER
    buf += struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0,
                       pixel_array_size, 2835, 2835, palette_size, palette_size)
    # 颜色表（B,G,R,保留字节 255）—— 和网站 exportIndexedBMP 保持逐字节一致
    for r, g, b in palette:
        buf += bytes((b, g, r, 255))
    # 像素：自下而上
    padding = row_size - width
    for y in range(height - 1, -1, -1):
        base = y * width
        buf += bytes(indices[base:base + width])
        buf += b"\x00" * padding

    assert len(buf) == file_size, (len(buf), file_size)
    with open(path, "wb") as fh:
        fh.write(buf)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--profile", choices=("qual", "fast"), default="qual")
    ap.add_argument("--palette-csv", default=None,
                    help="色值来源，默认 docs/palette_<profile>.csv。"
                         "量化请用 docs/palette_qual_measured.csv（实测原值）；"
                         "不要用「废弃_Lstar归一化色板.csv」，那会让暗部偏红")
    ap.add_argument("--metric", choices=("lab", "rgb"), default="lab",
                    help="lab = CIELAB+明度2倍（推荐）；rgb = 网站的旧行为")
    ap.add_argument("--dither", choices=tuple(KERNELS), default=DEFAULT_KERNEL)
    ap.add_argument("--fit", choices=("cover", "contain", "stretch"), default="cover")
    ap.add_argument("--serpentine", action="store_true")
    args = ap.parse_args()

    if args.palette_csv:
        import csv as _csv
        palette = [None] * 64
        candidates = []
        with open(args.palette_csv, encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                i = int(row["index"])
                palette[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
                if row.get("used") == "Y":
                    candidates.append(i)
        print(f"色板来自 {args.palette_csv}")
    else:
        palette, candidates = load_palette(args.profile)

    img = fit(Image.open(args.image).convert("RGB"), args.fit)

    print(f"色板 rgb_{args.profile}（{len(candidates)} 个可选槽），抖动 {args.dither}，度量 {args.metric}")
    idx = quantize(img, candidates, palette, args.dither, args.serpentine, args.metric)
    assert len(idx) == WIDTH * HEIGHT

    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.image))[0]

    bmp_path = os.path.join(args.outdir, f"{stem}_{args.profile}_{args.dither}_{args.metric}.bmp")
    write_website_bmp(bmp_path, idx, WIDTH, HEIGHT, palette)

    prev = Image.new("RGB", (WIDTH, HEIGHT))
    prev.putdata([palette[v] for v in idx])
    prev_path = os.path.join(args.outdir, f"{stem}_{args.profile}_{args.dither}_{args.metric}_preview.png")
    prev.save(prev_path)

    used = len(set(idx))
    print(f"用了 {used} 种颜色")
    print(f"BMP    {bmp_path}  ({os.path.getsize(bmp_path)} 字节)")
    print(f"预览   {prev_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
