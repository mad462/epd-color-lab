#!/usr/bin/env python3
"""把量化管线拆成几段，逐段渲染出来看，定位"偏黄"到底出在哪一步。

输出一张对比图：每格用**实测色板**渲染（也就是面板实际会显示的颜色），
这样看到的就是上屏效果，而不是网页预览的乐观版本。
"""

import csv
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")

W, H = 240, 320          # 小图够看色偏，跑得快


# ---------------------------------------------------------------- 色彩
def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb):
    r, g, b = (_lin(v) for v in rgb)
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750)
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab_to_rgb(lab):
    L, A, B = lab
    fy = (L + 16) / 116
    fx = fy + A / 500
    fz = fy - B / 200

    def finv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787

    x = finv(fx) * 0.95047
    y = finv(fy)
    z = finv(fz) * 1.08883
    r = x * 3.2404542 + y * -1.5371385 + z * -0.4985314
    g = x * -0.9692660 + y * 1.8760108 + z * 0.0415560
    b = x * 0.0556434 + y * -0.2040259 + z * 1.0572252

    def enc(c):
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055

    return tuple(max(0, min(255, round(enc(v) * 255))) for v in (r, g, b))


def load_palette(path):
    pal = [None] * 64
    used = []
    for row in csv.DictReader(open(path, encoding="utf-8")):
        i = int(row["index"])
        pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
        if row.get("used") == "Y":
            used.append(i)
    return pal, used


# ---------------------------------------------------------------- 管线各段
def auto_levels(img, p_lo=0.02, p_hi=0.98):
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
        if acc >= total * p_lo:
            lo = v
            break
    acc, hi = 0, 255
    for v in range(256):
        acc += hist[v]
        if acc >= total * p_hi:
            hi = v
            break
    if hi - lo < 8:
        lo, hi = 0, 255
    lut = [max(0, min(255, round((v - lo) / (hi - lo) * 255))) for v in range(256)]
    return img.point(lut * 3), lo, hi


def panel_map(img, lmin, lmax):
    """明度压到面板范围，色度跟 L 同比例缩（和网页一致）"""
    out = Image.new("RGB", img.size)
    src, dst = img.load(), out.load()
    for y in range(img.height):
        for x in range(img.width):
            L, A, B = rgb_to_lab(src[x, y])
            L2 = lmin + (L / 100) * (lmax - lmin)
            k = (L2 / L) if L > 1 else 1.0
            k = max(0.5, min(1.6, k))
            dst[x, y] = lab_to_rgb((L2, A * k, B * k))
    return out


def boost_sat(img, pct):
    if pct == 0:
        return img
    k = 1 + pct / 100.0
    out = Image.new("RGB", img.size)
    src, dst = img.load(), out.load()
    for y in range(img.height):
        for x in range(img.width):
            L, A, B = rgb_to_lab(src[x, y])
            dst[x, y] = lab_to_rgb((L, A * k, B * k))
    return out


def quantize(img, pal, used, lweight=1.2, dither=True):
    """返回索引图"""
    w, h = img.size
    lab_pal = [(i, rgb_to_lab(pal[i])) for i in used]
    buf = [[0.0] * (w * 3) for _ in range(h)]
    px = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            buf[y][x * 3] = float(r)
            buf[y][x * 3 + 1] = float(g)
            buf[y][x * 3 + 2] = float(b)

    idx = Image.new("L", (w, h))
    ipx = idx.load()
    KERNEL = [(1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)]

    for y in range(h):
        for x in range(w):
            r = max(0.0, min(255.0, buf[y][x * 3]))
            g = max(0.0, min(255.0, buf[y][x * 3 + 1]))
            b = max(0.0, min(255.0, buf[y][x * 3 + 2]))
            L, A, B = rgb_to_lab((int(r), int(g), int(b)))
            best, bestd = used[0], 1e18
            for i, (pl, pa, pb) in lab_pal:
                dL = (L - pl) * lweight
                d = dL * dL + (A - pa) ** 2 + (B - pb) ** 2
                if d < bestd:
                    bestd, best = d, i
            ipx[x, y] = best
            if dither:
                tr, tg, tb = pal[best]
                er, eg, eb = r - tr, g - tg, b - tb
                for dx, dy, f in KERNEL:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        buf[ny][nx * 3] += er * f
                        buf[ny][nx * 3 + 1] += eg * f
                        buf[ny][nx * 3 + 2] += eb * f
    return idx


def render(idx, pal):
    w, h = idx.size
    out = Image.new("RGB", (w, h))
    src, dst = idx.load(), out.load()
    for y in range(h):
        for x in range(w):
            dst[x, y] = pal[src[x, y]]
    return out


# ---------------------------------------------------------------- 主流程
def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Chizhi\Desktop\- 02.png"
    mea, used_mea = load_palette(os.path.join(DOCS, "palette_qual_measured.csv"))
    nom, used_nom = load_palette(os.path.join(DOCS, "palette_qual.csv"))

    # 有效槽位的 L* 范围
    ls = [rgb_to_lab(mea[i])[0] for i in used_mea]
    LMIN, LMAX = min(ls), max(ls)
    print(f"实测色板 L* 范围 {LMIN:.1f} ~ {LMAX:.1f}")

    base = Image.open(src_path).convert("RGB")
    s = max(W / base.width, H / base.height)
    base = base.resize((int(base.width * s), int(base.height * s)), Image.LANCZOS)
    left = (base.width - W) // 2
    top = (base.height - H) // 2
    base = base.crop((left, top, left + W, top + H))

    variants = []

    # 0 原图（参考）
    variants.append(("原图", base))

    # 1 标称色板 + 权重2（最老的那版，用户当时说只是偏绿）
    variants.append(("标称色板 w2",
                     render(quantize(base, nom, used_nom, 2.0), nom)))

    # 2 实测色板 + 权重2 + 无色调映射
    variants.append(("实测 w2 无映射",
                     render(quantize(base, mea, used_mea, 2.0), mea)))

    # 3 实测 + 权重1.2 + 无映射
    variants.append(("实测 w1.2 无映射",
                     render(quantize(base, mea, used_mea, 1.2), mea)))

    # 4 实测 + 权重1.2 + 面板映射
    v4 = panel_map(base, LMIN, LMAX)
    variants.append(("实测 w1.2 +映射",
                     render(quantize(v4, mea, used_mea, 1.2), mea)))

    # 5 实测 + 权重1.2 + 面板映射 + 自动色阶
    v5, lo, hi = auto_levels(base)
    v5 = panel_map(v5, LMIN, LMAX)
    variants.append((f"实测 w1.2 +映射+色阶{lo}-{hi}",
                     render(quantize(v5, mea, used_mea, 1.2), mea)))

    # 拼成网格
    cols = 3
    rows = (len(variants) + cols - 1) // cols
    pad, label_h = 8, 20
    gw = cols * W + (cols + 1) * pad
    gh = rows * (H + label_h) + (rows + 1) * pad
    grid = Image.new("RGB", (gw, gh), (18, 20, 24))
    d = ImageDraw.Draw(grid)
    for n, (label, im) in enumerate(variants):
        cx = pad + (n % cols) * (W + pad)
        cy = pad + (n // cols) * (H + label_h + pad)
        grid.paste(im, (cx, cy))
        d.text((cx + 2, cy + H + 4), label, fill=(230, 230, 230))

    out_path = os.path.join(DOCS, "变体对比.png")
    grid.save(out_path)
    print("对比图 ->", out_path)

    # 顺便量一下每格的平均色偏（和原图比）
    print("\n各变体的平均 Lab 偏移（相对原图，正 b* = 偏黄）:")
    ref = rgb_to_lab_mean(base)
    for label, im in variants:
        m = rgb_to_lab_mean(im)
        print(f"  {label:22s} dL*{m[0]-ref[0]:+6.1f}  da*{m[1]-ref[1]:+6.1f}  db*{m[2]-ref[2]:+6.1f}")


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
