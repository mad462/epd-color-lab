#!/usr/bin/env python3
"""从 FrameFilm 抠出 48 色色板。

**权威来源是 `tools/ForFilm/js/convert.js` 里的数组** —— 那是代码实际运行的。
`docs/film/film.md` §10.6 的表只作交叉核对（那张表 R/G/B 和 HEX 两列逐行有冲突）。

输出：
  palette_qual.csv / palette_fast.csv  （index,R,G,B,HEX,doc_Hex,doc_valid）
"""

import csv
import os
import re

ROOT = os.environ.get("FRAMEFILM_DIR", "")
if not ROOT:
    sys.exit("请先设置环境变量 FRAMEFILM_DIR 指向 FrameFilm 仓库根目录")
JS = os.path.join(ROOT, r"tools\ForFilm\js\convert.js")
MD = os.path.join(ROOT, r"docs\film\film.md")
OUT = r"\docs"

CAND_FAST = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
             20, 21, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
             38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53,
             54, 55, 56, 57, 58, 59, 63]
CAND_QUAL = [3, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16, 17, 18, 19, 21,
             24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40,
             41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56,
             57, 58, 59, 63]


def parse_js():
    text = open(JS, encoding="utf-8", errors="replace").read()
    out = {}
    for var, key in (("CF_PALETTE_FAST", "fast"), ("CF_PALETTE_QUAL", "qual")):
        m = re.search(r"var\s+" + var + r"\s*=\s*\[(.*?)\];", text, re.S)
        if not m:
            raise SystemExit(f"找不到 {var}")
        rows = re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", m.group(1))
        out[key] = [(int(a), int(b), int(c)) for a, b, c in rows]
        print(f"{key}: convert.js 里 {len(out[key])} 项")
    return out


def parse_md():
    """film.md 的表：index | R | G | B | HEX | 有效"""
    text = open(MD, encoding="utf-8", errors="replace").read().splitlines()
    out = {"fast": {}, "qual": {}}
    cur = None
    pat = re.compile(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*`?(#[0-9A-Fa-f]{6})`?\s*\|\s*(Y?)\s*\|")
    for ln in text:
        if "10.6.1" in ln:
            cur = "fast"
        elif "10.6.2" in ln:
            cur = "qual"
        if cur is None:
            continue
        m = pat.match(ln.strip())
        if m:
            out[cur][int(m.group(1))] = (m.group(5), m.group(6) == "Y")
    return out


def main():
    js = parse_js()
    md = parse_md()
    os.makedirs(OUT, exist_ok=True)

    for key in ("fast", "qual"):
        rows = js[key]
        cand = CAND_FAST if key == "fast" else CAND_QUAL
        path = os.path.join(OUT, f"palette_{key}.csv")
        mismatch = 0
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["index", "R", "G", "B", "HEX", "used", "doc_HEX", "doc_valid"])
            for i, (r, g, b) in enumerate(rows):
                hexv = f"#{r:02X}{g:02X}{b:02X}"
                doc_hex, doc_valid = md[key].get(i, ("", False))
                if doc_hex and doc_hex.upper() != hexv and doc_valid:
                    mismatch += 1
                w.writerow([i, r, g, b, hexv, "Y" if i in cand else "",
                            doc_hex, "Y" if doc_valid else ""])
        print(f"{key}: 写到 {path}；与 film.md 的 HEX 冲突 {mismatch} 处")

    # 打印 8x8 结构
    for key, cand, title in (("qual", CAND_QUAL, "ColorQual 46/52 色（3 相）"),
                             ("fast", CAND_FAST, "ColorFast 55/57 色（2 相）")):
        rows = js[key]
        print(f"\n=== rgb_{key} 8x8（{title}）行=高3bit 列=低3bit，· = 未用 ===")
        print("       " + " ".join(f" 低{lo}   " for lo in range(8)))
        for hi in range(8):
            cells = []
            for lo in range(8):
                i = hi * 8 + lo
                r, g, b = rows[i]
                cells.append("   ·    " if i not in cand else f" #{r:02X}{g:02X}{b:02X}")
            print(f"  高{hi}  " + " ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
