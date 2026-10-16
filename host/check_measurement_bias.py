#!/usr/bin/env python3
"""检验一个假设：实测值是不是被"环境光混入"污染了？

原理：如果测量时混进了环境光（漫射杂散光、含镜面反射、或仪器没贴紧），
读数会变成

    实测_线性反射率 = a · 真实_线性反射率 + b        （b = 混入的杂散光）

这个模型的特征是：
  · 暗部被抬起来（纯黑测出 L*31 而不是 L*15）
  · 饱和度被压（混入的白光稀释了色度）
  · 对比度被压缩

如果数据能很好地拟合这个模型，就说明"测不准"是很可能的；
如果拟合很差，说明偏色另有原因（色板结构空洞等真实问题）。
"""

import csv
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")


def srgb_to_lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin_to_srgb(v):
    v = max(0.0, min(1.0, v))
    return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055


def rgb_to_lab(rgb):
    r, g, b = (srgb_to_lin(v) for v in rgb)
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750)
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def load(path):
    pal, used = [None] * 64, []
    for row in csv.DictReader(open(path, encoding="utf-8")):
        i = int(row["index"])
        pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
        if row.get("used") == "Y":
            used.append(i)
    return pal, used


def fit_linear(xs, ys):
    """最小二乘 y = a*x + b，返回 (a, b, rms)"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 0
    b = my - a * mx
    rms = math.sqrt(sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys)) / n)
    return a, b, rms


def main():
    mea, used = load(os.path.join(DOCS, "palette_qual_measured.csv"))
    nom, _ = load(os.path.join(DOCS, "palette_qual.csv"))

    # ---------------------------------------------- 逐通道线性拟合
    xs, ys = [], []
    for i in used:
        for c in range(3):
            xs.append(srgb_to_lin(nom[i][c]))
            ys.append(srgb_to_lin(mea[i][c]))
    a, b, rms = fit_linear(xs, ys)

    print("## 假设检验：实测 = a·真实 + b（b = 混入的杂散光）\n")
    print("以标称值为「真实」的粗略代理，逐通道做线性拟合：\n")
    print(f"```")
    print(f"实测_线性 = {a:.3f} · 标称_线性 + {b:.3f}")
    print(f"拟合残差 RMS = {rms:.4f}（线性反射率 0~1 尺度）")
    print(f"```\n")

    print(f"- **增益 a = {a:.2f}**：")
    if a < 1:
        print(f"  实测比标称暗/淡，整体乘了 {a:.2f}")
    print(f"- **杂散项 b = {b:.3f}**：这就是「无论真实多黑，最少也测到这么多」的底噪。")
    print(f"  换算成 sRGB 约 **{lin_to_srgb(b)*255:.0f}**（8bit）")
    print()

    # 用这个模型反推「去污染」后的色板
    print("### 按这个模型「去污染」后的色板\n")
    print("| 索引 | 标称 | 实测 | 去污染后 | 说明 |")
    print("|---:|---|---|---|---|")
    fixed = [None] * 64
    for i in range(64):
        row = [0, 0, 0]
        for c in range(3):
            v = (srgb_to_lin(mea[i][c]) - b) / a if a else srgb_to_lin(mea[i][c])
            row[c] = max(0, min(255, round(lin_to_srgb(v) * 255)))
        fixed[i] = tuple(row)

    for i in used:
        if i not in (3, 8, 15, 32, 39, 48, 55, 59):
            continue
        Lm, Am, Bm = rgb_to_lab(mea[i])
        Lf, Af, Bf = rgb_to_lab(fixed[i])
        cm, cf = math.hypot(Am, Bm), math.hypot(Af, Bf)
        print(f"| {i} | #{nom[i][0]:02X}{nom[i][1]:02X}{nom[i][2]:02X} | "
              f"#{mea[i][0]:02X}{mea[i][1]:02X}{mea[i][2]:02X} | "
              f"#{fixed[i][0]:02X}{fixed[i][1]:02X}{fixed[i][2]:02X} | "
              f"C* {cm:.0f} -> {cf:.0f} |")

    # ---------------------------------------------- 各项统计
    print("\n### 关键统计\n")
    cms = [math.hypot(*rgb_to_lab(mea[i])[1:]) for i in used]
    cns = [math.hypot(*rgb_to_lab(nom[i])[1:]) for i in used]
    lm = [rgb_to_lab(mea[i])[0] for i in used]

    # ---------------------------------------------- 物理合理性检查
    #
    # 纯加法模型（实测 = 真实 + b）有个硬约束：减掉 b 之后的黑不能是负数。
    # 上面拟合出来的 b 直接减会得到负黑 —— 说明拟合值偏大（因为拿理想化的标称
    # 当"真实"）。所以改用**物理边界**来定 b：面板的对比度是已知量级。
    #
    # 参考：同规格六色电子纸（3.56" DEPG0356）规格书 —— 白反射率 30~34%，对比度 15~22。
    lex = lambda rgb: sum(srgb_to_lin(v) for v in rgb) / 3.0
    W_meas, B_meas = lex(mea[15]), lex(mea[3])

    print("#### 物理边界定 b（面板对比度）\n")
    print(f"实测白 线性反射率 {W_meas:.4f}，实测黑 {B_meas:.4f}")
    print(f"实测对比度 = **{W_meas/B_meas:.1f} : 1**")
    print("（同类六色电子纸规格是 15~22:1，实测 6:1 明显偏低 —— 镜面项垫高了黑）\n")
    print("| 假设的真实对比度 | 推出的镜面项 b |")
    print("|---:|---:|")
    for cr in (10, 15, 20, 22, 30):
        bb = (cr * B_meas - W_meas) / (cr - 1)
        mark = "  ← 落在规格区间" if cr in (15, 20, 22) else ""
        print(f"| {cr}:1 | **{bb*100:.1f}%**{mark} |")

    # 取规格区间中值 20:1 作为默认
    CR_ASSUMED = 20.0
    b_phys = (CR_ASSUMED * B_meas - W_meas) / (CR_ASSUMED - 1)
    print(f"\n**采用 b = {b_phys*100:.1f}%**（按真实对比度 {CR_ASSUMED:.0f}:1）")
    print(f"对比：拟合值 b = {b*100:.1f}%（偏大 {b/b_phys-1:+.0%}）—— 不能用")

    # ---------------------------------------------- 用物理边界重新导出
    print("\n#### 按物理边界修正后的色板\n")
    print("| 索引 | 实测 | 修正后 |")
    print("|---:|---|---|")
    fixed2 = [None] * 64
    for i in range(64):
        row = []
        for c in range(3):
            v = max(0.0, srgb_to_lin(mea[i][c]) - b_phys)
            row.append(max(0, min(255, round(lin_to_srgb(v) * 255))))
        fixed2[i] = tuple(row)
    for i in (3, 8, 15, 32, 39, 48, 55, 59):
        Lm, Am, Bm = rgb_to_lab(mea[i])
        Lf, Af, Bf = rgb_to_lab(fixed2[i])
        print(f"| {i} | #{mea[i][0]:02X}{mea[i][1]:02X}{mea[i][2]:02X} C*{math.hypot(Am,Bm):.0f} | "
              f"#{fixed2[i][0]:02X}{fixed2[i][1]:02X}{fixed2[i][2]:02X} C*{math.hypot(Af,Bf):.0f} |")

    W2, B2 = lex(fixed2[15]), lex(fixed2[3])
    print(f"\n修正后对比度 = **{W2/max(B2,1e-6):.1f} : 1**"
          f"（目标 {CR_ASSUMED:.0f}，实测 {W_meas/B_meas:.1f}）")
    cms2 = [math.hypot(*rgb_to_lab(fixed2[i])[1:]) for i in used]
    print(f"修正后平均色度 = **{sum(cms2)/len(cms2):.1f}**"
          f"（实测 {sum(cms)/len(cms):.1f}，标称 {sum(cns)/len(cns):.1f}）")
    lf2 = [rgb_to_lab(fixed2[i])[0] for i in used]
    print(f"修正后明度范围 = **{min(lf2):.1f} ~ {max(lf2):.1f}**"
          f"（实测 {min(lm):.1f} ~ {max(lm):.1f}）")

    print()
    print(f"- 实测平均色度 {sum(cms)/len(cms):.1f} -> 修正后 {sum(cms2)/len(cms2):.1f}")
    for tag, pal in (("实测", mea), ("修正后", fixed2)):
        hi = [i for i in used if rgb_to_lab(pal[i])[0] > 60]
        print(f"- L*>60 的档位（{tag}）：**{len(hi)} 个**")

    # ---------------------------------------------- 导出，供上板对比
    out_csv = os.path.join(DOCS, "palette_qual_去污染.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "R", "G", "B", "HEX", "used", "source"])
        for i in range(64):
            r, g, bl = fixed2[i]
            src = "measured-minus-specular" if i in used else "nominal"
            w.writerow([i, r, g, bl, f"#{r:02X}{g:02X}{bl:02X}",
                        "Y" if i in used else "", src])
    print(f"\n导出 -> {out_csv}")

    act = os.path.join(os.path.dirname(HERE), "host", "act", "48-color-qual-dc.act")
    with open(act, "wb") as fh:
        for i in range(64):
            fh.write(bytes(fixed2[i]))
    print(f"导出 -> {act}（{os.path.getsize(act)} 字节）")


if __name__ == "__main__":
    main()

