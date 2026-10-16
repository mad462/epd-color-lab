#!/usr/bin/env python3
"""WiFi 配网助手 —— 用串口给板子配网。

为什么要有这个：固件里**不写** WiFi 密码（这仓库要开源，写进去等于公开）。
改成配一次、存进设备的 NVS，掉电不丢。

为什么不直接在串口监视器里敲 W：
  密码要一个个字符敲进去、还不能回显，容易敲错。这个脚本帮你处理交互、
  顺便把结果读出来告诉你。

用法：
    python wifi_setup.py                       # 交互式问 SSID 和密码
    python wifi_setup.py -s MyWiFi             # 只问密码
    python wifi_setup.py -s MyWiFi -p 12345678 # 全给（注意：密码会进命令历史）
    python wifi_setup.py --clear               # 清掉设备里保存的 WiFi
    python wifi_setup.py --port COM7

密码从终端读时用 getpass —— **不回显**，也不会进 PowerShell 的历史记录。
"""

import argparse
import getpass
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("需要 pyserial：pip install pyserial")

BAUD = 115200
IP_RE = re.compile(r"IP\s*=\s*(\d+\.\d+\.\d+\.\d+)")


def wait_for(ser, needles, timeout=15.0, quiet=True):
    """读到 needles 里任意一个就返回 (命中的那个, 全部输出)。超时返回 (None, 输出)。"""
    buf, t0 = [], time.time()
    while time.time() - t0 < timeout:
        line = ser.readline()
        if not line:
            continue
        text = line.decode("utf-8", "replace").rstrip()
        buf.append(text)
        if not quiet:
            print("    | " + text)
        for n in needles:
            if n in text:
                return n, buf
    return None, buf


def main():
    ap = argparse.ArgumentParser(description="给板子配 WiFi（存进设备 NVS）")
    ap.add_argument("--port", default="COM6", help="串口，默认 COM6")
    ap.add_argument("-s", "--ssid", help="WiFi 名称")
    ap.add_argument("-p", "--password", help="WiFi 密码（不给就交互输入，不回显）")
    ap.add_argument("--clear", action="store_true", help="清掉设备里保存的 WiFi")
    ap.add_argument("-v", "--verbose", action="store_true", help="把设备输出全打出来")
    a = ap.parse_args()

    print(f"打开 {a.port} …")
    try:
        ser = serial.Serial(a.port, BAUD, timeout=0.3)
    except Exception as e:
        sys.exit(f"打不开 {a.port}：{e}\n（是不是串口监视器还开着？同一时刻只能一个程序用）")

    try:
        ser.setDTR(False)
        ser.setRTS(False)
        # 开串口会复位板子，等它起来
        print("等板子启动（打开串口会复位一次）…")
        wait_for(ser, ["============", "输入N进入下一个"], timeout=15, quiet=not a.verbose)

        ser.reset_input_buffer()
        print("发送配网命令 W …\n")
        ser.write(b"W")
        ser.flush()

        if a.clear:
            hit, _ = wait_for(ser, ["SSID）:", "SSID）:"], timeout=10, quiet=not a.verbose)
            if not hit:
                sys.exit("设备没进配网模式。固件是新的吗（要有 W 命令）？")
            ser.write(b"S\r")
            ser.flush()
            _, out = wait_for(ser, ["已清除", "失败"], timeout=10, quiet=not a.verbose)
            print("  " + "\n  ".join(out[-3:]))
            return

        hit, _ = wait_for(ser, ["SSID）:"], timeout=10, quiet=not a.verbose)
        if not hit:
            sys.exit("设备没进配网模式。固件是新的吗（要有 W 命令）？")

        ssid = a.ssid or input("WiFi 名称（SSID）: ").strip()
        if not ssid:
            sys.exit("SSID 不能为空")
        ser.write(ssid.encode("utf-8") + b"\r")
        ser.flush()

        hit, _ = wait_for(ser, ["密码"], timeout=10, quiet=not a.verbose)
        if not hit:
            sys.exit("没等到密码提示")

        if a.password is not None:
            pwd = a.password
        else:
            pwd = getpass.getpass("WiFi 密码（不回显）: ")
        ser.write(pwd.encode("utf-8") + b"\r")
        ser.flush()

        print("\n等下设备连接", end="", flush=True)
        t0 = time.time()
        while time.time() - t0 < 20:
            line = ser.readline()
            if not line:
                print(".", end="", flush=True)
                continue
            text = line.decode("utf-8", "replace").rstrip()
            if a.verbose and text:
                print("\n    | " + text, end="")
            m = IP_RE.search(text)
            if m:
                print(f"\n\n✓ 配网成功，设备 IP = {m.group(1)}")
                print(f"  网页传图地址： http://{m.group(1)}/")
                return
            if "没拿到 IP" in text or "失败" in text:
                print(f"\n\n✗ 配网失败：{text}")
                print("  检查：SSID / 密码对吗？板子在这个 WiFi 的覆盖范围内吗？")
                sys.exit(1)
        print("\n\n✗ 超时。加 -v 看看设备到底说了什么。")
        sys.exit(1)
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
