# 3.7″ 墨水屏 48 色

在 **720×480 六色电子纸（JD7601 / GDEH037E01 级）** 上跑出 **48 色**，并把这套颜色
真正调好用的完整工程。

- **硬件**：ESP32-S3（N16R8）+ 3.7″ 六色电子纸（SPI）+ TF 卡
- **固件**：ESP-IDF 5.4.1 / PlatformIO
- **颜色方案**：时间域抖动 + 自定义波形，8bpp 索引 `= 高3位*8 + 低3位`
- **波形来源**：[FrameFilm](https://github.com/kirmisaki/FrameFilm)（GPL-3.0）

---

## 从哪看起

👉 **[docs/颜色方法论.md](docs/颜色方法论.md)** —— 颜色怎么测、偏在哪、怎么评估、
最后用了什么方法，以及**走过的 8 条弯路**（每条都有症状、原因、和推翻它的数据）。

调色或重新测量之前，先读这份。

---

## 这个工程的两个特点

### 一、颜色是**测出来的**，不是算出来的

硬件只有 6 色，48 色是"用时间换色阶"抖出来的。每个索引实际显示成什么颜色，
**只能实测**。所以：

| 文件 | 作用 |
|---|---|
| `docs/palette_qual_measured.csv` | **实测原值，唯一事实来源** |
| `main/palette_table.c` | 固件色板表（= 实测值） |
| `host/act/48-color-qual.act` | 192 字节色板，必须与固件表逐字节一致 |

### 二、调色在**网页**上做，不用重新烧固件

`host/device.html` + `host/server.py` 组成一个本地传图台：

- 拖图进去 → 实时预览量化效果
- 亮度/对比度/饱和度/阴影/高光…（默认只露 6 个控件，其余收进「高级」）
- 一键经串口推到设备，存 TF 卡并显示
- 读出设备里已有的图片列表，点击切换

**所有串口操作走本地服务，不用浏览器直连串口** —— 某些机器上 Chrome 枚举不到
CH343，本地助手能绕开这层。

---

## 快速开始

```powershell
# 固件
pio run -d . -t upload          # 含校准模式，开机直接进

# 传图台
python host\server.py           # http://127.0.0.1:5174/device.html
```

---

## 目录

```
main/                 ESP-IDF 固件
  main.c              CLI + 校准模式
  epd370.c/.h         面板驱动（SPI、波形、刷新）
  waveforms.c/.h      9 张波形表，各 799 字节
  image.c/.h          串口收图 / 文件收图
  bmp.c/.h            BMP 解析、旋转、色板匹配
  sdcard.c/.h         SDMMC 4-bit
  palette_table.c     色板表（= 实测值）

host/                 上位机工具（Python + 网页）
  server.py           本地助手：静态服务 + 串口接口
  device.html         传图台页面
  palette_report.py   生成方法论文档里的数据表
  preview_variants.py 管线分段渲染对比
  sweep_params/blend  参数扫描
  compare_palettes.py 非循环的色板对比
  check_measurement_bias.py  测量偏差分析与修正
  check_page.py       页面 id 交叉核对（改完页面必跑）
  make_act.py         生成 .act
  make_palette_table.py  生成固件色板表

docs/                 文档与数据
```

---

## 改色板时三处必须同步

否则固件认不出 BMP 用的是哪张色板：

```powershell
python host\make_act.py --act-dir C:\FUCKWEB\quick-shake-pic\act --qual-csv docs\palette_qual_measured.csv
python host\make_act.py --act-dir host\act                          --qual-csv docs\palette_qual_measured.csv
python host\make_palette_table.py --fw-dir main                      --qual-csv docs\palette_qual_measured.csv
```

---

## 已知的坑（都写在方法论里了，这里列个索引）

| 坑 | 一句话 |
|---|---|
| **测量仪器几何** | LS173 是 D/8 SCI，包含镜面反射 → 暗部被抬高、颜色被稀释 |
| **stdin CR→LF** | ESP-IDF 默认把 stdin 的 `0x0D` 换成 `0x0A`，二进制会被改坏 |
| **打开串口会复位** | 操作完立刻发下一条指令会打断正在进行的刷新 |
| **全局曲线压高光** | 拐点会掉进中间调，暗部被一起压暗；要用局部算法 |
| **L\* 归一化改色板** | 会把暗部色度抹平 → 量化器分不清蓝暗红暗 |
| **明度权重 2** | FrameFilm 的默认值，在实测色板上会把肤色匹配成浅灰绿 |

详见 [docs/颜色方法论.md](docs/颜色方法论.md) 第六节。

---

## 许可

固件中的波形表取自 [FrameFilm](https://github.com/kirmisaki/FrameFilm)（GPL-3.0），
本项目相应部分同样适用 GPL-3.0。
