/* 单色 UI 演示：屏幕上一个大数字递增
 *
 * 目的：验证 MonoFast（黑白差分快刷）在我们这块屏上到底行不行、有多快。
 * 每刷一帧都会把耗时打到串口，方便看真实数据。
 *
 * 串口命令：
 *   s   开始/停止自动递增
 *   n   手动 +1
 *   c   全清（黑）
 *   f   强制全帧刷新（不做差分，用来对比差分省了多少）
 */

#include <stdio.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "epd370.h"
#include "mono.h"
#include "waveforms.h"

static const char *TAG = "demo";

static int s_count = 0;
static bool s_running = true;

/* 把一个整数画成七段数码管（右对齐，位数由 digits 指定） */
static void draw_number(int value, int digits, int x, int y, int dw, int dh, int gap)
{
    int v = value;
    for (int i = digits - 1; i >= 0; i--) {
        const int d = v % 10;
        v /= 10;
        mono_digit_seg(x + i * (dw + gap), y, dw, dh, d, dh / 8, true);
    }
}

static void compose(void)
{
    mono_clear(false);                       /* 白底 */

    /* 外框 */
    mono_thick_rect(12, 12, MONO_W - 24, MONO_H - 24, 4, true);

    /* 标题 */
    mono_text5x7(40, 40, 3, "MONO FAST", true);
    mono_text5x7(40, 74, 3, "COUNTER", true);

    /* 大数字：中间，5 位 */
    const int dw = 96, dh = 180, gap = 18, digits = 5;
    const int total = digits * dw + (digits - 1) * gap;
    draw_number(s_count, digits, (MONO_W - total) / 2, 150, dw, dh, gap);

    /* 底部一行小字，顺便显示计数值，便于和串口日志对照 */
    char buf[48];
    snprintf(buf, sizeof(buf), "%d", s_count);
    mono_text5x7(40, MONO_H - 60, 3, "N=", true);
    mono_text5x7(40 + 6 * 3 * 3, MONO_H - 60, 3, buf, true);

    /* 底部进度条：每 20 步走一格，用来看差分刷新有没有拖影 */
    const int bar_x = 300, bar_y = MONO_H - 58, bar_w = MONO_W - 340, bar_h = 18;
    mono_rect(bar_x, bar_y, bar_w, bar_h, true);
    const int fill = (s_count % 40) * (bar_w - 8) / 40;
    mono_fill(bar_x + 4, bar_y + 4, fill, bar_h - 8, true);
}

static void tick(void)
{
    compose();

    const int64_t t0 = esp_timer_get_time();
    const esp_err_t e = mono_push();
    const int64_t ms = (esp_timer_get_time() - t0) / 1000;

    if (e != ESP_OK) {
        ESP_LOGE(TAG, "push 失败: %s", esp_err_to_name(e));
    } else {
        ESP_LOGI(TAG, "count=%-5d  帧耗时 %lld ms", s_count, (long long)ms);
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "=== 3.7\" 单色 UI 演示（MonoFast 差分快刷）===");

    ESP_ERROR_CHECK(epd370_init());
    ESP_LOGI(TAG, "面板初始化完成");

    /* 进入单色模式（内部做一次全清，把面板推到已知起点） */
    const int64_t te = esp_timer_get_time();
    ESP_ERROR_CHECK(mono_enter());
    ESP_LOGI(TAG, "进入单色模式耗时 %lld ms", (long long)((esp_timer_get_time() - te) / 1000));

    /* 会话模式：连续刷帧时保持上电，省掉每帧的 PON/POF */
    mono_session_begin();

    /* 先刷一帧，看首帧（全屏都变）要多久 */
    tick();

    while (1) {
        if (s_running) {
            s_count++;
            tick();
            vTaskDelay(pdMS_TO_TICKS(700));
        } else {
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}
