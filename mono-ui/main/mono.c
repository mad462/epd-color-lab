#include "mono.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "epd370.h"
#include "waveforms.h"

static const char *TAG = "mono";

/* 位图：1 = 黑、0 = 白（和 FrameFilm / .film 约定一致） */
static uint8_t *s_fb = NULL;

/* 上一帧的**码位域**（不是位图）。清屏后面板实际是黑 -> 码位全 0。 */
static uint8_t *s_prev = NULL;

static bool s_session = false;   /* 会话模式：帧间保持上电 */
static bool s_inited  = false;   /* 上一帧有没有建立过 */
static bool s_entered = false;   /* 有没有做过进入 mono 的全清 */

/* 码位约定：1 = 白。位图是 1 = 黑，所以取反。 */
static inline int mono_code_bit(int bitmap_bit) { return bitmap_bit ? 0 : 1; }

static void build_tables(void);   /* 定义在下面，ensure_buf 里要先调 */

static bool ensure_buf(void)
{
    if (s_fb) {
        return true;
    }
    /* 放**内部 RAM**，不放 PSRAM。
     * 编码那一趟要逐像素读 s_fb、读写 s_prev（每帧 34.5 万次随机访问），
     * PSRAM 比内部 RAM 慢好几倍 —— 实测放 PSRAM 时光编码就 ~76 ms。
     * 两个缓冲各 42 KB，内部 SRAM 放得下。 */
    s_fb = heap_caps_malloc(MONO_FB_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    s_prev = heap_caps_malloc(MONO_FB_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_fb || !s_prev) {
        ESP_LOGE(TAG, "内部 RAM 分配失败（各需 %d 字节），退回 PSRAM", MONO_FB_BYTES);
        if (!s_fb) {
            s_fb = heap_caps_malloc(MONO_FB_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        }
        if (!s_prev) {
            s_prev = heap_caps_malloc(MONO_FB_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        }
    }
    if (!s_fb || !s_prev) {
        ESP_LOGE(TAG, "缓冲分配彻底失败");
        return false;
    }
    build_tables();
    memset(s_fb, 0x00, MONO_FB_BYTES);      /* 全白（位图 0） */
    memset(s_prev, 0x00, MONO_FB_BYTES);    /* 码位 0 = 黑，对清屏后的状态 */
    ESP_LOGI(TAG, "帧缓冲 %d 字节 x2", MONO_FB_BYTES);
    return true;
}

uint8_t *mono_fb(void)
{
    ensure_buf();
    return s_fb;
}

/* ---------------------------------------------------------------- 绘制 */

static inline void set_px(uint8_t *fb, int x, int y, bool black)
{
    if (x < 0 || x >= MONO_W || y < 0 || y >= MONO_H) {
        return;
    }
    uint8_t *p = fb + y * MONO_STRIDE + (x >> 3);
    const uint8_t mask = (uint8_t)(0x80 >> (x & 7));
    if (black) {
        *p |= mask;
    } else {
        *p &= (uint8_t)~mask;
    }
}

static inline bool get_px(const uint8_t *fb, int x, int y)
{
    return (fb[y * MONO_STRIDE + (x >> 3)] >> (7 - (x & 7))) & 1;
}

void mono_clear(bool black)
{
    if (!ensure_buf()) {
        return;
    }
    memset(s_fb, black ? 0xFF : 0x00, MONO_FB_BYTES);
}

void mono_pixel(int x, int y, bool black) { set_px(s_fb, x, y, black); }

void mono_hline(int x, int y, int w, bool black)
{
    for (int i = 0; i < w; i++) {
        set_px(s_fb, x + i, y, black);
    }
}

void mono_vline(int x, int y, int h, bool black)
{
    for (int i = 0; i < h; i++) {
        set_px(s_fb, x, y + i, black);
    }
}

void mono_fill(int x, int y, int w, int h, bool black)
{
    for (int j = 0; j < h; j++) {
        mono_hline(x, y + j, w, black);
    }
}

void mono_rect(int x, int y, int w, int h, bool black)
{
    mono_hline(x, y, w, black);
    mono_hline(x, y + h - 1, w, black);
    mono_vline(x, y, h, black);
    mono_vline(x + w - 1, y, h, black);
}

void mono_thick_rect(int x, int y, int w, int h, int t, bool black)
{
    mono_fill(x, y, w, t, black);                 /* 上 */
    mono_fill(x, y + h - t, w, t, black);         /* 下 */
    mono_fill(x, y, t, h, black);                 /* 左 */
    mono_fill(x + w - t, y, t, h, black);         /* 右 */
}

/* ---------------------------------------------------------------- 七段数字
 *
 *         aaa
 *       f     b
 *       f     b
 *         ggg
 *       e     c
 *       e     c
 *         ddd
 *
 * 段号：bit0=a bit1=b bit2=c bit3=d bit4=e bit5=f bit6=g
 */
static const uint8_t SEG[10] = {
    0x3F,   /* 0: abcdef   */
    0x06,   /* 1: bc       */
    0x5B,   /* 2: abdeg    */
    0x4F,   /* 3: abcdg    */
    0x66,   /* 4: bcfg     */
    0x6D,   /* 5: acdfg    */
    0x7D,   /* 6: acdefg   */
    0x07,   /* 7: abc      */
    0x7F,   /* 8: abcdefg  */
    0x6F,   /* 9: abcdfg   */
};

void mono_digit_seg(int x, int y, int w, int h, int digit, int t, bool black)
{
    if (digit < 0 || digit > 9) {
        return;
    }
    const uint8_t s = SEG[digit];

    /* 段长：竖段 (h - 3t) / 2，横段 w - 2t */
    const int vlen = (h - 3 * t) / 2;
    const int hlen = w - 2 * t;
    const int ym = y + t + vlen;          /* 中间横段位置 */

    if (s & 0x01) { mono_fill(x + t, y, hlen, t, black); }                 /* a 上 */
    if (s & 0x02) { mono_fill(x + w - t, y + t, t, vlen, black); }         /* b 右上 */
    if (s & 0x04) { mono_fill(x + w - t, ym + t, t, vlen, black); }        /* c 右下 */
    if (s & 0x08) { mono_fill(x + t, y + h - t, hlen, t, black); }         /* d 下 */
    if (s & 0x10) { mono_fill(x, ym + t, t, vlen, black); }                /* e 左下 */
    if (s & 0x20) { mono_fill(x, y + t, t, vlen, black); }                 /* f 左上 */
    if (s & 0x40) { mono_fill(x + t, ym, hlen, t, black); }                /* g 中 */
}

/* ---------------------------------------------------------------- 5x7 小字 */

static const uint8_t FONT5x7[][5] = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, /* 空格 */
    {0x00, 0x00, 0x5F, 0x00, 0x00}, /* ! */
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, /* 0 */
    {0x00, 0x42, 0x7F, 0x40, 0x00}, /* 1 */
    {0x42, 0x61, 0x51, 0x49, 0x46}, /* 2 */
    {0x21, 0x41, 0x45, 0x4B, 0x31}, /* 3 */
    {0x18, 0x14, 0x12, 0x7F, 0x10}, /* 4 */
    {0x27, 0x45, 0x45, 0x45, 0x39}, /* 5 */
    {0x3C, 0x4A, 0x49, 0x49, 0x30}, /* 6 */
    {0x01, 0x71, 0x09, 0x05, 0x03}, /* 7 */
    {0x36, 0x49, 0x49, 0x49, 0x36}, /* 8 */
    {0x06, 0x49, 0x49, 0x29, 0x1E}, /* 9 */
    {0x7F, 0x09, 0x09, 0x09, 0x01}, /* F */
    {0x7F, 0x49, 0x49, 0x49, 0x41}, /* E */
    {0x3E, 0x41, 0x41, 0x41, 0x22}, /* C */
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, /* U */
    {0x7F, 0x49, 0x49, 0x49, 0x36}, /* B */
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, /* Q */
    {0x7F, 0x40, 0x40, 0x40, 0x40}, /* L */
};

/* 返回 5x7 字模下标；不认识的字符给空格 */
static int font_index(char c)
{
    if (c >= '0' && c <= '9') return 2 + (c - '0');
    switch (c) {
        case ' ': return 0;
        case '!': return 1;
        case 'F': case 'f': return 12;
        case 'E': case 'e': return 13;
        case 'C': case 'c': return 14;
        case 'U': case 'u': return 15;
        case 'B': case 'b': return 16;
        case 'Q': case 'q': return 17;
        case 'L': case 'l': return 18;
        default: return 0;
    }
}

void mono_text5x7(int x, int y, int scale, const char *s, bool black)
{
    for (; *s; s++, x += 6 * scale) {
        const uint8_t *g = FONT5x7[font_index(*s)];
        for (int col = 0; col < 5; col++) {
            for (int row = 0; row < 7; row++) {
                if (g[col] & (1 << row)) {
                    mono_fill(x + col * scale, y + row * scale, scale, scale, black);
                }
            }
        }
    }
}

/* ---------------------------------------------------------------- 差分推屏
 *
 * 每个像素一个 4bit 半字节，低 2 位 = 码 (2*上一帧 + 这一帧)。
 *   偶数像素放高半字节（码在 bit5:4），奇数像素放低半字节（码在 bit1:0）
 * 一行 720 像素 -> 360 字节。
 *
 * s_prev 存的是**码位域**（1=白），不是位图 —— 所以更新时按字节取反即可。
 */
/* 每次 SPI 事务发多少行。
 * 逐行发是 480 次事务，每次的固定开销加起来约 100 ms；
 * 上限 8 KB / 360 字节 = 22 行，取 20 行 = 7200 字节，24 次发完。 */
#define MONO_ROWS_PER_CHUNK  20
#define MONO_CHUNK_BYTES     (MONO_LINE_BYTES * MONO_ROWS_PER_CHUNK)

/* ---------------------------------------------------------------- 查表编码
 *
 * 逐像素移位要 34.5 万次迭代，实测光编码就 ~75 ms（160 MHz 下每像素约 35 周期）。
 * 但一个字节的编码是**纯函数**：(上一帧字节, 位图字节) -> 4 个输出字节。
 * 拆开看，old 位只来自 p、new 位只来自 s，所以两张 256x4 的表就够：
 *
 *     输出[j] = TAB_P[prev][j] | TAB_S[src][j]
 *
 * 每字节从 8 轮移位变成 2 次查表 + 4 次或，编码降到 ~8 ms。
 */
static uint8_t TAB_P[256][4];   /* 上一帧字节 -> 各输出字节的 old 位 */
static uint8_t TAB_S[256][4];   /* 位图字节  -> 各输出字节的 new 位（= 取反后） */

static void build_tables(void)
{
    for (int v = 0; v < 256; v++) {
        for (int j = 0; j < 4; j++) {
            const int hi = 7 - 2 * j;        /* 偶数像素的位号 -> 高半字节 */
            const int lo = hi - 1;           /* 奇数像素       -> 低半字节 */
            const int h = (v >> hi) & 1;
            const int l = (v >> lo) & 1;
            /* 码 = 2*old + new；old 放 bit5/bit1，new（取反）放 bit4/bit0 */
            TAB_P[v][j] = (uint8_t)((h << 5) | (l << 1));
            TAB_S[v][j] = (uint8_t)(((1 - h) << 4) | (1 - l));
        }
    }
}

static esp_err_t push_frame(const uint8_t *lut, const uint8_t *bmp)
{
    /* 必须放内部 RAM —— SPI DMA 读不了 PSRAM */
    static uint8_t chunk[MONO_CHUNK_BYTES];
    static uint8_t blank[MONO_STRIDE];

    memset(blank, 0x00, sizeof(blank));     /* 位图全 0 = 全白 */

    esp_err_t e = epd370_wave(lut, WF_LEN);
    if (e != ESP_OK) {
        return e;
    }
    e = epd370_dtm1_begin();
    if (e != ESP_OK) {
        return e;
    }

    const int64_t t0 = esp_timer_get_time();
    int in_chunk = 0;
    for (int y = 0; y < MONO_H; y++) {
        const uint8_t *src = bmp ? (bmp + y * MONO_STRIDE) : blank;
        uint8_t *prev = s_prev + y * MONO_STRIDE;
        uint8_t *line = chunk + in_chunk * MONO_LINE_BYTES;

        memset(line, 0, MONO_LINE_BYTES);
        /* 一个源字节（8 像素）-> 4 个输出字节，全靠查表 */
        for (int bx = 0; bx < MONO_STRIDE; bx++) {
            const uint8_t p = prev[bx];
            const uint8_t s = src[bx];
            uint8_t *o = line + bx * 4;
            o[0] = (uint8_t)(TAB_P[p][0] | TAB_S[s][0]);
            o[1] = (uint8_t)(TAB_P[p][1] | TAB_S[s][1]);
            o[2] = (uint8_t)(TAB_P[p][2] | TAB_S[s][2]);
            o[3] = (uint8_t)(TAB_P[p][3] | TAB_S[s][3]);
        }
        /* 这一行的新状态成为下一帧的"上一帧"（码位域 = 位图取反） */
        for (int i = 0; i < MONO_STRIDE; i++) {
            prev[i] = (uint8_t)~src[i];
        }

        in_chunk++;
        if (in_chunk == MONO_ROWS_PER_CHUNK || y == MONO_H - 1) {
            e = epd370_dtm1_bulk(chunk, (size_t)in_chunk * MONO_LINE_BYTES);
            if (e != ESP_OK) {
                break;
            }
            in_chunk = 0;
        }
    }
    epd370_dtm1_end();
    if (e != ESP_OK) {
        return e;
    }
    const int64_t t1 = esp_timer_get_time();
    const esp_err_t er = epd370_refresh(true, !s_session);
    const int64_t t2 = esp_timer_get_time();
    ESP_LOGI(TAG, "分段时间：编码+SPI %lld ms   刷新 %lld ms",
             (long long)((t1 - t0) / 1000), (long long)((t2 - t1) / 1000));
    return er;
}

esp_err_t mono_enter(void)
{
    if (!ensure_buf()) {
        return ESP_ERR_NO_MEM;
    }
    /* 先全清到已知起点（黑），上一帧码位随之复位为 0 */
    esp_err_t e = epd370_wave(wf_mono_clear, WF_LEN);
    if (e != ESP_OK) {
        return e;
    }
    e = epd370_dtm1_begin();
    if (e != ESP_OK) {
        return e;
    }
    static uint8_t line[MONO_LINE_BYTES];
    memset(line, 0, sizeof(line));
    for (int y = 0; y < MONO_H; y++) {
        e = epd370_dtm1_row(line, sizeof(line));
        if (e != ESP_OK) {
            break;
        }
    }
    epd370_dtm1_end();
    if (e != ESP_OK) {
        return e;
    }
    e = epd370_refresh(true, true);
    memset(s_prev, 0x00, MONO_FB_BYTES);
    s_inited = true;
    s_entered = true;
    ESP_LOGI(TAG, "进入单色模式（全清完成）");
    return e;
}

esp_err_t mono_push(void)
{
    if (!ensure_buf()) {
        return ESP_ERR_NO_MEM;
    }
    if (!s_entered) {
        esp_err_t e = mono_enter();
        if (e != ESP_OK) {
            return e;
        }
    }
    /* 保持上电：会话里不 POF，省掉每帧约 100ms 的上下电 */
    return push_frame(wf_mono_fast, s_fb);
}

void mono_session_begin(void) { s_session = true; }

void mono_session_end(void)
{
    if (s_session) {
        s_session = false;
        epd370_power_off();
    }
}
