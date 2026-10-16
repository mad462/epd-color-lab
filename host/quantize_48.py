#!/usr/bin/env python3
"""把照片量化成 3.7" E6 的 8bpp 索引图（48 色方案）。

严格照抄 FrameFilm `tools/ForFilm/js/convert.js` 的选色规则：
  - 在 **D65 CIELAB** 里做最近邻
  - **明度权重 ×2**（原文注释：墨水屏上明度保真比色度更重要，灰阶梯度也更单调）
  - 只在候选索引里选（占位索引不参与）

索引含义：`index = 高3bit * 8 + 低3bit`
  高 3bit 由第一相（qual_phase1 / fast_color_phase1）刷进去
  低 3bit 由第三相（qual_phase3）或第二相（fast_color_phase2）刷进去

输出：
  <名字>.bin8            345600 字节（720x480，每像素 1 字节索引），推给固件
  <名字>_preview.png     量化后应该是的样子（用它跟屏上对比！）
  <名字>_side.png        原图 | 预览 并排

用法：
    python quantize_48.py 照片.jpg -o out
    python quantize_48.py 照片.jpg -o out --profile fast
    python quantize_48.py 照片.jpg -o out --dither floyd
"""

import argparse
import csv
import os
import sys

from PIL import Image

WIDTH, HEIGHT = 720, 480
FRAME_BYTES = WIDTH * HEIGHT

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")


# ---------------------------------------------------------------- 色板
def load_palette(profile):
    """从抠出来的 CSV 读色板。profile: 'qual' | 'fast'"""
    path = os.path.join(DOCS, f"palette_{profile}.csv")
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}，先跑 host/extract_palette.py")

    palette = [None] * 64
    candidates = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            i = int(row["index"])
            palette[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
            if row["used"] == "Y":
                candidates.append(i)
    if any(p is None for p in palette):
        sys.exit("色板不完整")
    return palette, candidates


# ---------------------------------------------------------------- 色彩空间
def _srgb_to_linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb):
    r, g, b = (_srgb_to_linear(v) for v in rgb)
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750)
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


# ---------------------------------------------------------------- 量化
def quantize(img, palette, candidates, dither):
    """返回 bytearray（每像素一个索引）。"""
    pal_lab = [rgb_to_lab(palette[i]) for i in candidates]
    w, h = img.size
    px = img.load()

    out = bytearray(w * h)

    # 误差缓冲（只有 floyd 用）
    err = [[0.0, 0.0, 0.0] for _ in range(w + 2)] if dither == "floyd" else None
    nxt = [[0.0, 0.0, 0.0] for _ in range(w + 2)] if dither == "floyd" else None

    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if err is not None:
                e = err[x + 1]
                r = max(0, min(255, r + e[0]))
                g = max(0, min(255, g + e[1]))
                b = max(0, min(255, b + e[2]))

            L, A, B = rgb_to_lab((r, g, b))

            best_i, best_d, best_lab = candidates[0], 1e30, pal_lab[0]
            for k, idx in enumerate(candidates):
                pl, pa, pb = pal_lab[k]
                dl, da, db = L - pl, A - pa, B - pb
                # 明度权重 2 倍
                d = 4.0 * dl * dl + da * da + db * db
                if d < best_d:
                    best_d, best_i, best_lab = d, idx, (pl, pa, pb)
            out[y * w + x] = best_i

            if err is not None:
                # 把误差按 Floyd-Steinberg 扩散到右边和下一行（RGB 空间近似）
                tr = palette[best_i]
                dr = r - tr[0]
                dg = g - tr[1]
                db = b - tr[2]
                for (dx, dy, wgt) in ((1, 0, 7), (2, 0, 1), (-1, 1, 3), (0, 1, 5), (1, 1, 1)):
                    tx = x + dx
                    if tx < 0 or tx >= w:
                        continue
                    tgt = nxt if dy == 1 else err
                    cell = tgt[tx + 1]
                    cell[0] += dr * wgt / 16.0
                    cell[1] += dg * wgt / 16.0
                    cell[2] += db * wgt / 16.0
        if err is not None:
            err, nxt = nxt, err
            for c in nxt:
                c[0] = c[1] = c[2] = 0.0

    return out


def fit(img, mode):
    w, h = img.size
    if mode == "stretch":
        return img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    scale = (min(WIDTH / w, HEIGHT / h) if mode == "contain"
             else max(WIDTH / w, HEIGHT / h))
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    canvas.paste(img.resize((nw, nh), Image.LANCZOS),
                 ((WIDTH - nw) // 2, (HEIGHT - nh) // 2))
    return canvas


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--profile", choices=("qual", "fast"), default="qual",
                    help="qual = ColorQual 3 相（默认）；fast = ColorFast 2 相")
    ap.add_argument("--fit", choices=("cover", "contain", "stretch"), default="cover")
    ap.add_argument("--dither", choices=("none", "floyd"), default="none",
                    help="默认不抖动；52 色通常够用，抖动会加颗粒")
    args = ap.parse_args()

    palette, candidates = load_palette(args.profile)
    print(f"色板 rgb_{args.profile}：候选色 {len(candidates)} 个")

    img = fit(Image.open(args.image).convert("RGB"), args.fit)
    idx = quantize(img, palette, candidates, args.dither)
    assert len(idx) == FRAME_BYTES, len(idx)

    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.image))[0]

    bin_path = os.path.join(args.outdir, f"{stem}_{args.profile}.bin8")
    with open(bin_path, "wb") as fh:
        fh.write(idx)

    # 预览：用色板 RGB 渲染每个像素
    prev = Image.new("RGB", (WIDTH, HEIGHT))
    prev.putdata([palette[v] for v in idx])
    prev_path = os.path.join(args.outdir, f"{stem}_{args.profile}_preview.png")
    prev.save(prev_path)

    side = Image.new("RGB", (WIDTH * 2 + 8, HEIGHT), (0, 0, 0))
    side.paste(img, (0, 0))
    side.paste(prev, (WIDTH + 8, 0))
    side_path = os.path.join(args.outdir, f"{stem}_{args.profile}_side.png")
    side.save(side_path)

    used = {}
    for v in idx:
        used[v] = used.get(v, 0) + 1
    print(f"\n用到 {len(used)} 种颜色（候选 {len(candidates)}）")
    top = sorted(used.items(), key=lambda kv: -kv[1])[:12]
    print("  占比最高的：" + "  ".join(
        f"#{palette[i][0]:02X}{palette[i][1]:02X}{palette[i][2]:02X} {c*100/len(idx):.1f}%"
        for i, c in top))

    print(f"\n索引图 {bin_path}（{FRAME_BYTES} 字节）")
    print(f"预览   {prev_path}")
    print(f"并排   {side_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
