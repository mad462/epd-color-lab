#!/usr/bin/env python3
"""传图台的本地助手。

做三件事：
  1. 把 host/ 目录当静态服务器发出去（页面、色板）
  2. POST /provision —— 配网：串口敲 W，把 SSID / 密码写进设备 NVS
  3. GET  /device    —— 只读当前 IP（不配网时用）

图片不走这里。板子连上 WiFi 后网页是**直接 POST 到设备**
（http://<设备IP>/raw，338KB 几秒），串口那 115200 传一张要 30 秒。

★ 串口是独占资源：每次操作完**都会关掉**，不会一直占着。
  但任何一次打开都会让板子复位一下（CH343 的 DTR/RTS 会跳），
  重启后 2~3 秒重新连上 WiFi，代价可以接受。

用法：
    python server.py               # http://127.0.0.1:5174/device.html
    一键启动就用根目录的 启动助手.bat
"""

import json
import os
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = 5174
ROOT = os.path.dirname(os.path.abspath(__file__))
SERIAL_PORT = os.environ.get("EPD_PORT", "COM6")
BAUD = 115200

try:
    import serial
except ImportError:
    print("需要 pyserial：pip install pyserial")
    sys.exit(1)

_lock = threading.Lock()          # 串口同一时刻只能一个请求用
IP_RE = re.compile(r"IP\s*=\s*(\d+\.\d+\.\d+\.\d+)")


def _read_until(ser, needles, timeout_s, log):
    """读到 needles 里任意一个就返回命中的那个，超时返回 None。同时收集输出。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        line = ser.readline()
        if not line:
            continue
        text = line.decode("utf-8", "replace").rstrip()
        if text:
            log.append(text)
        for n in needles:
            if n in text:
                return n
    return None


def _boot_wait(ser, log, timeout_s=15.0):
    """开串口会复位板子，等它启动到能收命令。

    只认校准提示那一行 —— 之前把 WiFi 横幅（====）也当完成标志，
    结果 W 发早了，设备还在启动、命令被丢掉。
    """
    hit = _read_until(ser, ["输入N进入下一个", "=== 校准模式"], timeout_s, log)
    time.sleep(0.4)              # 再稳一下，别抢在打印中间
    return hit


def _send_cmd(ser, cmd, expect, log, tries=3, wait_s=4.0):
    """发一个单字符命令，等预期回应。没等到就重发。

    校准模式是逐字符读的，偶尔会吃掉一次输入，重试比死等靠谱。
    """
    for i in range(tries):
        ser.reset_input_buffer()
        ser.write(cmd)
        ser.flush()
        if _read_until(ser, expect, wait_s, log):
            return True
        log.append(f"（第 {i + 1} 次发 {cmd.decode()} 没回应，重试）")
    return False


def provision(ssid, password):
    """配网。ssid 为空时只读 IP。

    返回 (ip, log)。串口无论如何都会关掉。
    """
    log = []
    ser = None
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.3)
        ser.setDTR(False)
        ser.setRTS(False)
        _boot_wait(ser, log)
        ser.reset_input_buffer()

        if not ssid:
            # 只问 IP：敲 e
            if not _send_cmd(ser, b"e", ["设备 IP", "还没配过"], log):
                log.append("设备没回应 e 命令")
                return None, log
            for line in reversed(log):
                m = IP_RE.search(line) or re.search(r"设备 IP\s*:\s*(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1), log
            return None, log

        # 配网：敲 W -> SSID -> 密码
        if not _send_cmd(ser, b"W", ["SSID）:"], log):
            log.append("设备没进配网模式")
            return None, log

        ser.write(ssid.encode("utf-8") + b"\r")
        ser.flush()
        if not _read_until(ser, ["密码"], 10, log):
            log.append("没等到密码提示")
            return None, log

        ser.write((password or "").encode("utf-8") + b"\r")
        ser.flush()

        hit = _read_until(ser, ["已连上", "没拿到 IP", "失败"], 25, log)
        if hit == "已连上":
            for line in reversed(log):
                m = IP_RE.search(line)
                if m:
                    return m.group(1), log
        return None, log
    except serial.SerialException as e:
        log.append(f"串口打不开：{e}（是不是被别的程序占着？）")
        return None, log
    finally:
        # ★ 一定要关 —— 串口是独占的，留着别的程序就用不了
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/device":
            if not _lock.acquire(blocking=False):
                return self._json({"ok": False, "log": ["串口正忙"]}, 409)
            try:
                ip, log = provision("", "")
            finally:
                _lock.release()
            print(f"[device] ip={ip}")
            return self._json({"ok": bool(ip), "ip": ip, "log": log[-8:]})
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/provision":
            return self._json({"ok": False, "error": "没有这个接口"}, 404)

        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception as e:
            return self._json({"ok": False, "error": f"请求格式不对：{e}"}, 400)

        ssid = (body.get("ssid") or "").strip()
        pwd = body.get("password") or ""

        if not _lock.acquire(blocking=False):
            return self._json({"ok": False, "error": "串口正忙，等上一个操作做完"}, 409)
        try:
            ip, log = provision(ssid, pwd)
        finally:
            _lock.release()

        print(f"[provision] ssid={ssid!r} -> ip={ip}")
        if ip:
            return self._json({"ok": True, "ip": ip, "log": log[-8:]})
        return self._json({"ok": False, "error": "没拿到 IP", "log": log[-8:]})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 56)
    print(f"  传图台助手已启动： http://127.0.0.1:{PORT}/device.html")
    print(f"  串口（配网用，用完即释放）： {SERIAL_PORT} @ {BAUD}")
    print("=" * 56)
    print()
    print("浏览器打开上面那个地址，右上角点「配网」填 WiFi。")
    print("关掉这个窗口就是停止助手。")
    print()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
