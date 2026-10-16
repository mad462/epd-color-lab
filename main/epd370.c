#include "epd370.h"
#include "pin_map.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "epd370";

#define EPD370_SPI_HOST   SPI2_HOST
/* FrameFilm 用 40 MHz；这里先 10 MHz 求稳，通了再往上提。 */
#define EPD370_SPI_HZ     (10 * 1000 * 1000)
#define EPD370_CHUNK      8192

/* JD7601 命令 */
#define CMD_WAVE_PRE      0x00   /* 波形载入前导，后跟 0x0F 0xA9 */
#define CMD_WRITE_LUT     0x20   /* 写波形表（799 字节） */
#define CMD_POF           0x02
#define CMD_PON           0x04
#define CMD_DSLP          0x07
#define CMD_DTM1          0x10
#define CMD_REF           0x12
#define CMD_PLL           0x30
#define CMD_UNK_41        0x41
#define CMD_SOFT_START    0xE9
#define CMD_TEMP          0xE0
#define CMD_UNK_E6        0xE6

static spi_device_handle_t s_spi;
static uint8_t            *s_bounce;
static uint8_t            *s_plane;      /* 172800 字节编码后的平面，PSRAM */
static bool                s_bus_up;
static bool                s_powered;

/* ------------------------------------------------------------------ 底层 */

static esp_err_t spi_setup(void)
{
    spi_bus_config_t buscfg = {
        .mosi_io_num     = eink_sda,
        .miso_io_num     = -1,
        .sclk_io_num     = eink_clk,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = EPD370_CHUNK,
    };
    esp_err_t err = spi_bus_initialize(EPD370_SPI_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_initialize: %s", esp_err_to_name(err));
        return err;
    }
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = EPD370_SPI_HZ,
        .mode           = 0,
        .spics_io_num   = -1,     /* CS 自己拉 */
        .queue_size     = 1,
    };
    return spi_bus_add_device(EPD370_SPI_HOST, &devcfg, &s_spi);
}

static void gpio_setup(void)
{
    const gpio_config_t out_cfg = {
        .pin_bit_mask = (1ULL << eink_cs) | (1ULL << eink_dc) | (1ULL << eink_rst),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&out_cfg));

    /* FrameFilm 的 BUSY 不加上拉；我们板子上 GPIO42 走排针，加上拉更稳。
     * 注意这会让"面板没接"时读成 1（空闲），要靠 wait_cycle 是否见到
     * "进入忙"来区分。 */
    const gpio_config_t in_cfg = {
        .pin_bit_mask = (1ULL << eink_busy),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&in_cfg));

    gpio_set_level(eink_cs, 1);
    gpio_set_level(eink_dc, 1);
    gpio_set_level(eink_rst, 1);
}

int epd370_busy_level(void)
{
    return gpio_get_level(eink_busy);
}

bool epd370_is_ready(void)
{
    return s_bus_up;
}

/* ------------------------------------------------------------------ 收发 */

esp_err_t epd370_cmd(uint8_t cmd, const uint8_t *data, size_t len)
{
    if (!s_bus_up) {
        return ESP_ERR_INVALID_STATE;
    }

    gpio_set_level(eink_dc, 0);
    gpio_set_level(eink_cs, 0);

    /* 必须带 SPI_TRANS_USE_TXDATA，否则 tx_data 会被当 tx_buffer 指针解引用 */
    spi_transaction_t t = {0};
    t.length     = 8;
    t.flags      = SPI_TRANS_USE_TXDATA;
    t.tx_data[0] = cmd;
    esp_err_t err = spi_device_polling_transmit(s_spi, &t);
    if (err != ESP_OK) {
        gpio_set_level(eink_cs, 1);
        return err;
    }

    if (data != NULL && len > 0) {
        gpio_set_level(eink_dc, 1);
        size_t off = 0;
        while (off < len) {
            size_t n = len - off;
            if (n > EPD370_CHUNK) {
                n = EPD370_CHUNK;
            }
            memcpy(s_bounce, data + off, n);
            spi_transaction_t d = {0};
            d.length    = n * 8;
            d.tx_buffer = s_bounce;
            err = spi_device_polling_transmit(s_spi, &d);
            if (err != ESP_OK) {
                gpio_set_level(eink_cs, 1);
                return err;
            }
            off += n;
        }
    }

    gpio_set_level(eink_cs, 1);
    return ESP_OK;
}

/* ------------------------------------------------------------------ BUSY */

int32_t epd370_wait_cycle(uint32_t assert_ms, uint32_t release_ms, bool *asserted)
{
    if (asserted) {
        *asserted = false;
    }

    /* 1) 等 BUSY 拉低 = 芯片进入忙 */
    const int64_t t0 = esp_timer_get_time();
    while (epd370_busy_level() != 0) {
        if ((esp_timer_get_time() - t0) / 1000 > (int64_t)assert_ms) {
            ESP_LOGW(TAG, "  BUSY 没进入忙（%u ms 内一直是高）", (unsigned)assert_ms);
            return -1;
        }
        vTaskDelay(1);
    }
    const int32_t assert_took = (int32_t)((esp_timer_get_time() - t0) / 1000);
    if (asserted) {
        *asserted = true;
    }

    /* 2) 等 BUSY 拉高 = 释放 */
    const int64_t t1 = esp_timer_get_time();
    while (epd370_busy_level() != 1) {
        if ((esp_timer_get_time() - t1) / 1000 > (int64_t)release_ms) {
            ESP_LOGW(TAG, "  BUSY 没释放（%u ms）", (unsigned)release_ms);
            return -1;
        }
        vTaskDelay(1);
    }
    return assert_took;
}

/* 只等 BUSY 回到空闲。FrameFilm 的 epd_wait_busy() 就是这个语义 ——
 * 用在初始化那几条寄存器之后：它们不一定触发忙，硬要求"先进忙"会白等。 */
int32_t epd370_wait_idle(uint32_t timeout_ms)
{
    const int64_t t0 = esp_timer_get_time();
    while (epd370_busy_level() != 1) {
        if ((esp_timer_get_time() - t0) / 1000 > (int64_t)timeout_ms) {
            ESP_LOGW(TAG, "  BUSY 等空闲超时 %u ms（现为 %d）",
                     (unsigned)timeout_ms, epd370_busy_level());
            return -1;
        }
        vTaskDelay(1);
    }
    return (int32_t)((esp_timer_get_time() - t0) / 1000);
}

/* ------------------------------------------------------------------ 流程 */

void epd370_hw_reset(void)
{
    gpio_set_level(eink_rst, 0); vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(eink_rst, 1); vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(eink_rst, 0); vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(eink_rst, 1); vTaskDelay(pdMS_TO_TICKS(10));
}

esp_err_t epd370_init(void)
{
    if (!s_bus_up) {
        gpio_setup();
        esp_err_t err = spi_setup();
        if (err != ESP_OK) {
            return err;
        }
        s_bounce = (uint8_t *)heap_caps_malloc(EPD370_CHUNK,
                                               MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (s_bounce == NULL) {
            return ESP_ERR_NO_MEM;
        }
        s_bus_up = true;
    }

    s_powered = false;
    ESP_LOGI(TAG, "复位前 BUSY = %d（低=忙；悬空有上拉应是 1）", epd370_busy_level());
    epd370_hw_reset();
    ESP_LOGI(TAG, "复位后 BUSY = %d", epd370_busy_level());

    bool asserted = false;
    int32_t ms = epd370_wait_cycle(15000, 15000, &asserted);
    ESP_LOGI(TAG, "复位后 wait_cycle -> %ld ms，见到忙=%d，BUSY 现为 %d",
             (long)ms, (int)asserted, epd370_busy_level());

    /* 0xE9 0x01 软启动 */
    {
        const uint8_t v = 0x01;
        esp_err_t e = epd370_cmd(CMD_SOFT_START, &v, 1);
        ESP_LOGI(TAG, "0xE9 0x01 -> %s", esp_err_to_name(e));
        if (e != ESP_OK) {
            return e;
        }
    }
    /* 注意：这里只等"回到空闲"，不要求它先进忙 ——
     * FrameFilm 的 epd_wait_busy() 就是这个语义，软启动不一定触发忙。 */
    ms = epd370_wait_idle(15000);
    ESP_LOGI(TAG, "软启动后等空闲 -> %ld ms，BUSY 现为 %d",
             (long)ms, epd370_busy_level());

    /* 四条初始化寄存器（FrameFilm 原样照抄） */
    {
        const uint8_t v30 = 0x04, v41 = 0x00, vE0 = 0x02, vE6 = 0x19;
        struct { uint8_t cmd; const uint8_t *p; const char *name; } seq[] = {
            {CMD_PLL,        &v30, "0x30 PLL"},
            {CMD_UNK_41,     &v41, "0x41"},
            {CMD_TEMP,       &vE0, "0xE0 温度"},
            {CMD_UNK_E6,     &vE6, "0xE6"},
        };
        for (size_t i = 0; i < sizeof(seq) / sizeof(seq[0]); i++) {
            esp_err_t e = epd370_cmd(seq[i].cmd, seq[i].p, 1);
            ESP_LOGI(TAG, "%s -> %s（BUSY %d）", seq[i].name, esp_err_to_name(e),
                     epd370_busy_level());
            if (e != ESP_OK) {
                return e;
            }
        }
    }

    ESP_LOGI(TAG, "init done");
    return ESP_OK;
}

esp_err_t epd370_wave(const uint8_t *lut, size_t len)
{
    if (lut == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    /* 前导：0x00 + 0x0F 0xA9（0xA9 的 bit7 = 1 = LUT_EN） */
    const uint8_t pre[2] = {0x0F, 0xA9};
    esp_err_t err = epd370_cmd(CMD_WAVE_PRE, pre, sizeof(pre));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "波形前导失败: %s", esp_err_to_name(err));
        return err;
    }

    err = epd370_cmd(CMD_WRITE_LUT, lut, len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "写波形表失败: %s", esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}

esp_err_t epd370_write_index_plane(const uint8_t *index8, int plane)
{
    /* 把 8bpp 索引缓冲里的一个 3bit 平面编码成 4bpp（2 像素/字节）再推。
     *   plane 0 -> index >> 3   平面 1 -> index & 7
     * 和 FrameFilm 的 epd_spectra_encode_row 一致。 */
    if (index8 == NULL || (plane != 0 && plane != 1)) {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_plane == NULL) {
        s_plane = (uint8_t *)heap_caps_malloc(EPD370_FRAME_BYTES,
                                              MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_plane == NULL) {
            ESP_LOGE(TAG, "平面缓冲分配失败（%d 字节）", EPD370_FRAME_BYTES);
            return ESP_ERR_NO_MEM;
        }
    }

    for (int y = 0; y < EPD370_HEIGHT; y++) {
        const uint8_t *row = index8 + (size_t)y * EPD370_WIDTH;
        uint8_t *out = s_plane + (size_t)y * EPD370_BPL;
        for (int x = 0; x < EPD370_WIDTH; x += 2) {
            uint8_t a = row[x];
            uint8_t b = row[x + 1];
            if (plane == 0) {
                a >>= 3;
                b >>= 3;
            } else {
                a &= 7;
                b &= 7;
            }
            *out++ = (uint8_t)((a << 4) | b);
        }
    }

    return epd370_cmd(CMD_DTM1, s_plane, EPD370_FRAME_BYTES);
}

esp_err_t epd370_fill(uint8_t value)
{
    static uint8_t line[EPD370_BPL];

    memset(line, value, sizeof(line));

    esp_err_t err = epd370_cmd(CMD_DTM1, NULL, 0);
    if (err != ESP_OK) {
        return err;
    }

    /* 手工发 480 行，避免一次性分配 172800 的中转 */
    if (!s_bus_up) {
        return ESP_ERR_INVALID_STATE;
    }
    gpio_set_level(eink_dc, 1);
    gpio_set_level(eink_cs, 0);
    for (int row = 0; row < EPD370_HEIGHT; row++) {
        memcpy(s_bounce, line, EPD370_BPL);
        spi_transaction_t d = {0};
        d.length    = EPD370_BPL * 8;
        d.tx_buffer = s_bounce;
        err = spi_device_polling_transmit(s_spi, &d);
        if (err != ESP_OK) {
            break;
        }
    }
    gpio_set_level(eink_cs, 1);
    return err;
}

esp_err_t epd370_power_off(void)
{
    const uint8_t v = 0x00;
    esp_err_t err = epd370_cmd(CMD_POF, &v, 1);
    if (err != ESP_OK) {
        return err;
    }
    (void)epd370_wait_cycle(1000, 60000, NULL);
    vTaskDelay(pdMS_TO_TICKS(20));
    s_powered = false;
    return ESP_OK;
}

esp_err_t epd370_refresh(bool power_on, bool power_off)
{
    if (power_on && !s_powered) {
        esp_err_t e = epd370_cmd(CMD_PON, NULL, 0);
        if (e != ESP_OK) {
            return e;
        }
        (void)epd370_wait_cycle(1000, 60000, NULL);
        vTaskDelay(pdMS_TO_TICKS(10));
        s_powered = true;
    }

    const int64_t t0 = esp_timer_get_time();
    const uint8_t v = 0x00;
    esp_err_t err = epd370_cmd(CMD_REF, &v, 1);
    if (err != ESP_OK) {
        return err;
    }

    bool asserted = false;
    const int32_t ms = epd370_wait_cycle(1000, 120000, &asserted);
    if (ms < 0) {
        ESP_LOGE(TAG, "刷新 wait_cycle 失败（见到忙=%d）", (int)asserted);
        return ESP_ERR_TIMEOUT;
    }
    ESP_LOGI(TAG, "刷新完成：进入忙 %ld ms，总计 %lld ms",
             (long)ms, (long long)((esp_timer_get_time() - t0) / 1000));
    vTaskDelay(pdMS_TO_TICKS(10));

    if (power_off) {
        return epd370_power_off();
    }
    return ESP_OK;
}
