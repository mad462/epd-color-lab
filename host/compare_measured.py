#!/usr/bin/env python3
"""客观对比：用标称色板量化 vs 用实测色板量化。

两条流程都用**实测色**渲染结果（因为那才是屏上真正显示的颜色），
再和原图比 ΔE2000。谁小谁更准。

同时也出视觉对比图（Floyd 抖动版），因为抖动看的是局部平均。

用法：python compare_measured.py 图片 [-o 输出目录]
"""

import argparse
import csv
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quantize_48 import rgb_to_lab  # noqa: E402
from make_bmp import KERNELS, quantize  # noqa: E402
from analyze_cal import de2000  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")
PORT_W, PORT_H = 480, 720


def load_csv(path):
    pal = [None] * 64
    active = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            i = int(row["index"])
            pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
            if row.get("used") == "Y":
                active.append(i)
    return pal, active


def fit_cover(img, w, h):
    sw, sh = img.size
    s = max(w / sw, h / sh)
    r = img.resize((max(1, round(sw * s)), max(1, round(sh * s))), Image.LANCZOS)
    return r.crop(((r.width - w) // 2, (r.height - h) // 2,
                   (r.width - w) // 2 + w, (r.height - h) // 2 + h))


def render(idx, w, h, pal):
    im = Image.new("RGB", (w, h))
    im.putdata([pal[v] for v in idx])
    return im


def white_balance(pal, white):
    """按白点做通道增益（把面板的"白"拉到 255）。

    理由：人眼在房间里会适应，把纸白当白。绝对实测值虽然客观，
    但观感上眼睛是按「相对纸白」判断的。"""
    gains = tuple(255.0 / max(1, c) for c in white)
    out = []
    for r, g, b in pal:
        out.append((min(255, round(r * gains[0])),
                    min(255, round(g * gains[1])),
                    min(255, round(b * gains[2]))))
    return out


def adapt_to_white(im, white_src, white_ref=(255, 255, 255)):
    """把图按 white_src -> white_ref 做通道增益，模拟眼睛适应纸白之后看到的样子。

    绝对 ΔE 是不公平的：它拿原图的绝对亮度当参照，而人眼看纸白是会适应的。
    """
    luts = []
    for i in range(3):
        g = white_ref[i] / max(1, white_src[i])
        luts.append([min(255, round(v * g)) for v in range(256)])
    r, g, b = im.split()
    return Image.merge("RGB", (r.point(luts[0]), g.point(luts[1]), b.point(luts[2])))


def mean_de_vs_original(rendered, original):
    """逐像素 ΔE2000 的均值（下采样加速，取 4 万像素）"""
    total = 0
    n = 0
    step = max(1, (rendered.width * rendered.height) // 40000)
    rp, op = rendered.load(), original.load()
    for y in range(0, rendered.height, 1):
        for x in range(0, rendered.width, step):
            total += de2000(rgb_to_lab(rp[x, y]), rgb_to_lab(op[x, y]))
            n += 1
    return total / max(1, n)


def local_avg_de(rendered, original, radius=4):
    """局部平均后的 ΔE —— 抖动看的是局部平均，这个指标更贴近观感"""
    a = rendered.filter(ImageFilter.BoxBlur(radius))
    b = original.filter(ImageFilter.BoxBlur(radius))
    return mean_de_vs_original(a, b)


def adapted_de(rendered, original, panel_white, radius=4):
    """先按纸白做适应，再比 ΔE —— 更接近人眼在房间里实际看到的差别"""
    a = adapt_to_white(rendered, panel_white)
    a = a.filter(ImageFilter.BoxBlur(radius))
    b = original.filter(ImageFilter.BoxBlur(radius))
    return mean_de_vs_original(a, b)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--outdir", default=os.path.join(HERE, "..", "cmp"))
    args = ap.parse_args()

    nom, active = load_csv(os.path.join(DOCS, "palette_qual.csv"))
    mea, _ = load_csv(os.path.join(DOCS, "palette_qual_measured.csv"))

    # 实测白（索引 15 实测 #ABB3AB）当参考白
    white = mea[15]
    wb = white_balance(mea, white)

    src = fit_cover(Image.open(args.image).convert("RGB"), PORT_W, PORT_H)
    os.makedirs(args.outdir, exist_ok=True)

    print(f"色板：标称 / 实测 / 实测+白点归一化（实测白 #{white[0]:02X}{white[1]:02X}{white[2]:02X}）")
    print(f"原图裁到 {PORT_W}x{PORT_H}\n")

    # 三套量化策略；**渲染一律用实测色**，那才是屏上真正显示的颜色
    strategies = [("标称色板", nom), ("实测色板", mea), ("实测+白平衡", wb)]

    print(f"{'':24} {'逐像素 ΔE':>10} {'局部平均 ΔE':>12} {'适应后 ΔE':>11}")
    stats = {}
    for name, pal in strategies:
        idx = quantize(src, active, pal, "none", False, "lab")
        rendered = render(idx, PORT_W, PORT_H, mea)
        d_px = mean_de_vs_original(rendered, src)
        d_av = local_avg_de(rendered, src)
        stats[name] = (d_px, d_av)
        print(f"{name+'（不抖动）':24} {d_px:>10.2f} {d_av:>12.2f}")

    renders = []
    for name, pal in strategies:
        idx = quantize(src, active, pal, "floyd-steinberg", False, "lab")
        rendered = render(idx, PORT_W, PORT_H, mea)
        d_px = mean_de_vs_original(rendered, src)
        d_av = local_avg_de(rendered, src)
        d_ad = adapted_de(rendered, src, white)
        stats[name + " 抖动"] = (d_px, d_av, d_ad)
        print(f"{name+'（Floyd）':24} {d_px:>10.2f} {d_av:>12.2f}   {d_ad:>10.2f}")
        renders.append((name + " + Floyd", rendered))

    # ---------- 对比图
    panels = [("原图", src)] + renders
    W, H = PORT_W, PORT_H
    canvas = Image.new("RGB", (W * len(panels) + 8 * (len(panels) - 1), H), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    for i, (name, im) in enumerate(panels):
        x = i * (W + 8)
        canvas.paste(im, (x, 0))
        d.rectangle([x + 4, 4, x + 200, 30], fill=(0, 0, 0))
        d.text((x + 12, 12), name, fill=(255, 255, 255))
    path = os.path.join(args.outdir, "measured_vs_nominal.png")
    canvas.save(path)
    print(f"\n对比图 -> {path}")

    # ---------- 结论
    print("\n结论：")
    print(f"  {'':14} {'逐像素':>8} {'局部平均':>9} {'适应后':>8}")
    for name, _ in strategies:
        px, av, ad = stats[name + " 抖动"]
        print(f"  {name:14} {px:>8.2f} {av:>9.2f} {ad:>8.2f}")
    best_abs = min(strategies, key=lambda s: stats[s[0] + " 抖动"][1])[0]
    best_ad = min(strategies, key=lambda s: stats[s[0] + " 抖动"][2])[0]
    print(f"\n  绝对误差最优：{best_abs}")
    print(f"  适应后（更接近观感）最优：{best_ad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
