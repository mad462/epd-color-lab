#!/usr/bin/env python3
"""生成校准表（CSV），供用仪器逐个测完填写。

列：编号, 原始索引, 标称HEX, 标称R, 标称G, 标称B, 实测HEX, 实测R, 实测G, 实测B

编号 = 固件校准模式里显示的编号（按有效槽位从 1 开始）。
实测那几列留空，你测完填上；也可以直接把「CAL,编号,索引,HEX,R,G,B」那行
后面补上实测值发回来。

用法：
    python make_cal_sheet.py
"""

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")


def make(profile, label):
    src = os.path.join(DOCS, f"palette_{profile}.csv")
    dst = os.path.join(DOCS, f"校准表_{profile}.csv")

    rows = []
    with open(src, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("used") != "Y":
                continue
            rows.append(row)

    with open(dst, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["编号", "原始索引", "标称HEX", "标称R", "标称G", "标称B",
                    "实测HEX", "实测R", "实测G", "实测B"])
        for n, row in enumerate(rows, 1):
            r, g, b = int(row["R"]), int(row["G"]), int(row["B"])
            w.writerow([n, int(row["index"]), f"#{r:02X}{g:02X}{b:02X}", r, g, b, "", "", "", ""])

    print(f"{label}: {len(rows)} 个色 -> {dst}")
    return rows


def main():
    q = make("qual", "ColorQual 3相")
    f = make("fast", "ColorFast 2相")

    md = os.path.join(DOCS, "校准说明.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# 48 色屏实测校准说明\n\n")
        fh.write("## 怎么测\n\n")
        fh.write("1. 串口里敲 `c` 进入校准模式（默认 ColorQual 3 相，共 "
                 f"{len(q)} 个色）\n")
        fh.write("2. 敲数字 + 回车跳到某个编号；`n`/`p` 下一个/上一个\n")
        fh.write("3. 每个颜色全屏刷一次，约 15 秒\n")
        fh.write("4. **仪器测屏幕中心**（满屏纯色，避开边框）\n")
        fh.write("5. 敲 `f` 可切到 ColorFast 2 相（共 %d 个色）\n" % len(f))
        fh.write("6. `x` 退出校准\n\n")
        fh.write("## 串口会打出什么\n\n")
        fh.write("```\n")
        fh.write("CAL,1,3,1F192B,31,25,43\n")
        fh.write("```\n\n")
        fh.write("格式是 `CAL,编号,原始索引,标称HEX,标称R,标称G,标称B`。\n")
        fh.write("把实测值补在后面发回来即可，例如：\n\n")
        fh.write("```\n")
        fh.write("CAL,1,3,1F192B,31,25,43  实测 #221C30 (34,28,48)\n")
        fh.write("```\n\n")
        fh.write("## 要填的表\n\n")
        fh.write(f"- `校准表_qual.csv` —— ColorQual 3 相，{len(q)} 个\n")
        fh.write(f"- `校准表_fast.csv` —— ColorFast 2 相，{len(f)} 个\n\n")
        fh.write("## 实测值拿来干嘛\n\n")
        fh.write("把标称色板换成**实测色板**后重新量化：\n\n")
        fh.write("- 量化时按「实际会渲染成什么颜色」来选，而不是按厂商标称值\n")
        fh.write("- 能纠正色偏，也能让抖动算出的中间色更接近真实观感\n")
        fh.write("- 实测色板只影响 host 侧的 .act，**不用重新烧固件**\n")
    print(f"说明 -> {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
