#!/usr/bin/env python3
"""从抠出来的 CSV 生成一份可读的色板说明文档。

重点是把 64 个索引按 `index = high*8 + low` 的**结构**排成 8x8 矩阵 ——
这样"为什么颜色不均匀"一眼就能看出来。
"""

import csv
import os

DOCS = r"\docs"

# convert.js 里实际用的候选索引（比 film.md 标的 55/46 多几个）
CAND_FAST = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
             20, 21, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
             38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53,
             54, 55, 56, 57, 58, 59, 63]
CAND_QUAL = [3, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16, 17, 18, 19, 21,
             24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40,
             41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56,
             57, 58, 59, 63]


def load(name):
    rows = {}
    with open(os.path.join(DOCS, f"palette_{name}.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows[int(r["index"])] = (int(r["R"]), int(r["G"]), int(r["B"]), r["HEX"])
    return rows


def matrix(rows, cand, title, out):
    out.append(f"### {title}\n")
    out.append("`index = 高3bit × 8 + 低3bit`。行 = 高 3bit（色系），列 = 低 3bit（档位）。\n")
    out.append("标记：`·` = 未使用/占位；其余为 RGB 值。\n")
    out.append("")
    out.append("| 高\\低 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for hi in range(8):
        cells = []
        for lo in range(8):
            i = hi * 8 + lo
            r, g, b, _ = rows[i]
            if i not in cand:
                cells.append("·")
            else:
                cells.append(f"`{r:02X}{g:02X}{b:02X}`")
        out.append(f"| **{hi}** | " + " | ".join(cells) + " |")
    out.append("")
    out.append(f"有效候选色 **{len(cand)}** 个，占 64 格里的 {len(cand)/64*100:.0f}%。\n")


def main():
    fast = load("fast")
    qual = load("qual")

    out = []
    out.append("# 3.7″ E6 48 色色板说明（FrameFilm）\n")
    out.append("> 数据源：`FrameFilm/docs/film/film.md` §10.6.1 / §10.6.2（64 索引全表）")
    out.append("> 代码实现：`FrameFilm/tools/ForFilm/js/convert.js`（`CF_PALETTE_FAST` / `CF_PALETTE_QUAL`）\n")
    out.append("抠好的 CSV：`palette_qual.csv`（ColorQual，3 相）/ `palette_fast.csv`（ColorFast，2 相）\n")
    out.append("---\n")

    out.append("## 一、为什么颜色「不均匀」\n")
    out.append("因为 8bpp 索引不是线性的，而是 **`高 3bit × 8 + 低 3bit`** 的组合：\n")
    out.append("- **高 3bit** 在 ColorQual 的第一相里决定「落在哪个色系」（8 个）")
    out.append("- **低 3bit** 在第三相里决定「在这个色系里走多深」（8 档）\n")
    out.append("所以 64 格里：\n")
    out.append("- 8 个色系之间的**覆盖范围天差地别**（有的整族都在暗部，有的跨亮部）")
    out.append("- 有相当一部分索引是**占位/未使用**（表里标 `·`），硬填进去只会得到重复或脏色\n")
    out.append("**这也解释了刚才那张索引卡为什么看着是 64 格：我把 0~63 全填满了。**")
    out.append("真正该用的只有下面列出的候选集。\n")
    out.append("---\n")

    out.append("## 二、ColorQual（3 相，高质量，46 色）\n")
    matrix(qual, CAND_QUAL, "8x8 结构（rgb_qual）", out)
    out.append("**ColorQual 的特点**：")
    out.append("- **没有真正的黑** —— 最暗的是 index 3 = `#1F192B`（深紫），不是纯黑")
    out.append("- 白只有 index 15 = `#FFFFFF`")
    out.append("- 暗部偏蓝紫，亮部偏黄绿；**红/品红区比较弱**（40~47 那一族偏紫）\n")
    out.append("---\n")

    out.append("## 三、ColorFast（2 相，快刷，55 色）\n")
    matrix(fast, CAND_FAST, "8x8 结构（rgb_fast）", out)
    out.append("**ColorFast 的特点**：")
    out.append("- 有 index 0 / 20 = 黑，index 21 = 白 —— 明暗跨度比 ColorQual 大")
    out.append("- 但只有 2 相，**色准略低**\n")
    out.append("---\n")

    out.append("## 四、两张表怎么选\n")
    out.append("| | ColorQual（`0x02`） | ColorFast（`0x03`） |")
    out.append("|---|---|---|")
    out.append("| 相数 | 3 | 2 |")
    out.append("| 候选色 | 52 | 57 |")
    out.append("| 实测耗时（我们这块屏） | 5.6 + 6.4 + 0.12 s | 未测 |")
    out.append("| 黑白 | **无真黑**，白只有 1 个 | 有真黑，白 1 个 |")
    out.append("| 适合 | 照片、需要细腻过渡 | 需要对比度 / 更快 |\n")
    out.append("---\n")

    out.append("## 五、量化时要注意的\n")
    out.append("`convert.js` 的选色规则（照抄）：")
    out.append("- **D65 CIELAB 最近邻**，**明度权重 ×2**")
    out.append("  （注释原文：墨水屏上明度保真比色度更重要，灰阶梯度也更单调）")
    out.append("- 只在 `candidateIndexes` 里选，占位索引不参与\n")
    out.append("⚠️ `film.md` 的 `有效` 列和 `convert.js` 的候选集**不一致**：")
    out.append("文档标 55 / 46，代码数组数出来是 **57 / 52**。**以代码为准**。\n")
    out.append("⚠️ `film.md` 表里的 `HEX` 列有明显笔误（index 0~2 的 R/G/B 是白 `255,255,255`，")
    out.append("HEX 却写 `#000000`）。**用之前逐项核一遍。**\n")

    path = os.path.join(DOCS, "色板说明.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print(f"已写入 {path}")

    # 顺带在终端把 qual 的矩阵打出来
    print("\n=== rgb_qual 8x8（行=高3bit, 列=低3bit，· = 未使用）===")
    print("      " + " ".join(f"  低{lo}  " for lo in range(8)))
    for hi in range(8):
        cells = []
        for lo in range(8):
            i = hi * 8 + lo
            r, g, b, _ = qual[i]
            cells.append("   ·    " if i not in CAND_QUAL else f"#{r:02X}{g:02X}{b:02X}")
        print(f"高{hi}  " + " ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
