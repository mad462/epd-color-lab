#!/usr/bin/env python3
"""色板混合扫描：实测值 <-> 标称值。

假设：实测色板系统性偏淡（所有色族都比标称淡），拿它当量化目标，
画面就会发灰、亮部只能落到黄色档位。混合一点标称值能把饱和度找回来。
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preview_variants import (  # noqa: E402
    load_palette, auto_levels, panel_map, quantize, render, rgb_to_lab, lab_to_rgb,
    DOCS, W, H,
)


def blend_palette(mea, nom, t):
    """在 Lab 里线性混合：t=0 全实测，t=1 全标称"""
    out = []
    for m, n in zip(mea, nom):
        if m is None or n is None:
            out.append(m or n)
            continue
        lm, ln = rgb_to_lab(m), rgb_to_lab(n)
        out.append(lab_to_rgb(tuple(ln[i] * t + lm[i] * (1 - t) for i in range(3))))
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python " + os.path.basename(__file__) + " <图片路径>")
    src = sys.argv[1]
    mea, used = load_palette(os.path.join(DOCS, "palette_qual_measured.csv"))
    nom, _ = load_palette(os.path.join(DOCS, "palette_qual.csv"))

    base = Image.open(src).convert("RGB")
    s = max(W / base.width, H / base.height)
    base = base.resize((int(base.width * s), int(base.height * s)), Image.LANCZOS)
    l, t0 = (base.width - W) // 2, (base.height - H) // 2
    base = base.crop((l, t0, l + W, t0 + H))

    lv, lo, hi = auto_levels(base)
    pma = panel_map(lv, 30.9, 72.1)

    variants = [("原图", base)]
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        pal = blend_palette(mea, nom, t)
        # 用混合后的色板当量化目标；渲染仍用**实测值**（= 面板真实显示）
        idx = quantize(pma, pal, used, 1.2)
        variants.append((f"混合{t:.0%}", render(idx, mea)))

    # 对照组：不开映射
    for t in (0.0, 0.5):
        pal = blend_palette(mea, nom, t)
        idx = quantize(lv, pal, used, 1.2)
        variants.append((f"无映射 混合{t:.0%}", render(idx, mea)))

    cols = 4
    rows = (len(variants) + cols - 1) // cols
    pad, lh = 8, 20
    grid = Image.new("RGB", (cols * W + (cols + 1) * pad,
                             rows * (H + lh) + (rows + 1) * pad), (18, 20, 24))
    d = ImageDraw.Draw(grid)
    for n, (label, im) in enumerate(variants):
        cx = pad + (n % cols) * (W + pad)
        cy = pad + (n // cols) * (H + lh + pad)
        grid.paste(im, (cx, cy))
        d.text((cx + 2, cy + H + 4), label, fill=(232, 232, 232))

    out = os.path.join(DOCS, "色板混合扫描.png")
    grid.save(out)
    print("-> ", out)

    ref = mean_lab(base)
    print(f"\n{'变体':18s} {'dL*':>7} {'da*':>7} {'db*':>7}   (da* 越接近 0 = 红润保得越好)")
    for label, im in variants:
        m = mean_lab(im)
        print(f"  {label:16s} {m[0]-ref[0]:+7.1f} {m[1]-ref[1]:+7.1f} {m[2]-ref[2]:+7.1f}")


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


if __name__ == "__main__":
    main()
