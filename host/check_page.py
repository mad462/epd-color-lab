#!/usr/bin/env python3
"""抽查 device.html：JS 里 el('xxx') 引用的每个 id，HTML 里都得真的存在。

为什么要有这个：渲染函数里只要引用了一个不存在的元素，就会在
textContent 赋值时抛异常，导致后面的 state.idx 设不上 —— 表现是
"发送按钮一直按不了"，但报错在控制台里，很容易漏看。
上一轮就是这么挂的，所以做成常驻检查。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "device.html")


def main():
    html = open(PAGE, encoding="utf-8").read()
    m = re.search(r'<script type="module">(.*?)</script>', html, re.S)
    if not m:
        print("找不到 <script type=\"module\">")
        return 1
    script = m.group(1)
    body = html[:html.index("<script")]

    have = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', body))
    used = set(re.findall(r"el\('([A-Za-z0-9_-]+)'\)", script))

    missing = sorted(used - have)
    print(f"HTML id {len(have)} 个 · JS 引用 {len(used)} 个")

    bad = False
    if missing:
        bad = True
        print("\n!! JS 引用了但 HTML 里不存在的 id（一赋值就抛异常）:")
        for name in missing:
            n = len(re.findall(r"el\('" + name + r"'\)", script))
            print(f"     {name:16s} 引用 {n} 次")
    else:
        print("id 全部对得上")

    # 顺便查一下 render() 里写了哪些元素 —— 这些是"崩了就按不了按钮"的高危点
    rm = re.search(r"function render\(\)\s*\{(.*?)\n        \}", script, re.S)
    if rm:
        touched = sorted(set(re.findall(r"el\('([A-Za-z0-9_-]+)'\)", rm.group(1))))
        print(f"\nrender() 会写到的元素（任一缺失都会让按钮失灵）:")
        for name in touched:
            print(f"     {name:16s} {'OK' if name in have else '缺失 !!'}")

    # 滑杆必须配一个数字框（能直接输数值），漏了就没法精调
    ranges = re.findall(r'id="(r[A-Z][A-Za-z]*)"[^>]*type="range"', html)
    ranges += re.findall(r'type="range"[^>]*id="(r[A-Z][A-Za-z]*)"', html)
    ranges = sorted(set(ranges))
    if ranges:
        print(f"\n滑杆 {len(ranges)} 个，检查数字框配对:")
        for name in ranges:
            num = name + "Num"
            ok = f'id="{num}"' in html
            print(f"     {name:14s} -> {num:16s} {'OK' if ok else '缺数字框 !!'}")
            if not ok:
                bad = True

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
