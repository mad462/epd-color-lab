#!/usr/bin/env python3
"""扫一组参数，渲染出来挑最好的。

关键：**全部用实测色板渲染**（面板实际显示的颜色）。
用标称值渲染会骗人 —— 那是网页预览的乐观版本，不是屏上效果。
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preview_variants import (  # noqa: E402
    load_palette, auto_levels, panel_map, boost_sat, quantize, render,
    rgb_to_lab, DOCS, W, H,
)


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python " + os.path.basename(__file__) + " <图片路径>")
    src = sys.argv[1]
    mea, used = load_palette(os.path.join(DOCS, "palette_qual_measured.csv"))
    ls = [rgb_to_lab(mea[i])[0] for i in used]
    LMIN, LMAX = min(ls), max(ls)

    base = Image.open(src).convert("RGB")
    s = max(W / base.width, H / base.height)
    base = base.resize((int(base.width * s), int(base.height * s)), Image.LANCZOS)
    l, t = (base.width - W) // 2, (base.height - H) // 2
    base = base.crop((l, t, l + W, t + H))

    lv, lo, hi = auto_levels(base)

    variants = []

    def add(label, img):
        variants.append((label, img))

    add("原图（参考）", base)

    # 不开映射，扫明度权重 —— 看色相优先能不能把暖色救回来
    for wgt in (2.0, 1.2, 0.8, 0.5):
        add(f"无映射 w{wgt}", render(quantize(base, mea, used, wgt), mea))

    # 开映射
    pm = panel_map(base, LMIN, LMAX)
    for wgt in (2.0, 1.2, 0.8, 0.5):
        add(f"映射 w{wgt}", render(quantize(pm, mea, used, wgt), mea))

    # 映射 + 色阶，低权重
    pma = panel_map(lv, LMIN, LMAX)
    for wgt in (1.2, 0.8, 0.5):
        add(f"映射+色阶 w{wgt}", render(quantize(pma, mea, used, wgt), mea))

    # 不开映射 + 加饱和度（把暖色拉进可选范围）
    for sat in (30, 60):
        add(f"无映射 w0.8 饱和+{sat}",
            render(quantize(boost_sat(base, sat), mea, used, 0.8), mea))

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

    out = os.path.join(DOCS, "参数扫描.png")
    grid.save(out)
    print(f"{len(variants)} 个变体 -> {out}")
    print(f"色板 L* 范围 {LMIN:.1f}~{LMAX:.1f}   自动色阶 {lo}-{hi}")

    ref = rgb_to_lab_mean(base)
    print(f"\n{'变体':26s} {'dL*':>7} {'da*':>7} {'db*':>7}   ('a* 掉得多 = 红润丢失)")
    for label, im in variants:
        m = rgb_to_lab_mean(im)
        print(f"  {label:24s} {m[0]-ref[0]:+7.1f} {m[1]-ref[1]:+7.1f} {m[2]-ref[2]:+7.1f}")


def rgb_to_lab_mean(img):
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
