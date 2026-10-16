# 3.7″ 墨水屏 48 色

在 **720×480 六色电子纸**（JD7601 / GDEH037E01 级）上跑出 **48 色**，并且把这套颜色
真正调好用的完整工程 —— 含固件、上位机传图台、以及**整个测色与调色的方法论**。

硬件本身只有 6 种颜色。48 色是"用时间换色阶"抖动出来的，所以每个索引到底显示成
什么颜色**只能实测**，不能算。这个仓库里所有颜色数据都是拿色度计量的。

---

## 致谢

**本项目建立在 [FrameFilm](https://github.com/kirmisaki/FrameFilm) 的工作之上。**

固件里的 9 张面板波形表（`main/waveforms.c`，各 799 字节）直接取自 FrameFilm 的
`hal_epd_370.c`。**没有这部分，48 色根本跑不起来** —— 波形是这块屏的"配方"，
不是能算出来的东西，只能靠逆向和实测得到。

FrameFilm 以 **GPL-3.0** 发布，本项目因此采用**同一许可**，见 [LICENSE](LICENSE)。

另外还参考了：

| 项目 | 用到了什么 |
|---|---|
| epdoptimize | 动态范围压缩的强度混合、色度保护、二分拟合、蛇形扫描 |

---

## 硬件

| | |
|---|---|
| 主控 | ESP32-S3（N16R8，8MB PSRAM） |
| 屏幕 | 3.7″ 六色电子纸，720×480，SPI（JD7601 / GDEH037E01 级） |
| 存储 | TF 卡（SDMMC 4-bit） |
| 开发 | ESP-IDF 5.4.1 / PlatformIO |

---

## 快速开始

### 1. 烧固件

```bash
pio run -t upload
```

固件开机直接进**校准模式**（逐个刷满屏，配色度计用）。烧完先别急着传图。

### 2. 配网

**固件里不含 WiFi 密码** —— 密码只存在设备自己的 NVS 里。首次使用必须先配网：

```bash
python host/wifi_setup.py
```

它会通过串口问你 SSID 和密码（密码用 `getpass` 读，不回显、不进命令历史），
写完当场生效并返回设备 IP。

也可以直接在串口工具（115200）里敲大写 `W` 手动配 —— 流程、快捷键、清除凭据
等细节见 [docs/配网.md](docs/配网.md)。

### 3. 传图

```bash
python host/server.py       # 或双击根目录的 启动助手.bat
```

浏览器打开 **http://127.0.0.1:5174/device.html**，然后：

```
把图片拖进页面  →  右边调参数看效果  →  右上角「下发」
```

图片通过 **WiFi 直连**推到设备（`POST http://<设备IP>/raw`），338 KB 只要几秒；
面板刷新约 15 秒，进度条是设备上报的真实相位。

---

## 目录

```
main/                  ESP-IDF 固件
  main.c               CLI、校准模式、串口配网
  epd370.c/.h          面板驱动：SPI、波形下发、刷新时序
  waveforms.c/.h       9 张波形表，各 799 字节
  net.c/.h             WiFi STA + HTTP 服务（/status、/raw）
  wifi_store.c/.h      WiFi 凭据读写 NVS
  palette_table.c      色板表（= 实测值）
  bmp.c/.h             BMP 解析、旋转
  sdcard.c/.h          SDMMC 4-bit
  image.c/.h           收图

host/                  上位机工具（Python + 网页）
  device.html          传图台页面（量化预览 + 参数面板 + 配网）
  server.py            本地助手：静态服务、配网接口
  启动助手.bat          一键启动（在仓库根目录）
  wifi_setup.py        串口配网助手
  make_act.py          生成 .act 色板
  make_palette_table.py 生成固件色板表
  check_measurement_bias.py  测量偏差分析与修正
  preview_variants.py  管线分段渲染对比
  sweep_params/blend.py 参数扫描
  compare_palettes.py  色板对比
  check_page.py        页面 id 交叉核对（改完页面必跑）
  diag_red.py          复现网页管线，查"某个颜色不对"

mono-ui/               单色快刷（MonoFast）子项目 —— 黑白差分，180 ms/帧
                       独立 PlatformIO 工程：pio run -d mono-ui

docs/                  方法论、测量数据、迭代记录
legacy/                已停用的串口接图 / TF 卡存图代码（留档，不参与构建）
```

---

## 48 色是怎么来的

**8bpp 索引 = 高 3 位 × 8 + 低 3 位**，高 3 位走平面 0，低 3 位走平面 1，用
ColorQual 三相波形（约 15 秒）刷出来。波形表从
[FrameFilm](https://github.com/kirmisaki/FrameFilm) 的同规格面板移植。

固件里额外提供 **ColorFast 两相**（约 8 秒，颜色少一些），适合快速预览。

> 波形的关键解锁点是 LUT 写入协议：`0x00` + `0x0F 0xA9`，然后 `0x20` + **799 字节**。
> 早期用 `0x89` 且只发 56 字节，面板直接挂死。

---

## 颜色数据

| 文件 | 说明 |
|---|---|
| `docs/palette_qual_measured.csv` | **实测原值，唯一事实来源** |
| `main/palette_table.c` | 固件色板表（= 实测值） |
| `host/act/48-color-qual.act` | 192 字节 .act 色板 |
| `docs/palette_qual_去污染.csv` | 按物理边界校正过的版本（见方法论） |

**这三处必须逐字节一致**，否则固件认不出 BMP 用的是哪张色板。改完跑：

```bash
python host/make_act.py          --act-dir host/act --qual-csv docs/palette_qual_measured.csv
python host/make_palette_table.py --fw-dir main     --qual-csv docs/palette_qual_measured.csv
```

---

## 已知的坑

都是真踩过的，细节和数据在 [docs/颜色方法论.md](docs/颜色方法论.md)：

| 坑 | 一句话 |
|---|---|
| **测量仪器几何** | D/8 SCI 包含镜面反射 → 暗部被抬高、颜色被稀释 |
| **明度权重别用 2** | FrameFilm 的默认值，在实测色板上会把肤色匹配成浅灰绿 |
| **误差扩散要设上限** | 无上限累积会把饱和区推成一块死红、细节全丢、边界出斑块 |
| **饱和判定别用 HSV** | `(max-min)/max` 对暗色不可靠 —— 近黑能算出 0.375，于是暗部被误判、大面积断层 |
| **全局曲线压高光** | 拐点会掉进中间调，暗部被一起压暗；要用局部算法 |
| **L\* 归一化改色板** | 会把暗部色度抹平 → 量化器分不清蓝暗红暗 |
| **stdin CR→LF** | ESP-IDF 默认把 stdin 的 `0x0D` 换成 `0x0A`，二进制会被改坏 |
| **打开串口会复位** | CH343 的 DTR/RTS 会跳，操作完立刻发下一条会打断正在进行的刷新 |

---

## 许可

**GPL-3.0**，见 [LICENSE](LICENSE)。

原因见前面的[致谢](#致谢)：波形表取自 GPL-3.0 的 FrameFilm，本项目只能同许可。
