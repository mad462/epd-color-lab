#!/usr/bin/env python3
"""复现网页那条管线，专门查「鲜红变暗红」。

网页的调色逻辑在 JS 里，这个脚本照抄一份到 Python，好用批量对比的方式定位。
参数用的是用户实测调出来的那套。
"""

import csv
import math
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")

SRGB = [0.0] * 256
for _i in range(256):
    _c = _i / 255.0
    SRGB[_i] = _c / 12.92 if _c <= 0.04045 else ((_c + 0.055) / 1.055) ** 2.4


def lin(c):
    return SRGB[int(c)]


def lin_to_srgb(v):
    v = max(0.0, min(1.0, v))
    return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055


def rgb_to_lab(rgb):
    r, g, b = (lin(v) for v in rgb)
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
    return tuple(max(0, min(255, round(lin_to_srgb(v) * 255))) for v in (r, g, b))


def load_palette(path):
    pal, used = [None] * 64, []
    for row in csv.DictReader(open(path, encoding="utf-8")):
        i = int(row["index"])
        pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
        if row.get("used") == "Y":
            used.append(i)
    return pal, used


# ---------------------------------------------------------------- 管线
def auto_levels(img):
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
        if acc >= total * 0.01:
            lo = v
            break
    acc, hi = 0, 255
    for v in range(256):
        acc += hist[v]
        if acc >= total * 0.99:
            hi = v
            break
    if hi - lo < 8:
        lo, hi = 0, 255
    return lo, hi


def apply_tone(img, lo, hi, black, bright, contrast):
    cf = (100 + contrast) / 100.0
    bf = bright * 2.55
    lut = [0] * 256
    for v in range(256):
        x = black + (v - lo) / (hi - lo) * (247 - black)
        x = (x - 128) * cf + 128 + bf
        lut[v] = max(0, min(255, round(x)))
    return img.point(lut * 3)


def sat_of(r, g, b):
    mx = max(r, max(g, b)) / 255.0
    mn = min(r, min(g, b)) / 255.0
    return 0.0 if mx == 0 else (mx - mn) / mx


def smoothstep(e0, e1, x):
    if e1 <= e0:
        return 1.0 if x >= e1 else 0.0
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def local_luma(img, scale):
    w, h = img.size
    bw, bh = max(1, round(w / scale)), max(1, round(h / scale))
    s = [0.0] * (bw * bh)
    c = [0] * (bw * bh)
    px = img.load()
    for y in range(h):
        by = min(bh - 1, y * bh // h)
        for x in range(w):
            bx = min(bw - 1, x * bw // w)
            r, g, b = px[x, y]
            s[by * bw + bx] += r * 0.2126 + g * 0.7152 + b * 0.0722
            c[by * bw + bx] += 1
    for i in range(len(s)):
        s[i] /= max(1, c[i])
    for _ in range(2):
        s = box(s, bw, bh, 2)
    return s, bw, bh


def box(src, w, h, r):
    t = [0.0] * (w * h)
    o = [0.0] * (w * h)
    for y in range(h):
        for x in range(w):
            a, n = 0.0, 0
            for d in range(-r, r + 1):
                xx = x + d
                if 0 <= xx < w:
                    a += src[y * w + xx]
                    n += 1
            t[y * w + x] = a / n
    for x in range(w):
        for y in range(h):
            a, n = 0.0, 0
            for d in range(-r, r + 1):
                yy = y + d
                if 0 <= yy < h:
                    a += t[yy * w + x]
                    n += 1
            o[y * w + x] = a / n
    return o


def samp(m, w, h, fx, fy):
    x0 = max(0, min(w - 1, int(fx)))
    y0 = max(0, min(h - 1, int(fy)))
    x1 = min(w - 1, x0 + 1)
    y1 = min(h - 1, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    return ((m[y0 * w + x0] * (1 - tx) + m[y0 * w + x1] * tx) * (1 - ty) +
            (m[y1 * w + x0] * (1 - tx) + m[y1 * w + x1] * tx) * ty)


def shadows_highlights(img, shadow, highlight, radius):
    if shadow == 0 and highlight == 0:
        return img
    w, h = img.size
    m, bw, bh = local_luma(img, max(2, round(radius)))
    out = Image.new("RGB", (w, h))
    src, dst = img.load(), out.load()
    s, hi = shadow / 100.0, highlight / 100.0
    for y in range(h):
        fy = (y + 0.5) * bh / h - 0.5
        for x in range(w):
            fx = (x + 0.5) * bw / w - 0.5
            lb = samp(m, bw, bh, fx, fy) / 255.0
            r, g, b = src[x, y]
            lum = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 255.0
            mS = (1 - lb) ** 2 * (1 - lum)
            mH = lb * lb * lum
            f = 1 + s * mS * 0.9 - hi * mH * 0.45
            dst[x, y] = (max(0, min(255, round(r * f))),
                         max(0, min(255, round(g * f))),
                         max(0, min(255, round(b * f))))
    return out


def tint_img(img, sat_pct, tint):
    if sat_pct == 0 and tint == 0:
        return img
    k = 1 + sat_pct / 100.0
    out = Image.new("RGB", img.size)
    src, dst = img.load(), out.load()
    for y in range(img.height):
        for x in range(img.width):
            L, A, B = rgb_to_lab(src[x, y])
            dst[x, y] = lab_to_rgb((L, A * k, B * k + tint))
    return out


def quantize(img, pal, used, lw=1.2, serp=True, chroma_guard=False, pure_sat=False):
    w, h = img.size
    labs = [(i, rgb_to_lab(pal[i])) for i in used]
    orig = [[0] * (w * 3) for _ in range(h)]
    buf = [[0.0] * (w * 3) for _ in range(h)]
    px = img.load()
    for y in range(h):
        row, orow = buf[y], orig[y]
        for x in range(w):
            r, g, b = px[x, y]
            orow[x * 3], orow[x * 3 + 1], orow[x * 3 + 2] = r, g, b
            row[x * 3], row[x * 3 + 1], row[x * 3 + 2] = float(r), float(g), float(b)
    K = [(1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)]
    idx = Image.new("L", (w, h))
    ip = idx.load()
    CLAMP = 64
    PURE_SAT = 0.5
    for y in range(h):
        rev = serp and (y & 1)
        for i in range(w):
            x = (w - 1 - i) if rev else i
            r0, g0, b0 = orig[y][x * 3], orig[y][x * 3 + 1], orig[y][x * 3 + 2]
            sat = sat_of(r0, g0, b0)
            k = 0.85 * min(1.0, sat / 0.6) if pure_sat else 0.0
            keep = 1 - k
            wr = max(0.0, min(255.0, buf[y][x * 3]))
            wg = max(0.0, min(255.0, buf[y][x * 3 + 1]))
            wb = max(0.0, min(255.0, buf[y][x * 3 + 2]))
            rf = k * r0 + keep * wr
            gf = k * g0 + keep * wg
            bf = k * b0 + keep * wb
            ri, gi, bi = int(rf), int(gf), int(bf)
            effw = lw * (1 - 0.7 * sat) if chroma_guard else lw
            L, A, B = rgb_to_lab((ri, gi, bi))
            best, bd = used[0], 1e18
            for pi, (pl, pa, pb) in labs:
                dL = (L - pl) * effw
                d = dL * dL + (A - pa) ** 2 + (B - pb) ** 2
                if d < bd:
                    bd, best = d, pi
            ip[x, y] = best
            tr, tg, tb = pal[best]
            er, eg, eb = (rf - tr) * keep, (gf - tg) * keep, (bf - tb) * keep
            for dx, dy, f in K:
                nx, ny = x + (dx if not rev else -dx), y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    o2 = nx * 3
                    for ch, e in ((0, er), (1, eg), (2, eb)):
                        buf[ny][o2 + ch] = max(orig[ny][o2 + ch] - CLAMP,
                                               min(orig[ny][o2 + ch] + CLAMP,
                                                   buf[ny][o2 + ch] + e * f))
    return idx


def render(idx, pal):
    w, h = idx.size
    out = Image.new("RGB", (w, h))
    src, dst = idx.load(), out.load()
    for y in range(h):
        for x in range(w):
            dst[x, y] = pal[src[x, y]]
    return out


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Chizhi\Desktop\- 02.png"
    mea, used = load_palette(os.path.join(DOCS, "palette_qual_measured.csv"))
    dc, _ = load_palette(os.path.join(DOCS, "palette_qual_去污染.csv"))

    W, H = 480, 720
    base = Image.open(src_path).convert("RGB")
    s = max(W / base.width, H / base.height)
    base = base.resize((int(base.width * s), int(base.height * s)), Image.LANCZOS)
    l, t = (base.width - W) // 2, (base.height - H) // 2
    base = base.crop((l, t, l + W, t + H))

    lo, hi = auto_levels(base)
    print(f"自动色阶 {lo}~{hi}")

    variants = []

    c025, _ = load_palette(os.path.join(DOCS, "palette_qual_去污染.csv")) if False else (None, None)
    # 三档校正色板
    def fix_level(b):
        out = []
        for i in range(64):
            rgb = []
            for c in range(3):
                v = max(0.0, SRGB[mea[i][c]] - b)
                rgb.append(max(0, min(255, round(lin_to_srgb(v) * 255))))
            out.append(tuple(rgb))
        return out
    c025 = fix_level(0.025)
    c050 = fix_level(0.050)

    def add(tag, pal, black, bright, highlight, tint, lw=1.2, guard=False, pure=False):
        img = apply_tone(base, lo, hi, black, bright, 0)
        img = shadows_highlights(img, 0, highlight, 12)
        img = tint_img(img, 0, tint)
        idx = quantize(img, pal, used, lw, True, guard, pure)
        variants.append((tag, render(idx, mea)))

    add("① 2.5% 硬开关（你现在）", c025, 60, -11, 100, -10, 1.2, True, False)
    add("② 2.5% 平滑（新）", c025, 60, -11, 100, -10, 1.2, True, True)
    add("③ 2.5% 不保色相", c025, 60, -11, 100, -10, 1.2, False, False)
    add("④ 5.0% 平滑", c050, 34, -11, 100, -10, 1.2, True, True)
    add("⑤ 实测原值 平滑", mea, 73, -11, 100, -10, 1.2, True, True)

    variants.append(("⑥ 原图（参考）", base))

    out = variants
    cols = 3
    rows = (len(out) + cols - 1) // cols
    pad, lh = 8, 22
    grid = Image.new("RGB", (cols * W + (cols + 1) * pad,
                             rows * (H + lh) + (rows + 1) * pad), (18, 20, 24))
    d = ImageDraw.Draw(grid)
    for n, (tag, im) in enumerate(out):
        cx = pad + (n % cols) * (W + pad)
        cy = pad + (n // cols) * (H + lh + pad)
        grid.paste(im, (cx, cy))
        d.text((cx + 2, cy + H + 5), tag, fill=(235, 235, 235))

    p = os.path.join(DOCS, "红色诊断.png")
    grid.save(p)
    print("-> ", p)


if __name__ == "__main__":
    main()
