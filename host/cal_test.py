#!/usr/bin/env python3
"""校准模式的自动化验证。

逐条检查：
  1. 开机直接进校准模式，不刷屏、不挂卡（没有 full_clear/相位日志）
  2. 进模式后不自动刷，只等输入
  3. n -> 开始刷编号1，原始索引3 / 刷新完成 / 输入N进入下一个
  4. 再按 n -> 编号2
  5. f -> 只切色板，不刷
  6. 数字+回车 -> 跳到该编号
  7. 刷新期间连按 nnnn -> 只算一次，不排队
  8. 校准期间刷新过程日志被压掉（串口只有那三行 + 标题）

用法：python cal_test.py
"""

import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("需要 pyserial")

PORT = "COM6"
BAUD = 115200

results = []


class Board:
    def __init__(self):
        self.buf = ""
        self.lines = []
        self.ser = serial.Serial(PORT, BAUD, timeout=0.2)
        self.ser.setDTR(False)
        self.ser.setRTS(False)
        time.sleep(0.5)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def pump(self):
        data = self.ser.read(4096)
        if data:
            self.buf += data.decode("utf-8", "replace")
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                line = line.rstrip("\r")
                if line.strip():
                    self.lines.append(line)
                    print("   |", line)

    def wait(self, pattern, timeout=60, label=""):
        rex = re.compile(pattern)
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.pump()
            for line in self.lines:
                if rex.search(line):
                    return line
            time.sleep(0.05)
        raise TimeoutError(f"等不到 {pattern!r} {label}（{timeout}s）")

    def send(self, s):
        self.ser.write(s.encode())
        self.ser.flush()


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  —— {detail}" if detail else ""))


def main():
    b = Board()
    try:
        print("\n[1] 开机：应直接进校准模式，不刷屏、不挂卡")
        b.wait(r"校准模式", 25, "开机进校准")
        b.wait(r"输入N进入下一个", 10, "等待输入")
        noise = [l for l in b.lines if re.search(r"full_clear|qual_phase|刷新完成：进入忙|里找到|TF 卡", l)]
        check("开机不刷屏、不挂卡", not noise, f"杂音 {noise[:3]}" if noise else "无 full_clear/相位/挂卡日志")
        check("进模式后不自动刷", not any("开始刷编号" in l for l in b.lines))

        print("\n[2] 按 n：应刷编号1（原始索引3）")
        b.lines.clear()
        b.send("n")
        l1 = b.wait(r"开始刷编号1，原始索引3", 30)
        b.wait(r"刷新完成", 40)
        b.wait(r"输入N进入下一个", 10)
        check("n -> 开始刷编号1，原始索引3", True, l1.strip())
        check("三行输出齐全", True, " / ".join(x.strip() for x in b.lines
                                              if re.search(r"开始刷编号|刷新完成|输入N", x)))

        print("\n[3] 再按 n：应刷编号2")
        b.lines.clear()
        b.send("n")
        b.wait(r"开始刷编号2", 30)
        b.wait(r"刷新完成", 40)
        b.wait(r"输入N进入下一个", 10)
        check("n -> 编号2", True)

        print("\n[4] 按 f：只切色板，不刷")
        b.lines.clear()
        b.send("f")
        b.wait(r"已切到 ColorFast", 10)
        time.sleep(1.5)
        b.pump()
        check("f 只切色板不刷", not any("开始刷编号" in l for l in b.lines),
              next((l.strip() for l in b.lines if "已切到" in l), ""))

        print("\n[5] 数字+回车：跳到编号 3")
        b.lines.clear()
        b.send("3\r")
        l = b.wait(r"开始刷编号3", 30)
        b.wait(r"刷新完成", 40)
        b.wait(r"输入N进入下一个", 10)
        check("3+回车 -> 编号3", True, l.strip())

        print("\n[6] 连按不排队：刷动作中连发 nnnn，结束后只应再刷一次")
        b.lines.clear()
        b.send("n")                 # 开始刷编号4
        b.wait(r"开始刷编号4", 30)
        b.send("nnnn")              # 刷新期间连按 4 下
        b.wait(r"刷新完成", 40)
        b.wait(r"输入N进入下一个", 10)
        # 之后 6 秒内，除了第一次（编号5）不该再有第二次刷
        b.lines.clear()
        time.sleep(8)
        b.pump()
        flashes = [l for l in b.lines if "开始刷编号" in l]
        check("刷新期间的连按被丢弃", len(flashes) <= 1,
              f"之后只出现 {len(flashes)} 次刷：{[f.strip() for f in flashes]}")

        print("\n[7] 校准期间过程日志被压掉")
        b.lines.clear()
        b.send("n")
        b.wait(r"开始刷编号", 30)
        b.wait(r"刷新完成", 40)
        b.wait(r"输入N进入下一个", 10)
        junk = [l for l in b.lines if re.search(r"full_clear|qual_phase|刷新完成：进入忙|phase", l)]
        check("串口没有相位/清理日志", not junk, f"残留 {junk[:3]}" if junk else "干净")

        print("\n[8] 按 x 退出校准")
        b.lines.clear()
        b.send("x")
        b.wait(r"已退出校准模式", 10)
        check("x 退出", True)

    except Exception as e:
        check("流程异常", False, str(e))
    finally:
        b.close()

    print("\n" + "=" * 60)
    bad = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  —— {detail}" if detail and not ok else ""))
    print("=" * 60)
    print(f"{len(results) - len(bad)}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
