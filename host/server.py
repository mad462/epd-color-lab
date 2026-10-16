#!/usr/bin/env python3
"""传图台的本地助手 —— 只做两件事：

  1. 把 host/ 目录当静态服务器发出去（页面、色板）
  2. GET /device —— 从串口读设备启动时打印的 IP

图片不再走串口了。板子连上 WiFi 之后，网页是**直接 POST 到设备**
（http://<设备IP>/raw），338 KB 只要几秒；串口 115200 下要 30 秒。

串口在这里唯一的用途是**问设备要 IP** —— 因为网页没法自动发现它。
（打开串口会让板子复位一次，重启后 2~3 秒重新连上 WiFi。）

用法：
    python server.py            # http://127.0.0.1:5174/device.html
    set EPD_PORT=COM7 & python server.py
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
IP_RE = re.compile(r"IP:\s*(\d+\.\d+\.\d+\.\d+)")


def read_device_ip(timeout_s=15.0):
    """从串口读几秒，抓设备打印的 IP。

    ⚠️ 打开串口会复位板子（CH343 的 DTR/RTS 会跳一下），所以这一步会让设备重启。
    重启后它 2~3 秒重新连上 WiFi，代价可以接受。
    """
    log = []
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.3)
    except Exception as e:
        return None, [f"打不开 {SERIAL_PORT}: {e}"]

    try:
        ser.setDTR(False)
        ser.setRTS(False)
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", "replace").rstrip()
            log.append(text)
            m = IP_RE.search(text)
            if m:
                return m.group(1), log
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return None, log


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT)

    def log_message(self, fmt, *args):
        pass    # 别把静态请求刷屏

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
                return self._json({"ok": False, "log": ["串口忙"]}, 409)
            try:
                ip, log = read_device_ip()
            finally:
                _lock.release()
            print(f"[device] ip={ip}")
            return self._json({"ok": bool(ip), "ip": ip, "log": log[-6:]})

        return super().do_GET()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"传图台已启动： http://127.0.0.1:{PORT}/device.html")
    print(f"串口（只用来读设备 IP）： {SERIAL_PORT} @ {BAUD}")
    print()
    print("图片走 WiFi 直连，不经过这里。流程：")
    print("  1. 页面点「从串口读取」拿到设备 IP")
    print("  2. 拖图进来，调参数，点「下发」")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
