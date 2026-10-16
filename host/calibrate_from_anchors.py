#!/usr/bin/env python3
"""用 epdoptimize 的独立实测 Spectra 6 色板当锚点，校准我们自己的实测色板。

为什么可信：
  我们的测量仪器（林上 LS173）是 D/8 **SCI** 几何，包含镜面反射光 ——
  等于给每个读数加了一份与颜色无关的白光。后果是暗部被抬高、色度被稀释。

  epdoptimize 提供了多套**独立测量**的 Spectra 6 色板
  （不同的人、不同的仪器、不同的面板批次）。拿它们的 6 个基色当真值锚点，
  可以拟合出我们自己那份数据里的系统偏差，而且**不循环论证**。

模型（在**线性**反射率空间）：
    实测_线性 = a · 真实_线性 + b

  b 是镜面项（加法），a 是增益。锚点够多就能解出来。
  注意 a 里混着"我们面板和他们的面板不同"这个因素，所以校正后的色板是
  **近似**，不是精确值 —— 但比不校正强得多。
"""

import csv
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")
EPD = os.environ.get("EPDOPTIMIZE_DIR", "")
if not EPD:
    sys.exit("请先设置环境变量 EPDOPTIMIZE_DIR 指向 epdoptimize 仓库根目录")
EPD = os.path.join(EPD, "src", "dither", "data", "default-palettes.json")

# 我们色板里对应 6 个原生基色的索引（按标称值和原色的距离找出来的）
ANCHORS = [("black", 3), ("white", 15), ("blue", 18),
           ("green", 52), ("red", 39), ("yellow", 63)]

# 用哪几套他们的色板当参照
REF_PALETTES = ["spectra6", "aitjcize-spectra6", "spectra6legacy", "spectra6-boeber"]


def lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


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


def hexc(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def load_ours(path):
    pal, used = [None] * 64, []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        i = int(r["index"])
        pal[i] = (int(r["R"]), int(r["G"]), int(r["B"]))
        if r.get("used") == "Y":
            used.append(i)
    return pal, used


def fit_ls(xs, ys):
    """y = a*x + b 最小二乘，返回 a, b, rms"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 1.0
    b = my - a * mx
    rms = math.sqrt(sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys)) / n)
    return a, b, rms


def main():
    ours, used = load_ours(os.path.join(DOCS, "palette_qual_measured.csv"))
    epd = json.load(open(EPD, encoding="utf-8"))

    names = list(epd.keys())
    avail = [p for p in REF_PALETTES if p in names]
    if not avail:
        avail = [k for k in names if k.startswith("spectra6") or "spectra6" in k]
    print(f"参照色板（独立实测）：{', '.join(avail)}\n")

    # 展开成 {primary: [每个参照的值]}
    ref = {}
    for p in avail:
        for e in epd[p]:
            ref.setdefault(e["name"], []).append((p, hexc(e["color"])))

    print("## 锚点对照（我们实测 vs 独立实测）\n")
    hdr = f"{'基色':8s} {'我们的索引':>8s} {'我们实测':>10s} | " + \
          " | ".join(f"{p[:14]:>14s}" for p in avail)
    print(hdr)
    print("-" * len(hdr))
    for name, idx in ANCHORS:
        vals = " | ".join(f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}".rjust(14)
                          for _, c in ref.get(name, []))
        m = ours[idx]
        print(f"{name:8s} {idx:>8d} #{m[0]:02X}{m[1]:02X}{m[2]:02X} | {vals}")

    # ---------------------------------------------------------------- 拟合
    # ⚠️ 只用**黑/白两端**拟合，不用中间色。
    #
    # 为什么：4 套参照色板是不同面板、不同仪器测的，它们**互相之间的分歧**
    # （红的 L* 从 22.7 到 49）比我们要测的偏差还大。用全部 6 色拟合，
    # 拟合出来的其实是"参照之间的分歧"，结果是荒谬的（b 变负数、对比度 2.8:1）。
    #
    # 但黑和白是可靠的：任何面板的黑就是黑、白就是白，四套参照在**方向上完全一致**
    # （我们的黑太亮、白太暗 —— 即我们的测量被压缩了）。所以只拿这两端定范围。
    ENDPOINTS = [("black", 3), ("white", 15)]

    def median(xs):
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    print("\n## 参照之间的分歧（说明为什么不能用中间色拟合）\n")
    print("| 基色 | 各参照的 L* | 极差 |")
    print("|---|---|---:|")
    for name, idx in ANCHORS:
        ls = [rgb_to_lab(c)[0] for _, c in ref.get(name, [])]
        print(f"| {name} | {' / '.join(f'{v:.0f}' for v in ls)} | {max(ls)-min(ls):.0f} |")

    xs, ys = [], []
    for name, idx in ENDPOINTS:
        # 该基色的"真值" = 四套参照的中位数（逐通道）
        cs = [c for _, c in ref.get(name, [])]
        med = tuple(int(round(median([c[k] for c in cs]))) for k in range(3))
        for k in range(3):
            xs.append(lin(ours[idx][k]))     # 观测（实测）
            ys.append(lin(med[k]))           # 目标（真值）

    # fit_ls(xs, ys) 拟合的是  ys = a·xs + b，即 **真值 = a·实测 + b** —— 直接可用。
    # ⚠️ 别套逆变换：上一版就是把它当成 "实测 = a·真实 + b" 又取逆，结果全错。
    a, b, rms = fit_ls(xs, ys)

    print("\n## 只用黑白两端拟合\n")
    print("```")
    print(f"真实_线性 = {a:.4f} · 实测_线性 + ({b:+.4f})")
    print(f"样本 {len(xs)} 个（黑白 2 色 × 3 通道，真值取 {len(avail)} 套参照的中位数）")
    print(f"残差 RMS = {rms:.4f}")
    print("```\n")
    print(f"- 增益 a = {a:.3f}（>1 = 我们的测量被压缩了，要往外拉）")
    print(f"- 偏移 b = {b:+.4f}（负值 = 减掉镜面项；|b| ≈ {abs(b)*100:.1f}%）")
    print("- 对照：仪器规格推测镜面项 4.4~5.3%（按面板对比度物理边界）")

    # 用锚点自己验算一遍
    print("\n  **锚点自检**（把实测值代进去，看能不能落回他们的真值）：")
    for name, idx in ENDPOINTS:
        cs = [c for _, c in ref.get(name, [])]
        med = tuple(int(round(median([c[k] for c in cs]))) for k in range(3))
        pred = tuple(max(0, min(255, round(lin_to_srgb(a * lin(ours[idx][k]) + b) * 255)))
                     for k in range(3))
        print(f"    {name:6s} 实测 #{ours[idx][0]:02X}{ours[idx][1]:02X}{ours[idx][2]:02X}"
              f" -> 预测 #{pred[0]:02X}{pred[1]:02X}{pred[2]:02X}"
              f"   他们的中位数 #{med[0]:02X}{med[1]:02X}{med[2]:02X}")


    # ---------------------------------------------------------------- 校正
    fixed = [None] * 64
    for i in range(64):
        row = []
        for c in range(3):
            v = a * lin(ours[i][c]) + b
            row.append(max(0, min(255, round(lin_to_srgb(v) * 255))))
        fixed[i] = tuple(row)

    print("## 校正前后对比（部分档位）\n")
    print("| 索引 | 实测 | 校正后 | L* 变化 | C* 变化 |")
    print("|---:|---|---|---|---|")
    for i in (3, 8, 15, 18, 32, 39, 48, 52, 55, 63):
        Lm, Am, Bm = rgb_to_lab(ours[i])
        Lf, Af, Bf = rgb_to_lab(fixed[i])
        print(f"| {i} | #{ours[i][0]:02X}{ours[i][1]:02X}{ours[i][2]:02X} | "
              f"#{fixed[i][0]:02X}{fixed[i][1]:02X}{fixed[i][2]:02X} | "
              f"{Lm:.1f} -> {Lf:.1f} | {math.hypot(Am,Bm):.1f} -> {math.hypot(Af,Bf):.1f} |")

    # ---------------------------------------------------------------- 体检
    lex = lambda rgb: sum(lin(v) for v in rgb) / 3.0
    Wm, Bm_ = lex(ours[15]), lex(ours[3])
    Wf, Bf_ = lex(fixed[15]), lex(fixed[3])
    cm = [math.hypot(*rgb_to_lab(ours[i])[1:]) for i in used]
    cf = [math.hypot(*rgb_to_lab(fixed[i])[1:]) for i in used]
    lm = [rgb_to_lab(ours[i])[0] for i in used]
    lf = [rgb_to_lab(fixed[i])[0] for i in used]

    print("\n## 物理体检\n")
    print("| 指标 | 实测 | 校正后 | 合理范围 |")
    print("|---|---|---|---|")
    print(f"| 对比度（白/黑） | {Wm/Bm_:.1f} : 1 | **{Wf/max(Bf_,1e-6):.1f} : 1** | 15~22（同类面板规格） |")
    print(f"| 平均色度 C* | {sum(cm)/len(cm):.1f} | **{sum(cf)/len(cf):.1f}** | — |")
    print(f"| 明度范围 | {min(lm):.1f} ~ {max(lm):.1f} | **{min(lf):.1f} ~ {max(lf):.1f}** | — |")

    # ---------------------------------------------------------------- 导出
    out_csv = os.path.join(DOCS, "palette_qual_锚点校正.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "R", "G", "B", "HEX", "used", "source"])
        for i in range(64):
            r, g, bl = fixed[i]
            src = "anchor-calibrated" if i in used else "nominal"
            w.writerow([i, r, g, bl, f"#{r:02X}{g:02X}{bl:02X}",
                        "Y" if i in used else "", src])
    act = os.path.join(HERE, "act", "48-color-qual-ac.act")
    with open(act, "wb") as fh:
        for i in range(64):
            fh.write(bytes(fixed[i]))
    print(f"\n导出 -> {out_csv}")
    print(f"导出 -> {act}（{os.path.getsize(act)} 字节）")


if __name__ == "__main__":
    main()
