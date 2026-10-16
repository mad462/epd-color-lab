#!/usr/bin/env python3
"""对比「实测色板」和「去污染色板」当量化目标时的真实上屏效果。

关键：两个变体都用**实测色板**渲染。

为什么这样才公平：不管量化器挑了哪个索引，面板显示出来的都是**实测色**
（去污染色板只是用来"挑索引"的目标，不是面板会显示的颜色）。
所以两组都按实测色渲染，看到的才是真正会上屏的画面 —— 这样比较不循环论证。
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preview_variants import (  # noqa: E402
    load_palette, auto_levels, quantize, render, rgb_to_lab, DOCS, W, H,
)


def blend_levels(img, out_lo, out_hi=247, p=0.01):
    """和网页一致的色阶：把 p~1-p 分位铺到 [out_lo, out_hi]"""
    hist = [0] * 256
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            hist[(r * 77 + g * 150 + b * 29) >> 8] += 1
    total = img.width * img.height
    acc, lo = 0, 0
    for v in range(256):
        acc += hist[v]
        if acc >= total * p:
            lo = v
            break
    acc, hi = 0, 255
    for v in range(256):
        acc += hist[v]
        if acc >= total * (1 - p):
            hi = v
            break
    if hi - lo < 8:
        lo, hi = 0, 255
    lut = [max(0, min(255, round(out_lo + (v - lo) / (hi - lo) * (out_hi - out_lo))))
           for v in range(256)]
    return img.point(lut * 3), lo, hi


def mean_lab(img):
    px = img.load()
    sl = sa = sb = 0.0
    n = img.width * img.height
    for y in range(img.height):
        for x in range(img.width):
            L, A, B = rgb_to_lab(px[x, y])
            sl += L
            sa += A
            sb += B
    return (sl / n, sa / n, sb / n)


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python " + os.path.basename(__file__) + " <图片路径>")
    src = sys.argv[1]
    mea, used = load_palette(os.path.join(DOCS, "palette_qual_measured.csv"))
    dc, _ = load_palette(os.path.join(DOCS, "palette_qual_去污染.csv"))

    base = Image.open(src).convert("RGB")
    s = max(W / base.width, H / base.height)
    base = base.resize((int(base.width * s), int(base.height * s)), Image.LANCZOS)
    l, t = (base.width - W) // 2, (base.height - H) // 2
    base = base.crop((l, t, l + W, t + H))

    variants = [("原图", base)]

    # 黑点用各自合适的值：实测版 60，去污染版 25（因为它的黑是真的黑）
    for tag, pal, black in (("实测色板 黑点60", mea, 60),
                            ("去污染 黑点25", dc, 25),
                            ("去污染 黑点60", dc, 60)):
        lv, lo, hi = blend_levels(base, black)
        idx = quantize(lv, pal, used, 1.2)
        # ★ 两组都用实测色渲染 = 面板真正显示的颜色
        variants.append((tag, render(idx, mea)))

    cols = 2
    rows = (len(variants) + cols - 1) // cols
    pad, lh = 8, 22
    grid = Image.new("RGB", (cols * W + (cols + 1) * pad,
                             rows * (H + lh) + (rows + 1) * pad), (18, 20, 24))
    d = ImageDraw.Draw(grid)
    for n, (label, im) in enumerate(variants):
        cx = pad + (n % cols) * (W + pad)
        cy = pad + (n // cols) * (H + lh + pad)
        grid.paste(im, (cx, cy))
        d.text((cx + 2, cy + H + 5), label, fill=(235, 235, 235))

    out = os.path.join(DOCS, "去污染对比.png")
    grid.save(out)
    print("-> ", out)

    ref = mean_lab(base)
    print(f"\n{'变体':22s} {'dL*':>7} {'da*':>7} {'db*':>7}   （全部用实测色渲染 = 真实上屏）")
    for label, im in variants:
        m = mean_lab(im)
        print(f"  {label:20s} {m[0]-ref[0]:+7.1f} {m[1]-ref[1]:+7.1f} {m[2]-ref[2]:+7.1f}")

    # 索引使用分布：去污染版是不是能用上更多档位？
    print("\n用到的不同颜色数：")
    for tag, pal, black in (("实测色板", mea, 60), ("去污染", dc, 25)):
        lv, _, _ = blend_levels(base, black)
        idx = quantize(lv, pal, used, 1.2)
        usedset = set(idx.get_flattened_data())
        # 去污染版挑的索引，映射回实测色后有多少种不同颜色
        cols_mea = {mea[i] for i in usedset}
        print(f"  {tag:10s} 挑了 {len(usedset):2d} 个索引 -> 上屏显示 {len(cols_mea):2d} 种不同颜色")


if __name__ == "__main__":
    main()
