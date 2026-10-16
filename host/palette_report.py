#!/usr/bin/env python3
"""从实测色板生成文档要用的权威数据表。

手抄数据容易出错，所以文档里的表格全部由这个脚本生成。
跑法：python palette_report.py > 报告.md
"""

import csv
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")


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


def load(path):
    pal, used = [None] * 64, []
    for row in csv.DictReader(open(path, encoding="utf-8")):
        i = int(row["index"])
        pal[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
        if row.get("used") == "Y":
            used.append(i)
    return pal, used


def main():
    mea, used = load(os.path.join(DOCS, "palette_qual_measured.csv"))
    nom, _ = load(os.path.join(DOCS, "palette_qual.csv"))

    rows = []
    for i in used:
        L, A, B = rgb_to_lab(mea[i])
        rows.append((L, i, mea[i], A, B, math.hypot(A, B)))

    print("## 实测色板（按明度排序）\n")
    print("| 索引 | 实测 HEX | L* | a* | b* | C* | 备注 |")
    print("|---:|---|---:|---:|---:|---:|---|")
    for L, i, rgb, A, B, C in sorted(rows):
        note = []
        if B > 20:
            note.append("偏黄")
        if A > 15:
            note.append("偏红")
        if C < 3:
            note.append("接近中性")
        print(f"| {i} | #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X} | {L:.1f} | "
              f"{A:+.1f} | {B:+.1f} | {C:.1f} | {'/'.join(note)} |")

    print("\n## 明度分布（每个 10 L* 区间有几个档位）\n")
    print("| L* 区间 | 档位数 | 说明 |")
    print("|---|---:|---|")
    for lo in range(30, 80, 10):
        n = sum(1 for L, *_ in rows if lo <= L < lo + 10)
        bar = "#" * n
        note = ""
        if n <= 4 and lo >= 60:
            note = "**亮部档位太少**"
        elif n == 0:
            note = "**空档**"
        print(f"| {lo}~{lo+10} | {n} | {bar} {note} |")

    print("\n## 结构性空洞\n")
    mid = [r for r in rows if 40 <= r[0] <= 52]
    print(f"L* 40~52 区间共 {len(mid)} 个档位，其中 chroma < 8 的（接近中性）：")
    neutrals = [r for r in mid if r[5] < 8]
    if neutrals:
        for L, i, rgb, A, B, C in sorted(neutrals):
            print(f"  - 索引 {i} #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}  L*{L:.1f} C*{C:.1f}")
    else:
        print("  **一个都没有** —— 中间调的中性色无处可去")

    print("\n## 实测 vs 标称：色度损失\n")
    print("| 索引 | 标称 HEX | 实测 HEX | 标称 C* | 实测 C* | 保留比例 |")
    print("|---:|---|---|---:|---:|---:|")
    tot_n = tot_m = 0
    for i in used:
        ln, an, bn = rgb_to_lab(nom[i])
        lm, am, bm = rgb_to_lab(mea[i])
        cn, cm = math.hypot(an, bn), math.hypot(am, bm)
        if cn < 3:
            continue
        tot_n += cn
        tot_m += cm
        if i in (3, 8, 15, 32, 39, 48, 55, 59):
            print(f"| {i} | #{nom[i][0]:02X}{nom[i][1]:02X}{nom[i][2]:02X} | "
                  f"#{mea[i][0]:02X}{mea[i][1]:02X}{mea[i][2]:02X} | "
                  f"{cn:.1f} | {cm:.1f} | {cm/cn:.0%} |")
    print(f"\n全部有效档位平均色度：标称 {tot_n/len(used):.1f} → 实测 {tot_m/len(used):.1f} "
          f"（保留 {tot_m/tot_n:.0%}）")

    # 重复色
    print("\n## 重复的实测色（52 个编号 → 46 个不同颜色）\n")
    seen = {}
    for i in used:
        seen.setdefault(mea[i], []).append(i)
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    print("| 实测 HEX | 重复的原始索引 | 个数 |")
    print("|---|---|---:|")
    for rgb, idxs in sorted(dup.items(), key=lambda kv: kv[1][0]):
        print(f"| #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X} | {', '.join(map(str, idxs))} | {len(idxs)} |")
    print(f"\n不同颜色共 **{len(seen)}** 个")


if __name__ == "__main__":
    main()
