#!/usr/bin/env python3
"""从 FrameFilm 的 hal_epd_370.c 里把 9 张 799 字节波形表抠出来，生成 C 文件。

用法：
    python extract_waveforms.py

输入：<FRAMEFILM_DIR>\\firmware\\frame_film_ark\\components\\film_hal\\src\\hal_epd_370.c
      （用环境变量 FRAMEFILM_DIR 指定 FrameFilm 仓库根目录）
输出：..\\main\\waveforms.c / waveforms.h
"""

import os
import re
import sys

SRC = (os.environ.get("FRAMEFILM_DIR", "") + r"\firmware\frame_film_ark"
       r"\components\film_hal\src\hal_epd_370.c")
OUT = r"\main"

# 想要哪些波形（其余不带，省 flash）
WANTED = [
    "full_clear",
    "qual_phase1",
    "qual_phase2",
    "qual_phase3",
    "fast_color_phase1",
    "fast_color_phase2",
    "restore_after_fast_color",
    "mono_fast",
    "mono_clear",
]


def main():
    text = open(SRC, encoding="utf-8", errors="replace").read()

    tables = {}
    for name in WANTED:
        m = re.search(
            r"static\s+const\s+uint8_t\s+" + re.escape(name) + r"\s*\[\s*799\s*\]\s*=\s*\{(.*?)\}\s*;",
            text, re.S)
        if not m:
            print(f"!! 找不到 {name}")
            continue
        vals = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{1,2})", m.group(1))]
        if len(vals) != 799:
            print(f"!! {name} 长度是 {len(vals)}，不是 799")
            continue
        tables[name] = vals
        print(f"OK  {name:26s} 799 字节")

    if not tables:
        sys.exit("一个都没提取到")

    os.makedirs(OUT, exist_ok=True)

    with open(os.path.join(OUT, "waveforms.h"), "w", encoding="utf-8") as fh:
        fh.write("/* 自动生成，不要手改 —— 源：FrameFilm hal_epd_370.c (GPL-3.0) */\n")
        fh.write("#ifndef WAVEFORMS_H\n#define WAVEFORMS_H\n\n")
        fh.write("#include <stdint.h>\n\n")
        fh.write("#define WF_LEN 799\n\n")
        for name in tables:
            fh.write(f"extern const uint8_t wf_{name}[WF_LEN];\n")
        fh.write("\n#endif\n")

    with open(os.path.join(OUT, "waveforms.c"), "w", encoding="utf-8") as fh:
        fh.write("/* 自动生成，不要手改。\n")
        fh.write(" * 源：FrameFilm firmware/frame_film_ark/components/film_hal/src/hal_epd_370.c\n")
        fh.write(" * 许可：GPL-3.0-or-later —— 自用/评估可以；要进闭源产品请先处理许可。\n")
        fh.write(" * 这批波形是针对 3.7\" JD7601 / GDEH037E01 调的。\n */\n\n")
        fh.write('#include "waveforms.h"\n\n')
        for name, vals in tables.items():
            fh.write(f"const uint8_t wf_{name}[WF_LEN] = {{\n")
            for i in range(0, len(vals), 16):
                row = ", ".join(f"0x{v:02X}" for v in vals[i:i + 16])
                fh.write(f"    {row},\n")
            fh.write("};\n\n")

    print(f"\n输出到 {OUT}\\waveforms.c / .h，共 {len(tables)} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
