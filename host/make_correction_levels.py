#!/usr/bin/env python3
"""生成几档不同校正强度的色板，供上板对比。

背景：测色仪器（林上 LS173）是 D/8 SCI 几何，把镜面反射也算进读数，
导致暗部被抬高、色度被稀释。我们推出镜面项 b 大概在 4.4%~7.3% 之间，
但**没有仪器可以复测**，所以改成用眼睛定：生成几档，上板对比挑一个。

模型（线性反射率空间）：真实 = 实测 − b

档位：
    b = 0     实测原值（不校正）
    b = 2.5%  保守
    b = 5.0%  按面板对比度物理边界推的（对比度落在 15~22 区间）
    b = 7.5% 那档会过度外推（对比度拉到 139:1，物理上不可能），所以不放进来。

第三个是推荐值，但最终由板上的观感决定 —— 哪档暗部层次更好、
颜色更浓，就用哪档。
"""

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")
ACT = os.path.join(HERE, "act")

LEVELS = [
    (0.000, "48-color-qual.act", "原值"),
    (0.025, "48-color-qual-c025.act", "2.5%"),
    (0.050, "48-color-qual-c050.act", "5.0%"),
]

SRGB = [0.0] * 256
for _i in range(256):
    _c = _i / 255.0
    SRGB[_i] = _c / 12.92 if _c <= 0.04045 else ((_c + 0.055) / 1.055) ** 2.4


def lin_to_srgb(v):
    v = max(0.0, min(1.0, v))
    return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055


def main():
    rows = list(csv.DictReader(open(os.path.join(DOCS, "palette_qual_measured.csv"),
                                    encoding="utf-8")))
    pal = [None] * 64
    used = []
    for r in rows:
        i = int(r["index"])
        pal[i] = (int(r["R"]), int(r["G"]), int(r["B"]))
        if r.get("used") == "Y":
            used.append(i)

    print(f"{'档位':>8s}  {'输出文件':28s}  {'白(15)':>8s} {'黑(3)':>8s} {'对比度':>8s}")
    print("-" * 70)

    for b, fname, tag in LEVELS:
        out = []
        for i in range(64):
            rgb = []
            for c in range(3):
                v = max(0.0, SRGB[pal[i][c]] - b)
                rgb.append(max(0, min(255, round(lin_to_srgb(v) * 255))))
            out.append(tuple(rgb))

        with open(os.path.join(ACT, fname), "wb") as fh:
            for c in out:
                fh.write(bytes(c))

        lex = lambda rgb: sum(SRGB[v] for v in rgb) / 3.0
        W, B = lex(out[15]), lex(out[3])
        print(f"{tag or '原值':>8s}  {fname:28s}  #{out[15][0]:02X}{out[15][1]:02X}{out[15][2]:02X}"
              f"  #{out[3][0]:02X}{out[3][1]:02X}{out[3][2]:02X}  {W/max(B,1e-6):6.1f}:1")

    # 旧的 -dc 名字统一成 -c050，保持一处一致
    old = os.path.join(ACT, "48-color-qual-dc.act")
    if os.path.exists(old):
        os.remove(old)
        print("\n（删掉了旧的 48-color-qual-dc.act，内容和 c050 一样）")


if __name__ == "__main__":
    main()
