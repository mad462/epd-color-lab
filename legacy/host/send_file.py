#!/usr/bin/env python3
"""把一个文件从串口传到板子的 TF 卡上（对应固件的 'w' 命令）。

用途：不用拔卡、不用读卡器，直接把 BMP 推到板子上。
115200 下 346 KB 约 30 秒。

用法：
    python send_file.py photo.bmp
    python send_file.py photo.bmp --name my.bmp
"""

import argparse
import os
import struct
import sys
import time
import zlib

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

MAGIC = b"EPF"
MAX_NAME = 200


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--port", default="COM6")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--name", default=None, help="存到卡上的名字（默认用原文件名）")
    ap.add_argument("--pre-delay", type=float, default=2.0)
    ap.add_argument("-t", "--timeout", type=float, default=180.0)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        sys.exit(f"找不到 {args.file}")
    data = open(args.file, "rb").read()
    name = args.name or os.path.basename(args.file)
    nb = name.encode("utf-8")
    if len(nb) > MAX_NAME:
        sys.exit("文件名太长")
    if "/" in name or "\\" in name:
        sys.exit("文件名不能带路径分隔符")

    crc = zlib.crc32(data) & 0xFFFFFFFF
    header = MAGIC + bytes([len(nb)]) + nb + struct.pack("<II", len(data), crc)

    ser = serial.Serial(args.port, args.baud, timeout=0.3)
    ser.setDTR(False)
    ser.setRTS(False)
    try:
        time.sleep(0.5)
        ser.reset_input_buffer()
        if args.pre_delay > 0:
            print(f"先等 {args.pre_delay:.0f} 秒 ...")
            end = time.time() + args.pre_delay
            while time.time() < end:
                ser.readline()

        # 只敲一次 'w'。反复敲会在固件忙的时候积压，
        # 等固件进接收流程时把残留字节当成文件头（实测报过 magic=77 77 77）。
        print("[1/3] 发一次 'w'，等固件清完缓冲...")
        ser.write(b"w")
        ser.flush()

        armed = False
        deadline = time.time() + 120      # 开机可能要忙十几秒
        while time.time() < deadline:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            print("   ", text)
            if "开始发送头部" in text:
                armed = True
                break
        if not armed:
            sys.exit("固件一直没准备好（检查板子是否在跑、串口是否被别的程序占用）")

        print(f"[2/3] 发头部：{name}  {len(data)} 字节  CRC 0x{crc:08X}")
        ser.write(header)
        ser.flush()

        # 等固件说"开始发送数据"
        ok = False
        t_end = time.time() + 15
        while time.time() < t_end:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            print("   ", text)                      # 全部都回显，别把错误行吞了
            if "开始发送数据" in text:
                ok = True
                break
            if "失败" in text or "不合理" in text or "magic" in text:
                sys.exit(1)
        if not ok:
            sys.exit("固件没确认，放弃")

        print("[3/3] 发数据 ...")
        t0 = time.time()
        chunk = 32 * 1024
        for off in range(0, len(data), chunk):
            ser.write(data[off:off + chunk])
        ser.flush()
        print(f"      发完 {time.time() - t0:.1f}s，等保存确认 ...")

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            print("   ", text)
            if "文件保存完成" in text:
                break
            if "失败" in text or "CRC 不匹配" in text:
                sys.exit(1)
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
