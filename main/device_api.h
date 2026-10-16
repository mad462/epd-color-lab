#ifndef DEVICE_API_H
#define DEVICE_API_H

/* 给 http 服务（net.c）用的设备操作接口。
 *
 * 为什么单独抽一层：面板 SPI 和 TF 卡是**共享**资源，命令行和网页服务
 * 不能同时动它们。所有对外操作都要先拿锁 —— 见 device_lock()。 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define DEVICE_MAX_FILES 128
#define DEVICE_NAME_MAX  128

typedef struct {
    char name[DEVICE_NAME_MAX];
    long size;
    int  w, h, bpp, colors;
    bool is_bmp;          /* false = 不是可用的 BMP（文件还在，但读不出头） */
} device_file_t;

/* 设备操作串行化。拿不到锁就别动硬件。 */
bool device_lock(uint32_t timeout_ms);
void device_unlock(void);

/* 扫 TF 卡的 IMAGE_DIR，填进 out，返回文件数（<= max）。
 * 不打印任何东西，纯数据。 */
int device_scan(device_file_t *out, int max);

/* 当前列表里有几张（0 = 还没扫过）。 */
int device_count(void);

/* 显示第 idx 张（0 基）。越界返回 ESP_ERR_INVALID_ARG。 */
esp_err_t device_show(int idx);

/* 直接显示一份 8bpp 索引数据（不落盘）。len 必须是 720*480 = 345600。
 * 对应串口的 'u' 那条路。 */
esp_err_t device_show_raw(const uint8_t *idx, size_t len);

/* 把一整份数据存成 IMAGE_DIR/<name>，然后显示它。
 *
 * 为什么整份收完再落盘：写 SD 卡会阻塞几十毫秒，边收边写会丢数据。
 * WiFi 虽然是突发式，但同理 —— 先缓冲，再写。 */
esp_err_t device_save_and_show(const char *name, const uint8_t *data, size_t len);

/* 最近一次操作的结果（供 /status 显示） */
const char *device_last_msg(void);
void device_set_last_msg(const char *fmt, ...);

/* ---------------- 进度上报（供网页轮询 /status 用）----------------
 *
 * 为什么需要：刷屏要 15 秒，而且是在 httpd 任务里**同步**跑的 ——
 * 那样网页在刷屏期间连 /status 都问不到。所以 /raw 收完数据立刻返回，
 * 实际的显示交给一个独立任务，网页轮询这里的进度。
 *
 * phase 取值：recv / refresh1 / refresh2 / refresh3 / idle
 * progress：0~100，-1 表示空闲
 */
void        device_set_phase(const char *phase, int progress);
const char *device_phase(void);
int         device_progress(void);

#endif /* DEVICE_API_H */
