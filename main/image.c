#include "image.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_crc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "image";

/* 从 stdin 读满 len 字节。
 * ⚠️ 控制台 VFS 在"暂时没数据"时返回 0（不是阻塞），所以 0 不能当 EOF，
 * 只能小睡一下再试。 */
static esp_err_t read_exact(uint8_t *dst, size_t len)
{
    size_t got = 0;
    int idle_ms = 0;

    while (got < len) {
        const size_t n = fread(dst + got, 1, len - got, stdin);
        if (n == 0) {
            if (++idle_ms > 20000) {   /* 连着 20 s 没数据 */
                ESP_LOGE(TAG, "等了 20 s 没数据，只收到 %u/%u 字节",
                         (unsigned)got, (unsigned)len);
                return ESP_ERR_TIMEOUT;
            }
            vTaskDelay(1);
            continue;
        }
        idle_ms = 0;
        got += n;
    }
    return ESP_OK;
}

/* 把串口上已经积压的垃圾字节丢掉。
 *
 * 为什么需要：host 侧习惯"反复敲命令直到看见应答"，而开机时固件要忙
 * 十几秒（挂卡 + 显示第一张图），这期间敲进去的命令字符全积压在 UART 缓冲里。
 * 等固件进到接收流程，那些残留字节会被当成数据 —— 实测就是这样：
 * magic 读成了 77 77 77（"www"）。
 *
 * 清完再报"可以发了"，host 收到才开始发，就不会混。 */
void image_drain_input(uint32_t ms)
{
    uint8_t trash[128];
    const int64_t t0 = esp_timer_get_time();

    while ((esp_timer_get_time() - t0) < (int64_t)ms * 1000) {
        while (fread(trash, 1, sizeof(trash), stdin) > 0) {
            /* 丢掉 */
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}


#define RECV_FILE_MAX   8 * 1024 * 1024   /* 单文件上限 8 MB */

