#!/usr/bin/env python3
"""分析实测校准数据，生成实测色板。

输入：docs/校准表_qual.xlsx（Excel 存成了 .csv 扩展名，实际是 xlsx）
      —— 若不存在则退回读 docs/校准表_qual.csv

输出：
  docs/palette_qual_measured.csv   实测色板（索引 -> 实测 RGB），给量化器用
  docs/实测分析.md                  分析报告
  docs/校准表_qual.csv              规范化回标准 CSV（UTF-8）
"""

import csv
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")


# ------------------------------------------------------------------ 读 xlsx
def read_xlsx(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8")
        for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
            shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))

    sheet = [n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)][0]
    xml = z.read(sheet).decode("utf-8")

    rows = []
    for rm in re.finditer(r'<row[^>]*r="(\d+)"[^>]*>(.*?)</row>', xml, re.S):
        cells = {}
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', rm.group(2), re.S):
            col, attrs, body = cm.group(1), cm.group(2), cm.group(3)
            v = re.search(r"<v>(.*?)</v>", body, re.S)
            if not v:
                continue
            val = v.group(1)
            if 't="s"' in attrs:
                val = shared[int(val)] if int(val) < len(shared) else ""
            cells[col] = val
        rows.append(cells)
    return rows


def hex_to_rgb(s):
    s = (s or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def rgb_to_lab(rgb):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750)
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab_to_rgb(lab, gamma=True):
    """Lab(D65) -> sRGB，超出色域就截断"""
    L, a, b = lab
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200

    def finv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787

    x = finv(fx) * 0.95047
    y = finv(fy)
    z = finv(fz) * 1.08883

    r = x * 3.2404542 + y * -1.5371385 + z * -0.4985314
    g = x * -0.9692660 + y * 1.8760108 + z * 0.0415560
    bl = x * 0.0556434 + y * -0.2040259 + z * 1.0572252

    def enc(c):
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055

    return tuple(max(0, min(255, round(enc(v) * 255))) for v in (r, g, bl))


def normalize_range(pal, active):
    """把色板的 L* 从 [实测最暗, 实测最亮] 线性拉到 [0, 100]，a/b 同比例缩放。

    为什么这么做：面板只能出 L* 31~72 这一段。要让观感对，就得把**图像的
    0~100 映射到面板的 31~72**（在感知空间 L* 里线性映射），这样眼睛适应
    纸白之后看到的就是正确的关系。

    只按白点做增益（×1.49）是错的 —— 黑点没拉到 0，中间调被整体压低，
    结果就是又暗、对比度又高、高光还被挤成断层。
    """
    labs = [rgb_to_lab(c) for c in pal]
    Ls = [labs[i][0] for i in active]
    Lmin, Lmax = min(Ls), max(Ls)
    span = max(1e-6, Lmax - Lmin)

    out = []
    for (L, A, B) in labs:
        t = (L - Lmin) / span
        L2 = t * 100.0
        # a/b 按 L 的比例缩，保持色相、避免明度一变色度就崩
        k = (L2 / L) if L > 1e-6 else 1.0
        out.append(lab_to_rgb((L2, A * k, B * k)))
    return out, Lmin, Lmax


def de2000(lab1, lab2):
    import math
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb > 0 else 0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    if C1p * C2p == 0:
        dhp = 0
    elif dhp > 180:
        dhp -= 360
    elif dhp < -180:
        dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    hbp = h1p + h2p
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) > 180:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dTh = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dTh)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def main():
    xlsx = os.path.join(DOCS, "校准表_qual.xlsx")
    csv_in = os.path.join(DOCS, "校准表_qual.csv")

    # 用户把 xlsx 存成了 .csv 扩展名，两种都试
    if zipfile.is_zipfile(csv_in):
        src = csv_in
        rows = read_xlsx(src)
    elif os.path.exists(xlsx) and zipfile.is_zipfile(xlsx):
        rows = read_xlsx(xlsx)
    else:
        sys.exit("找不到可读的校准表（.csv 是 xlsx 格式，或提供 .xlsx）")

    data = []
    for cells in rows[1:]:                       # 跳过表头
        num = int(cells.get("A", 0) or 0)
        if num < 1:
            continue
        idx = int(cells.get("B", 0) or 0)
        nom = hex_to_rgb(cells.get("C"))
        mea = hex_to_rgb(cells.get("G"))
        data.append((num, idx, nom, mea))

    print(f"读到 {len(data)} 行，实测填了 {sum(1 for d in data if d[3])} 行")
    if not any(d[3] for d in data):
        sys.exit("实测列是空的")

    # ---------------------------------------------------------- 分析
    out = []
    out.append("# 48 色屏实测分析（ColorQual 3 相）\n")
    out.append(f"数据：`docs/校准表_qual.csv`（实为 xlsx），共 {len(data)} 个色\n")
    out.append("---\n")

    meas = [(num, idx, nom, mea) for num, idx, nom, mea in data if mea]

    # 重复
    groups = {}
    for num, idx, nom, mea in meas:
        groups.setdefault(mea, []).append(num)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    out.append("## 一、重复的实测色\n")
    out.append(f"52 个索引测出 **{len(groups)} 个不同的颜色**，"
               f"有 {sum(len(v) - 1 for v in dups.values())} 个是重复的。\n")
    if dups:
        out.append("| 实测HEX | 重复的编号 | 原始索引 |")
        out.append("|---|---|---|")
        for mea, nums in sorted(dups.items(), key=lambda kv: kv[1][0]):
            idxs = [str(d[1]) for d in meas if d[0] in nums]
            out.append(f"| `#{mea[0]:02X}{mea[1]:02X}{mea[2]:02X}` | "
                       f"{', '.join(map(str, nums))} | {', '.join(idxs)} |")
        out.append("")
        out.append("> 面板只有 6 种物理颜料，同一族的相邻档位分辨率不够时会落到同一档，"
                   "测出重复值是正常的。\n")

    # 动态范围
    labs = [(num, idx, nom, mea, rgb_to_lab(mea)) for num, idx, nom, mea in meas]
    Ls = [l[4][0] for l in labs]
    dark = min(labs, key=lambda x: x[4][0])
    light = max(labs, key=lambda x: x[4][0])
    out.append("## 二、动态范围（实测）\n")
    out.append(f"- **最暗**：编号 {dark[0]}（索引 {dark[1]}）`#{dark[3][0]:02X}{dark[3][1]:02X}{dark[3][2]:02X}`"
               f"　L\\* = {dark[4][0]:.1f}")
    out.append(f"- **最亮**：编号 {light[0]}（索引 {light[1]}）`#{light[3][0]:02X}{light[3][1]:02X}{light[3][2]:02X}`"
               f"　L\\* = {light[4][0]:.1f}")
    out.append(f"- 明度跨度 **ΔL\\* = {light[4][0] - dark[4][0]:.1f}**")
    out.append("")

    nom_white = [(n, i, nm, me) for n, i, nm, me in meas if nm and min(nm) > 240]
    if nom_white:
        n, i, nm, me = nom_white[0]
        out.append(f"> 标称白 `#FFFFFF` 实测是 `#{me[0]:02X}{me[1]:02X}{me[2]:02X}`。\n"
                   f"> 墨水屏白反射率通常 30~40%，对应 L\\* 62~70 —— **这个实测值是合理的**，\n"
                   f"> 反倒是标称的 `#FFFFFF` 属于理想值，不是这块屏真能出来的。\n")

    # 偏差
    devs = []
    for num, idx, nom, mea, lab in labs:
        if not nom:
            continue
        devs.append((de2000(rgb_to_lab(nom), lab), num, idx, nom, mea))
    devs.sort(reverse=True)
    out.append("## 三、和标称值差得最多的几个\n")
    out.append("| 编号 | 索引 | 标称 | 实测 | ΔE2000 |")
    out.append("|---|---|---|---|---|")
    for d, num, idx, nom, mea in devs[:10]:
        out.append(f"| {num} | {idx} | `#{nom[0]:02X}{nom[1]:02X}{nom[2]:02X}` | "
                   f"`#{mea[0]:02X}{mea[1]:02X}{mea[2]:02X}` | {d:.1f} |")
    out.append("")
    avg = sum(d[0] for d in devs) / len(devs)
    out.append(f"**平均 ΔE2000 = {avg:.1f}**，最大 {devs[0][0]:.1f}。")
    out.append("")

    # ---------------------------------------------------------- 写实测色板
    # 实测色板：原始索引(0..63) -> 实测 RGB。没测的用标称补
    nom_full = {}
    used_full = set()
    with open(os.path.join(DOCS, "palette_qual.csv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            i = int(row["index"])
            nom_full[i] = (int(row["R"]), int(row["G"]), int(row["B"]))
            if row.get("used") == "Y":
                used_full.add(i)

    measured_pal = dict(nom_full)
    measured_idx = set()
    for num, idx, nom, mea in meas:
        measured_pal[idx] = mea
        if mea:
            measured_idx.add(idx)

    path = os.path.join(DOCS, "palette_qual_measured.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        # 列名和 palette_qual.csv 对齐，这样 make_act / make_palette_table 能直接吃
        w.writerow(["index", "R", "G", "B", "HEX", "used", "source"])
        for i in range(64):
            r, g, b = measured_pal[i]
            src = "measured" if i in measured_idx else "nominal"
            w.writerow([i, r, g, b, f"#{r:02X}{g:02X}{b:02X}",
                        "Y" if i in used_full else "", src])
    print(f"实测色板 -> {path}")

    # 规范化回标准 CSV
    norm = os.path.join(DOCS, "校准表_qual_norm.csv")
    with open(norm, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["编号", "原始索引", "标称HEX", "实测HEX", "实测R", "实测G", "实测B"])
        for num, idx, nom, mea in data:
            w.writerow([num, idx,
                        f"#{nom[0]:02X}{nom[1]:02X}{nom[2]:02X}" if nom else "",
                        f"#{mea[0]:02X}{mea[1]:02X}{mea[2]:02X}" if mea else "",
                        mea[0] if mea else "", mea[1] if mea else "", mea[2] if mea else ""])
    print(f"规范化 CSV -> {norm}")

    md = os.path.join(DOCS, "实测分析.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print(f"分析报告 -> {md}")

    # ---------------------------------------------------------- 校准色板
    # 把实测色板的 L* 从 [31, 72] 线性拉到 [0, 100]（感知空间里做）。
    #
    # 为什么：面板只能出 L* 31~72 这一段，要让观感对，就得把图像的 0~100
    # 映射到面板的 31~72。只按白点做增益是错的 —— 黑点没拉到 0，
    # 中间调被整体压低 → 又暗、对比度高、高光挤成断层。
    #
    # 实测数据（`palette_qual_measured.csv`）保留原始值做参考。
    pal_list = [measured_pal[i] for i in range(64)]
    pal64, Lmin, Lmax = normalize_range(pal_list, sorted(used_full))
    print(f"实测明度范围 L* {Lmin:.1f} ~ {Lmax:.1f}，已线性拉到 0~100")
    print(f"  归一化后：索引3 -> #{pal64[3][0]:02X}{pal64[3][1]:02X}{pal64[3][2]:02X}"
          f"   索引15 -> #{pal64[15][0]:02X}{pal64[15][1]:02X}{pal64[15][2]:02X}")

    # ⚠️ 这个文件是**废弃产物**，名字里带「废弃」就是为了防止误用。
    #
    # 为什么废弃：把色板的 L* 从 [31,72] 拉到 [0,100] 时，色度按 k=L2/L 缩放。
    # 暗色 L 小 -> k 趋近 0 -> 暗部色度被抹平，量化器分不清暗部的蓝和红，
    # 会随机挑到偏红/偏紫的档位 —— 实测症状就是"暗部偏红"。
    #
    # 正确做法是**改图像、不改色板**（见 docs/颜色方法论.md 第六节）。
    # 量化请用 docs/palette_qual_measured.csv。
    path = os.path.join(DOCS, "废弃_Lstar归一化色板.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "R", "G", "B", "HEX", "used", "source"])
        for i in range(64):
            r, g, b = pal64[i]
            src = "measured+norm" if i in measured_idx else "nominal"
            w.writerow([i, r, g, b, f"#{r:02X}{g:02X}{b:02X}",
                        "Y" if i in used_full else "", src])
    print(f"校准色板（实测 + L* 全域归一化）-> {path}")

    print("\n" + "=" * 60)
    print("\n".join(out[:6]))
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
