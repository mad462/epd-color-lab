#!/usr/bin/env python3
"""对比不同「选色距离度量」和「抖动算法」的量化效果。

按设备真实路径走：竖版图先在 480x720 上量化，再转 90° 成 720x480
（和固件 bmp_load_index8 的旋转一致），这样预览和屏上看到的是一样的。

用法：
    python compare_metrics.py 照片.png -o out
    python compare_metrics.py 照片.png -o out --portrait     # 走竖版+旋转路径
"""

import argparse
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quantize_48 import load_palette  # noqa: E402
from make_bmp import quantize          # noqa: E402

LAND_W, LAND_H = 720, 480
PORT_W, PORT_H = 480, 720


def fit_cover(img, w, h):
    sw, sh = img.size
    s = max(w / sw, h / sh)
    r = img.resize((max(1, round(sw * s)), max(1, round(sh * s))), Image.LANCZOS)
    left = (r.width - w) // 2
    top = (r.height - h) // 2
    return r.crop((left, top, left + w, top + h))


def rot_cw(idx, src_w, src_h):
    """索引缓冲顺时针转 90°：src(src_w x src_h) -> dst(src_h x src_w)"""
    dst_w, dst_h = src_h, src_w
    out = bytearray(dst_w * dst_h)
    for y in range(dst_h):
        base = y * dst_w
        for x in range(dst_w):
            out[base + x] = idx[(src_h - 1 - x) * src_w + y]
    return out, dst_w, dst_h


def render(idx, w, h, palette):
    im = Image.new("RGB", (w, h))
    im.putdata([palette[v] for v in idx])
    return im


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--profile", choices=("qual", "fast"), default="qual")
    ap.add_argument("--portrait", action="store_true",
                    help="走竖版路径：480x720 量化后转 90°")
    args = ap.parse_args()

    palette, candidates = load_palette(args.profile)
    src = Image.open(args.image).convert("RGB")

    if args.portrait:
        src_fit = fit_cover(src, PORT_W, PORT_H)
        variants = [
            ("RGB距离 + atkinson", "rgb", "atkinson"),
            ("Lab距离 + atkinson", "lab", "atkinson"),
            ("Lab距离 + 不抖动", "lab", "none"),
            ("Lab距离 + Floyd", "lab", "floyd-steinberg"),
        ]
    else:
        src_fit = fit_cover(src, LAND_W, LAND_H)
        variants = [
            ("RGB距离 + atkinson", "rgb", "atkinson"),
            ("Lab距离 + atkinson", "lab", "atkinson"),
            ("Lab距离 + 不抖动", "lab", "none"),
            ("Lab距离 + Floyd", "lab", "floyd-steinberg"),
        ]

    os.makedirs(args.outdir, exist_ok=True)
    panels = [("原图", src_fit)]

    for name, metric, kernel in variants:
        idx = quantize(src_fit, candidates, palette, kernel, False, metric)
        if args.portrait:
            idx, w, h = rot_cw(idx, PORT_W, PORT_H)
        else:
            w, h = LAND_W, LAND_H
        panels.append((name, render(idx, w, h, palette)))
        print(f"{name}: {len(set(idx))} 色")

    W, H = panels[1][1].size
    cols = 2
    rows = (len(panels) + 1) // 2
    gap = 12
    canvas = Image.new("RGB", (W * cols + gap, H * rows + gap), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    for i, (name, im) in enumerate(panels):
        x = (i % cols) * (W + gap)
        y = (i // cols) * (H + gap)
        canvas.paste(im, (x, y))
        d.rectangle([x + 4, y + 4, x + 260, y + 32], fill=(0, 0, 0))
        d.text((x + 12, y + 12), name, fill=(255, 255, 255))

    path = os.path.join(args.outdir, "compare_" + os.path.splitext(
        os.path.basename(args.image))[0] + ".png")
    canvas.save(path)
    print("对比图 ->", path, canvas.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
