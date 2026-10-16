#!/usr/bin/env python3
"""把 8bpp 索引图从串口推给板子（对应固件的 'u' 命令）。

用法：
    python send_index.py ..\\out\\card48_qual.bin8
    python send_index.py ..\\out\\photo_qual.bin8 --pre-delay 5

流程：开串口（DTR/RTS 拉低防复位）-> 反复敲 'u' 直到固件接受 ->
     发 345600 字节 + 4 字节 CRC32 -> 等三次刷新完成。

115200 下 345600 字节约 30 秒，之后还有 3 次刷新（约 15 秒）。
"""

import argparse
import os
import sys
import time
import zlib

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

INDEX_BYTES = 720 * 480


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("index_bin")
    ap.add_argument("--port", default="COM6")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--pre-delay", type=float, default=2.0)
    ap.add_argument("-t", "--timeout", type=float, default=90.0,
                    help="发完后最多等多久（三次刷新约 15 秒）")
    args = ap.parse_args()

    if not os.path.exists(args.index_bin):
        sys.exit(f"找不到 {args.index_bin}")
    data = open(args.index_bin, "rb").read()
    if len(data) != INDEX_BYTES:
        sys.exit(f"{args.index_bin} 是 {len(data)} 字节，应该是 {INDEX_BYTES}")

    crc = zlib.crc32(data) & 0xFFFFFFFF

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

        # 只敲一次 'u'（反复敲会积压，被当成图像数据 —— 同 send_file.py 的坑）
        print(f"[1/3] 发一次 'u'，等固件清完缓冲 ...")
        ser.write(b"u")
        ser.flush()

        armed = False
        deadline = time.time() + 120
        while time.time() < deadline:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            print("   ", text)
            if "开始发送数据" in text:
                armed = True
                break
        if not armed:
            sys.exit("固件一直没准备好")

        print(f"[2/3] 推 {len(data)} 字节 + CRC 0x{crc:08X} ...")
        t0 = time.time()
        ser.write(data)
        ser.write(crc.to_bytes(4, "little"))
        ser.flush()
        print(f"      发完，{time.time() - t0:.1f}s")

        print("[3/3] 等刷新 ...")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            print("   ", text)
            if "显示完成" in text or "失败" in text:
                break
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
