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

esp_err_t image_recv_serial(uint8_t *fb, size_t len)
{
    ESP_LOGI(TAG, "等串口送 %u 字节索引图 + 4 字节 CRC32", (unsigned)len);

    image_drain_input(250);
    printf("开始发送数据\n");
    fflush(stdout);

    esp_err_t err = read_exact(fb, len);
    if (err != ESP_OK) {
        return err;
    }

    uint8_t tail[4];
    err = read_exact(tail, sizeof(tail));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CRC 没收到");
        return err;
    }

    const uint32_t want = (uint32_t)tail[0] | ((uint32_t)tail[1] << 8) |
                          ((uint32_t)tail[2] << 16) | ((uint32_t)tail[3] << 24);
    const uint32_t have = esp_crc32_le(0, fb, (uint32_t)len);
    if (want != have) {
        ESP_LOGE(TAG, "CRC 不匹配：收到 0x%08lX，算得 0x%08lX —— 传输掉字节了",
                 (unsigned long)want, (unsigned long)have);
        return ESP_ERR_INVALID_CRC;
    }

    ESP_LOGI(TAG, "收到 %u 字节，CRC 0x%08lX 通过", (unsigned)len, (unsigned long)have);
    return ESP_OK;
}

#define RECV_FILE_MAX   8 * 1024 * 1024   /* 单文件上限 8 MB */

esp_err_t image_recv_file(const char *dir, char *out_path, size_t out_path_len)
{
    uint8_t hdr[3 + 1 + 255 + 4 + 4];
    uint8_t fixed[8];   /* name_len 单独读，方便后面定长读 */

    ESP_LOGI(TAG, "等文件头：\"EPF\" + 名字长度(1) + 名字 + 大小(4) + CRC32(4)");

    image_drain_input(250);
    printf("开始发送头部\n");
    fflush(stdout);

    esp_err_t err = read_exact(hdr, 4);   /* magic(3) + name_len(1) */
    if (err != ESP_OK) {
        return err;
    }
    if (hdr[0] != 'E' || hdr[1] != 'P' || hdr[2] != 'F') {
        ESP_LOGE(TAG, "magic 不对（%02X %02X %02X），应该是 \"EPF\"", hdr[0], hdr[1], hdr[2]);
        return ESP_ERR_INVALID_ARG;
    }

    const size_t name_len = hdr[3];
    if (name_len == 0 || name_len > 200) {
        ESP_LOGE(TAG, "文件名长度 %u 不合理", (unsigned)name_len);
        return ESP_ERR_INVALID_ARG;
    }

    char name[201];
    err = read_exact((uint8_t *)name, name_len);
    if (err != ESP_OK) {
        return err;
    }
    name[name_len] = '\0';

    /* 文件名里不允许出现路径分隔符，避免写到别处 */
    if (strchr(name, '/') != NULL || strchr(name, '\\') != NULL) {
        ESP_LOGE(TAG, "文件名里有路径分隔符，拒绝: %s", name);
        return ESP_ERR_INVALID_ARG;
    }

    err = read_exact(fixed, 8);
    if (err != ESP_OK) {
        return err;
    }
    const uint32_t size = (uint32_t)fixed[0] | ((uint32_t)fixed[1] << 8) |
                          ((uint32_t)fixed[2] << 16) | ((uint32_t)fixed[3] << 24);
    const uint32_t want_crc = (uint32_t)fixed[4] | ((uint32_t)fixed[5] << 8) |
                              ((uint32_t)fixed[6] << 16) | ((uint32_t)fixed[7] << 24);

    if (size == 0 || size > RECV_FILE_MAX) {
        ESP_LOGE(TAG, "文件大小 %u 不合理（上限 %d）", (unsigned)size, RECV_FILE_MAX);
        return ESP_ERR_INVALID_SIZE;
    }

    char path[256];
    snprintf(path, sizeof(path), "%s/%s", dir, name);

    ESP_LOGI(TAG, "准备收 %s（%u 字节，CRC 0x%08lX）", name,
             (unsigned)size, (unsigned long)want_crc);

    /* 先整份收进 PSRAM，最后再落盘。
     *
     * 为什么不能边收边写：写 SD 卡会阻塞几十毫秒，而 115200 下每 87 µs
     * 就来一个字节 —— UART 收不上来就溢出丢字节（上一版就是这么失败的：
     * "只收到 5186/18230 字节"）。缓存到 PSRAM 后接收循环只剩 memcpy，
     * 速度远超波特率，就不会丢了。 */
    uint8_t *buf = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buf == NULL) {
        buf = malloc(size);
    }
    if (buf == NULL) {
        ESP_LOGE(TAG, "分配 %u 字节缓冲失败", (unsigned)size);
        return ESP_ERR_NO_MEM;
    }

    /* ⚠️ 这句必须在 malloc **之后**再喊。
     * 之前放在 malloc 前，服务器看到这句就开始发数据，而板子还在分配
     * 34 万字节的 PSRAM —— 那个窗口里 UART 缓冲被冲爆，开头丢几个字节，
     * 整段数据就错位了（实测：长度对、CRC 对不上，而且两次结果一模一样，
     * 说明不是随机噪声而是固定窗口）。 */
    image_drain_input(100);
    printf("开始发送数据\n");
    fflush(stdout);

    const int64_t t0 = esp_timer_get_time();
    esp_err_t err2 = read_exact(buf, size);
    const int64_t recv_ms = (esp_timer_get_time() - t0) / 1000;
    if (err2 != ESP_OK) {
        free(buf);
        ESP_LOGE(TAG, "接收失败（%lld ms 后）", (long long)recv_ms);
        return err2;
    }
    ESP_LOGI(TAG, "收完 %u 字节，用时 %lld ms，开始落盘", (unsigned)size,
             (long long)recv_ms);

    const uint32_t crc = esp_crc32_le(0, buf, size);
    if (crc != want_crc) {
        ESP_LOGE(TAG, "CRC 不匹配：收到 0x%08lX，算得 0x%08lX",
                 (unsigned long)want_crc, (unsigned long)crc);
        free(buf);
        return ESP_ERR_INVALID_CRC;
    }

    FILE *fp = fopen(path, "wb");
    if (fp == NULL) {
        ESP_LOGE(TAG, "创建 %s 失败。若是中文/长文件名，检查 CONFIG_FATFS_LFN_HEAP 是否打开"
                      "（本工程默认已开）；也检查 SFN 非法字符 : * ? \" < > |",
                 path);
        free(buf);
        return ESP_ERR_NOT_FOUND;
    }

    esp_err_t werr = ESP_OK;
    size_t written = 0;
    while (written < size) {
        size_t n = size - written;
        if (n > 64 * 1024) {
            n = 64 * 1024;
        }
        if (fwrite(buf + written, 1, n, fp) != n) {
            ESP_LOGE(TAG, "写盘失败（卡满了吗？）");
            werr = ESP_FAIL;
            break;
        }
        written += n;
    }
    fclose(fp);
    free(buf);

    if (werr != ESP_OK) {
        remove(path);
        return err;
    }

    ESP_LOGI(TAG, "文件保存完成 %s（%u 字节，接收 %lld ms）", path, (unsigned)size,
             (long long)recv_ms);
    if (out_path != NULL && out_path_len > 0) {
        strlcpy(out_path, path, out_path_len);
    }
    return ESP_OK;
}
