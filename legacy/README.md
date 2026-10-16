# legacy —— 已经用不上的代码，留档

这个文件夹里的东西**不参与构建、不被引用**，纯粹留档。

## 为什么搬走

传图原本有**三条路**，现在只留一条：

| 路径 | 传输 345600 字节 | 状态 |
|---|---|---|
| **WiFi 直连**（`POST /raw`） | **约 3 秒** | ✅ **保留**（唯一路径） |
| 串口传索引（固件 `u`/`f`） | 约 30 秒 | ❌ 搬到这里 |
| 串口传文件存 TF 卡（固件 `w`） | 约 30 秒 | ❌ 搬到这里 |
| TF 卡浏览（固件 `l`/`j`/`n`/`p`/`d`） | — | ❌ 搬到这里 |

既然 WiFi 已经稳定（实测 0.12~0.26 秒响应），串口那两条路就是纯负担：

- 固件里多 260 KB 代码（Flash 1.10 MB → 843 KB，RAM 16.5% → 11.3%）
- 网页上多一堆按钮和分支
- 串口带宽 115200，一张图 30 秒，而 WiFi 只要 3 秒

**串口只剩一个用途**：问设备要 WiFi IP（因为网页没法自动发现设备）。
固件里是 `e` 命令，网页上是「从串口读取」。

## 目录

```
legacy/
├── README.md                          本文件
├── firmware/
│   └── serial_receive_and_tf.c.txt    从 main.c / image.c 摘出来的函数
└── host/
    ├── send_file.py                   串口传文件到 TF 卡（对应固件 'w'）
    ├── send_index.py                  串口推索引图（对应固件 'u'）
    ├── make_bmp.py                    造 8bpp 索引 BMP（给 TF 卡用的测试图）
    └── _cal_block.c.txt               早期调试留下的片段
```

## 怎么拿回来用

### 固件

`firmware/serial_receive_and_tf.c.txt` 里每个函数都标了它原来在哪个文件：

```c
/* ======== main.c :: recv_and_display ======== */
/* ======== image.c :: image_recv_file ======== */
```

1. 把函数贴回对应的 `.c`
2. `main.c` 的 `cli_loop()` 里补回对应的 `case` 分支
   （`l` `j` `n` `p` `d` `r` `w` `u` `f`）
3. `cal_input()` 里把 `'u' 'w' 'l' 'd' 'j'` 加回透传分支
4. `image.h` 里补回 `image_recv_serial` / `image_recv_file` 的声明
5. `pio run` 验证

### 网页

`host/device.html` 里补回串口相关的 UI 和 `/send` 路径。
`host/server.py` 的 git 历史里有完整的旧版本（第 11 个提交之前）。

## 被删掉的固件接口

`main/net.c` 的 HTTP 接口也精简了：

| 接口 | 原来干什么 | 现在 |
|---|---|---|
| `GET /status` | 设备信息 | ✅ 保留（**新增了 phase/progress**） |
| `POST /raw` | 收索引直接显示 | ✅ 保留（改成异步，见下） |
| `POST /upload` | 收 BMP 存 TF 卡 | ❌ 删除 |
| `GET /list` | 列 TF 卡上的图 | ❌ 删除 |
| `GET /show?i=N` | 显示 TF 卡上第 N 张 | ❌ 删除 |

## 顺带改的架构（重要）

`POST /raw` **从同步改成了异步**：

以前它在 httpd 任务里**同步**刷屏 —— 那 15 秒里 httpd 完全卡住，
连 `/status` 都问不到，所以进度条只能按时间瞎估。

现在：

```
/raw  →  收数据 →  丢进队列 → 立刻返回 {"ok":true,"queued":true}
                              ↓
                        独立 display 任务刷屏，更新 phase/progress
                              ↓
       网页每 250ms 轮询 /status → 拿到真实进度
```

`phase` 取值：`recv` / `refresh1`（清场）/ `refresh2`（第 1 相）/
`refresh3`（第 2、3 相）/ `idle`，`progress` 是 0~100。
