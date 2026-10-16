#!/usr/bin/env python3
"""device.html 的本地助手服务器。

除了当静态服务器，还提供两个接口，让浏览器**绕开 Web Serial**：

  POST /send?name=xxx.bmp    body = BMP 原始字节
      用已验证的串口协议写到板子 TF 卡，返回过程日志

  POST /cmd?k=l              body 忽略
      往串口发一个命令字符（l/n/p/d/r/i/?），返回这一小段的输出

为什么要这个：这台机器上 Chrome 的 Web Serial **枚举不到 CH343 的 COM6**
（串口本身没问题，串口助手能正常打开），换成本地助手就绕开了浏览器那层。

用法：
    python server.py            # 127.0.0.1:5174
    set EPD_PORT=COM7 & python server.py
"""

import json
import os
import re
import sys
import threading
import time
import zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 5174
ROOT = os.path.dirname(os.path.abspath(__file__))
SERIAL_PORT = os.environ.get("EPD_PORT", "COM6")
BAUD = 115200
CHUNK = 8 * 1024
CHUNK_DELAY = 0.002      # 每块之间稍等一下，给板子 UART 缓冲留余量

try:
    import serial
except ImportError:
    print("需要 pyserial：pip install pyserial")
    sys.exit(1)

_lock = threading.Lock()          # 串口同一时刻只能一个请求用

# 传输进度：网页轮询 /progress 拿它显示进度条
_progress = {"active": False, "phase": "", "sent": 0, "total": 0, "detail": ""}
_progress_lock = threading.Lock()


def _set_prog(**kw):
    with _progress_lock:
        _progress.update(kw)


def _add_sent(n):
    with _progress_lock:
        _progress["sent"] += n


def get_progress():
    with _progress_lock:
        return dict(_progress)


def _begin_prog(phase, total=0):
    _set_prog(active=True, phase=phase, sent=0, total=total, detail="")


def _end_prog(detail=""):
    _set_prog(active=False, phase="done", detail=detail)


def _pump(ser, log, seconds, stop_on=None):
    """读串口若干秒，收进 log；命中 stop_on 里的任一正则就提前返回"""
    t0 = time.time()
    patterns = [re.compile(p) for p in (stop_on or [])]
    while time.time() - t0 < seconds:
        line = ser.readline()
        if not line:
            continue
        text = line.decode("utf-8", "replace").rstrip()
        log.append(text)
        for p in patterns:
            if p.search(text):
                return text
    return None


# 板子已经就绪的标志：进校准模式等输入，或已经在普通 CLI
_READY_PATTERNS = ["输入N进入下一个", "已退出校准模式", "敲 '1' 开始", "按 '1' 开始"]


def _open_and_wait_boot(log, settle_s=1.5):
    """打开串口并**等板子启动完成**再返回。

    为什么要等：打开 CH343 会让 DTR/RTS 跳一下，ESP32 随之复位。
    复位后马上发命令的话，那个字节会落在 ROM 启动阶段被丢掉 ——
    实测就是这样：发出去的 'w' 没反应，日志里只有完整开机过程。
    """
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.3)
    ser.setDTR(False)
    ser.setRTS(False)

    # 等一个"就绪"标志，最多 12 秒；没等到也别死等，静默一会儿照样往下走
    hit = _pump(ser, log, 12, _READY_PATTERNS)
    if hit is None:
        time.sleep(settle_s)

    # 把开机过程中残留的字节丢掉
    ser.reset_input_buffer()
    return ser


def send_file_to_board(data: bytes, name: str):
    """把整个文件写到板子。返回 (ok, log)"""
    log = []
    crc = zlib.crc32(data) & 0xFFFFFFFF

    try:
        ser = _open_and_wait_boot(log)     # 打开 + 等开机完成，只开一次
    except Exception as e:
        return False, [f"打不开 {SERIAL_PORT}: {e}"]

    try:
        # 1) 敲 w，等固件清完缓冲喊"开始发送头部"
        ser.write(b"w")
        ser.flush()
        log.append("已发送 'w'，等固件准备好 ...")
        if not _pump(ser, log, 120, ["开始发送头部", r"失败"]):
            return False, log + ["固件没准备好（没等到「开始发送头部」）"]

        # 2) 文件头：EPF + 名字长度 + 名字 + 大小 + CRC
        nb = name.encode("ascii", "replace")
        head = b"EPF" + bytes([len(nb)]) + nb + \
            len(data).to_bytes(4, "little") + crc.to_bytes(4, "little")
        ser.write(head)
        ser.flush()
        log.append(f"文件头：{name}  {len(data)} 字节  CRC {crc:08X}")
        if not _pump(ser, log, 20, ["开始发送数据", r"失败", r"magic"]):
            return False, log + ["固件没收文件头"]

        # 3) 数据
        _begin_prog("传输中", len(data))
        t0 = time.time()
        for off in range(0, len(data), CHUNK):
            n = min(CHUNK, len(data) - off)
            ser.write(data[off:off + n])
            _add_sent(n)
            time.sleep(CHUNK_DELAY)
        ser.flush()
        log.append(f"发完 {len(data)} 字节，用了 {time.time() - t0:.1f}s")

        # 4) 等落盘，然后**必须等它刷完**再返回。
        #
        # 为什么：打开串口会让板子复位。如果这里只等到"文件保存完成"就返回，
        # 网页紧接着发的下一个请求（比如刷新列表）就会复位板子，
        # 把正在进行的刷新打断 —— 屏上就是"刷到一半挂了"。
        # 「只显示」那条路没这问题，因为它本来就等到"显示完成"。
        _set_prog(phase="写入 TF 卡", sent=len(data))
        hit = _pump(ser, log, 120, ["文件保存完成", r"失败", "CRC 不匹配"])
        if hit is None or "失败" in hit or "不匹配" in hit:
            _end_prog("失败")
            return False, log

        _set_prog(phase="刷新显示（约 18 秒）", sent=len(data))
        _pump(ser, log, 150, ["显示完成", r"失败"])
        _end_prog("完成")
        return True, log
    finally:
        try:
            ser.close()
        except Exception:
            pass


def send_raw_to_board(idx: bytes):
    """只显示不存卡：u + 索引 + CRC（对应固件 'u'）"""
    log = []
    crc = zlib.crc32(idx) & 0xFFFFFFFF
    try:
        ser = _open_and_wait_boot(log)     # 只开一次
    except Exception as e:
        return False, [f"打不开 {SERIAL_PORT}: {e}"]
    try:
        ser.write(b"u")
        ser.flush()
        log.append("已发送 'u'，等固件清完缓冲 ...")
        if not _pump(ser, log, 120, ["开始发送数据", r"失败"]):
            return False, log + ["固件没准备好"]

        t0 = time.time()
        _begin_prog("传输中", len(idx))
        for off in range(0, len(idx), CHUNK):
            n = min(CHUNK, len(idx) - off)
            ser.write(idx[off:off + n])
            _add_sent(n)
            time.sleep(CHUNK_DELAY)
        ser.write(crc.to_bytes(4, "little"))
        ser.flush()
        log.append(f"发完 {len(idx)} 索引 + CRC，用了 {time.time() - t0:.1f}s")

        _set_prog(phase="刷新显示", sent=len(idx))
        hit = _pump(ser, log, 180, ["显示完成", r"失败", "CRC 不匹配"])
        ok = hit is not None and "失败" not in hit and "不匹配" not in hit
        _end_prog("完成" if ok else "失败")
        return ok, log
    finally:
        try:
            ser.close()
        except Exception:
            pass


def send_command_to_board(key: str):
    """发一个命令字符，收集 2 秒输出"""
    log = []
    try:
        ser = _open_and_wait_boot(log)     # 只开一次
    except Exception as e:
        return False, [f"打不开 {SERIAL_PORT}: {e}"]
    try:
        ser.write(key.encode("ascii", "replace")[:1])
        ser.flush()
        _pump(ser, log, 2.5)
        return True, log
    finally:
        try:
            ser.close()
        except Exception:
            pass


LIST_RE = re.compile(r"^\s*\[\s*(\d+)\]\s+(.+?)\s+(-?\d+)\s*B\s*(.*)$")


def list_device_files():
    """让固件扫一遍卡，把列表解析成结构化数据。

    固件打的行长这样：
        [ 0] name.bmp  345910 B  480x720  8bpp  64色
        [ 1] bad.bmp   1234 B    (不是可用的 BMP)
    """
    log = []
    _begin_prog("读取设备列表")
    try:
        ser = _open_and_wait_boot(log)
    except Exception as e:
        _end_prog("失败")
        return False, [], [f"打不开 {SERIAL_PORT}: {e}"]
    try:
        ser.write(b"l")
        ser.flush()
        _pump(ser, log, 25, ["按 'j' 输编号", "没挂上", "还没"])
    finally:
        try:
            ser.close()
        except Exception:
            pass

    files = []
    for line in log:
        m = LIST_RE.match(line)
        if m:
            files.append({
                "index": int(m.group(1)),
                "name": m.group(2).strip(),
                "size": int(m.group(3)),
                "info": m.group(4).strip(),
            })
    _end_prog("完成")
    return len(files) > 0, files, log


def show_device_file(i):
    """切到第 i 张（0-based）：'j' + 编号 + 回车"""
    log = []
    _begin_prog("切换图片")
    try:
        ser = _open_and_wait_boot(log)
    except Exception as e:
        _end_prog("失败")
        return False, [f"打不开 {SERIAL_PORT}: {e}"]
    try:
        ser.write(b"j")
        ser.flush()
        if not _pump(ser, log, 20, ["输编号"]):
            # 可能还没扫过卡，先扫一次再来
            ser.write(b"l")
            ser.flush()
            _pump(ser, log, 25, ["按 'j' 输编号", "没挂上"])
            ser.write(b"j")
            ser.flush()
            if not _pump(ser, log, 20, ["输编号"]):
                _end_prog("失败")
                return False, log + ["固件没进跳转模式"]

        ser.write(f"{i + 1}\r".encode())
        ser.flush()
        _set_prog(phase="刷新显示")
        hit = _pump(ser, log, 150, ["显示完成", r"失败", "超出范围", "还没有图片"])
        ok = hit is not None and "失败" not in hit and "超出范围" not in hit
        _end_prog("完成" if ok else "失败")
        return ok, log
    finally:
        try:
            ser.close()
        except Exception:
            pass


def read_device_ip(timeout_s=15.0):
    """从串口读几秒，抓设备启动时打印的 IP。

    ⚠️ 打开串口会复位板子（CH343 的 DTR/RTS），所以这一步会让设备重启一次。
    重启后 2~3 秒就能重新连上 WiFi，代价可以接受。
    """
    log = []
    pattern = re.compile(r"IP:\s*(\d+\.\d+\.\d+\.\d+)")
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
            m = pattern.search(text)
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
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path == "/progress":
            return self._json(get_progress())

        if u.path == "/list":
            if not _lock.acquire(blocking=False):
                return self._json({"ok": False, "log": ["串口忙，等上一次操作完"]}, 409)
            try:
                ok, files, log = list_device_files()
            finally:
                _lock.release()
            print(f"[list] {len(files)} 个文件 ok={ok}")
            return self._json({"ok": ok, "files": files, "log": log})

        if u.path == "/show":
            try:
                i = int((qs.get("i") or ["0"])[0])
            except ValueError:
                return self._json({"ok": False, "log": ["i 不是数字"]}, 400)
            if not _lock.acquire(blocking=False):
                return self._json({"ok": False, "log": ["串口忙"]}, 409)
            try:
                ok, log = show_device_file(i)
            finally:
                _lock.release()
            print(f"[show] i={i} ok={ok}")
            return self._json({"ok": ok, "log": log})

        if u.path == "/device":
            if not _lock.acquire(blocking=False):
                return self._json({"ok": False, "log": ["串口忙"]}, 409)
            try:
                ip, log = read_device_ip()
            finally:
                _lock.release()
            print(f"[device] ip={ip}")
            return self._json({"ok": bool(ip), "ip": ip, "log": log[-8:]})

        return super().do_GET()

    def do_POST(self):
        try:
            self._do_post_inner()
        except Exception as e:
            # 端口被占 / 板子掉线之类，别让整个请求 500 掉，回个能看懂的 JSON
            try:
                self._json({"ok": False, "log": [f"本地助手出错：{type(e).__name__}: {e}"]}, 500)
            except Exception:
                pass

    def _do_post_inner(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path == "/send":
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length)
            kind = (qs.get("type") or ["bmp"])[0]
            name = (qs.get("name") or ["pic.bmp"])[0]
            # 只允许 ASCII 且 FAT 合法的字符（固件 FATFS 按 GBK 解析，中文会 fopen 失败）。
            # 空格和 + 都留着 —— 参数标签里 B+10 这种要看得懂。
            name = re.sub(r"[^A-Za-z0-9._ +\-]", "_", name)
            if not name.lower().endswith(".bmp"):
                name += ".bmp"

            if not _lock.acquire(blocking=False):
                return self._json({"ok": False, "log": ["串口忙，等上一次发完"]}, 409)
            try:
                if kind == "raw":
                    print(f"[send-raw] {len(data)} 字节")
                    ok, log = send_raw_to_board(data)
                else:
                    # 串口偶发丢字节会让板子报 CRC 不匹配；整份重传一次就好
                    ok, log = False, []
                    for attempt in range(3):
                        if attempt:
                            log.append(f"—— 第 {attempt + 1} 次尝试 ——")
                        part_ok, part_log = send_file_to_board(data, name)
                        log.extend(part_log)
                        if part_ok:
                            ok = True
                            break
                        log.append("失败，整份重传 ...")
                        time.sleep(1.0)
            finally:
                _lock.release()
            print("[send]", "OK" if ok else "失败")
            return self._json({"ok": ok, "log": log})

        if u.path == "/cmd":
            key = (qs.get("k") or [""])[0][:1]
            if not key:
                return self._json({"ok": False, "log": ["缺少 k 参数"]}, 400)
            if not _lock.acquire(blocking=False):
                return self._json({"ok": False, "log": ["串口忙"]}, 409)
            try:
                ok, log = send_command_to_board(key)
            finally:
                _lock.release()
            return self._json({"ok": ok, "log": log})

        return self._json({"ok": False, "log": ["未知接口"]}, 404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"本地助手已启动： http://127.0.0.1:{PORT}/device.html")
    print(f"串口： {SERIAL_PORT} @ {BAUD}")
    print("接口： POST /send?name=..  POST /cmd?k=..")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
