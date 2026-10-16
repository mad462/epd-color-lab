#!/usr/bin/env python3
"""把「串口接图」和「TF 卡存图/浏览」这两块已经用不上的代码，
从固件里摘出来存进 legacy/，并顺手从构建里移除。

为什么留档而不是删掉：
  这两条路是把 48 色跑通时的主要调试手段（串口 115200 下一张图 30 秒，
  但胜在不用网络）。以后要离线烧一张卡、或者 WiFi 出问题想退回串口，
  这些代码还能直接用。删了就没了。

注意：这里**不用正则去匹配函数体**。第一版用
    case '[^\n]*\n(?:\s+[^\n]*\n)*?\s*break;\n
  —— \s 会把换行也吃掉，配上惰性重复就是指数级回溯，直接把进程卡死 20 分钟。
  改成逐行数花括号配平，线性、可控。

跑完记得 pio run 验证编译。
"""

import os
import re
import sys

ROOT = r"C:\FUCKEDA\e6-48color"
LEG = os.path.join(ROOT, "legacy", "firmware")

MAIN_FUNCS = [
    "recv_and_display", "rot_name", "sd_print_entry", "sd_scan", "sd_show",
    "recv_file_to_sd", "jump_input",
    "device_rescan", "device_scan", "device_show", "device_save_and_show",
]
IMAGE_FUNCS = ["image_recv_serial", "image_recv_file"]

DROP_CASES = set("ljn pdrwuf".replace(" ", ""))

TYPE_RE = re.compile(r"^(static\s+)?(esp_err_t|void|int|bool|size_t|uint8_t|"
                     r"int32_t|const\s+char\s*\*)\b")


def find_block(lines, name):
    """返回 (起始行, 结束行)；数花括号配平，不用正则。"""
    want = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    for idx, ln in enumerate(lines):
        if not TYPE_RE.match(ln) or not want.search(ln):
            continue
        depth, opened = 0, False
        for k in range(idx, len(lines)):
            depth += lines[k].count("{") - lines[k].count("}")
            if "{" in lines[k]:
                opened = True
            if opened and depth <= 0:
                return idx, k
    return None


def with_leading_comment(lines, start):
    """把函数上面那段注释也一起搬走。"""
    s = start
    while s > 0:
        prev = lines[s - 1].strip()
        if prev.startswith("//"):
            s -= 1
        elif prev.startswith("*") or prev.startswith("/*"):
            s -= 1
            if prev.startswith("/*"):
                break
        else:
            break
    return s


def strip_funcs(path, names, sink):
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    base = os.path.basename(path)
    for n in names:
        r = find_block(lines, n)
        if not r:
            print(f"    !! {base}: 找不到 {n}", flush=True)
            continue
        a, b = with_leading_comment(lines, r[0]), r[1]
        sink.append(f"/* ======== {base} :: {n} ======== */\n")
        sink.append("\n".join(lines[a:b + 1]).rstrip() + "\n\n")
        del lines[a:b + 1]
        print(f"    摘出 {n}（{b - a + 1} 行）", flush=True)
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines))


def drop_cli_cases(path):
    lines = open(path, encoding="utf-8").read().split("\n")

    # 只在 cli_loop 体内动刀。cal_input 也有 switch，
    # 而且里面 case 'F'（切换色板）跟这里的 'f' 只差大小写。
    rng = find_block(lines, "cli_loop")
    if not rng:
        print("    !! 找不到 cli_loop", flush=True)
        return
    a, b = rng

    out, i, removed = lines[:a], a, 0
    case_re = re.compile(r"^\s*case\s+'")
    default_re = re.compile(r"^\s*default\s*:")
    while i <= b:
        m = case_re.match(lines[i])
        if m:
            letter = re.search(r"'([a-z])'", lines[i])      # 只小写
            if letter and letter.group(1) in DROP_CASES:
                # 往后吃到第一个 break / return，或撞上下一个分支标签。
                # 不要用花括号配平 —— case 体通常是 { ... break; }，
                # break 在花括号**里面**，按 depth<=0 判断会一路吃到文件尾。
                i += 1
                while i <= b:
                    if case_re.match(lines[i]) or default_re.match(lines[i]):
                        break
                    hit = ("break;" in lines[i]) or ("return" in lines[i])
                    i += 1
                    if hit:
                        if i <= b and lines[i].strip() in ("}", "};"):
                            i += 1
                        break
                removed += 1
                continue
        out.append(lines[i])
        i += 1

    out += lines[b + 1:]
    text = "\n".join(out)
    for key in ("l  扫描 TF 卡", "j  输编号跳转", "n  下一张", "r  切换竖版",
                "w  串口收一个文件", "u  串口收一张", "f  同上但用 ColorFast",
                "l  扫描 TF", "j  输编号"):
        text = re.sub(r"\n[^\n]*" + re.escape(key) + r"[^\n]*", "", text)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"    删了 {removed} 个 CLI 分支", flush=True)

    text = "\n".join(out)
    for key in ("l  扫描 TF 卡", "j  输编号跳转", "n  下一张", "r  切换竖版",
                "w  串口收一个文件", "u  串口收一张", "f  同上但用 ColorFast"):
        text = re.sub(r"\n[^\n]*" + re.escape(key) + r"[^\n]*", "", text)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"    删了 {removed} 个 CLI 分支", flush=True)


def main():
    os.makedirs(LEG, exist_ok=True)
    sink = [
        "/* 从固件里摘出来的「串口接图」和「TF 卡存图/浏览」代码。\n"
        " * 自动生成，**不参与构建** —— 留档用。\n"
        " *\n"
        " * 这两条路是把 48 色跑通时的主要调试手段：串口 115200 传一张图 30 秒，\n"
        " * 但胜在不需要网络。现在图片走 WiFi 直连（http://<设备IP>/raw，\n"
        " * 338KB 只要几秒），串口只留着读设备 IP。\n"
        " *\n"
        " * 要退回去用：把下面的函数贴回 main.c / image.c，\n"
        " * 再把 cli_loop 里对应的 case 分支和帮助文本恢复即可。\n"
        " */\n\n"
    ]

    print("  main.c:", flush=True)
    strip_funcs(os.path.join(ROOT, "main", "main.c"), MAIN_FUNCS, sink)
    drop_cli_cases(os.path.join(ROOT, "main", "main.c"))

    print("  image.c:", flush=True)
    strip_funcs(os.path.join(ROOT, "main", "image.c"), IMAGE_FUNCS, sink)

    out = os.path.join(LEG, "serial_receive_and_tf.c.txt")
    open(out, "w", encoding="utf-8", newline="\n").write("".join(sink))
    print(f"\n  -> {out}  ({os.path.getsize(out)} 字节)", flush=True)


if __name__ == "__main__":
    main()
