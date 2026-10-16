/* ------------------------------------------------------------------------
 * 3.7" JD7601 720x480 六色屏 —— 48 色方案验证（第 1、2 步）
 *
 * 目标：复现 FrameFilm 的 48 色方案。分步验证，从最安全的开始。
 *
 *   1  第 1 步：复位 + 五条初始化寄存器（0xE9 0x01 / 0x30 / 0x41 / 0xE0 / 0xE6）
 *              每步打印 BUSY 电平，看协议通不通
 *   2  第 2 步：只加载 full_clear 波形，推一帧全 0（黑）并刷新
 *              看波形能不能写进去、会不会卡、画面是否变黑
 *   3  第 3 步：只加载波形不刷新（诊断用）
 *
 * 接线：BUSY=42  CS=41  DC=40  RST=39  SDA=38  SCL=47
 * 本工程独立，不动 jd7601 / e6-730 / epd0356。
 * ---------------------------------------------------------------------- */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "epd370.h"
#include "image.h"
#include "palette_table.h"
#include "bmp.h"
#include "sdcard.h"
#include "waveforms.h"

static const char *TAG = "main";

/* 第 1 步：复位 + 初始化 */
static void step1_init(void)
{
    ESP_LOGI(TAG, "=== 第 1 步：复位 + 初始化 ===");
    const esp_err_t err = epd370_init();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "第 1 步通过：初始化没报错，BUSY 现为 %d", epd370_busy_level());
    } else {
        ESP_LOGE(TAG, "第 1 步失败: %s", esp_err_to_name(err));
    }
}

/* 第 2 步：只加载 full_clear，推全 0 帧并刷新 */
static void step2_full_clear(void)
{
    ESP_LOGI(TAG, "=== 第 2 步：full_clear 波形 + 全 0 帧 ===");

    esp_err_t err = epd370_wave(wf_full_clear, WF_LEN);
    ESP_LOGI(TAG, "写 full_clear 波形(%d 字节) -> %s，BUSY %d",
             WF_LEN, esp_err_to_name(err), epd370_busy_level());
    if (err != ESP_OK) {
        return;
    }

    err = epd370_fill(0x00);
    ESP_LOGI(TAG, "推全 0 帧(%d 字节) -> %s", EPD370_FRAME_BYTES, esp_err_to_name(err));
    if (err != ESP_OK) {
        return;
    }

    err = epd370_refresh(true, true);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "第 2 步通过：全屏应该变黑，且没有卡死");
    } else {
        ESP_LOGE(TAG, "第 2 步失败: %s", esp_err_to_name(err));
    }
}

/* 只写波形，不刷新（纯诊断） */
static void step_wave_only(void)
{
    ESP_LOGI(TAG, "=== 只写 full_clear 波形，不刷新 ===");
    const esp_err_t err = epd370_wave(wf_full_clear, WF_LEN);
    ESP_LOGI(TAG, "写入 -> %s，BUSY %d（画面应该没变化）",
             esp_err_to_name(err), epd370_busy_level());
}

/* ColorQual 3 相显示（先 full_clear 到已知起点，最稳） */
static esp_err_t display_qual(const uint8_t *index8)
{
    if (index8 == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "0) full_clear");
    esp_err_t e = epd370_wave(wf_full_clear, WF_LEN);
    if (e != ESP_OK) return e;
    (void)epd370_fill(0x00);
    e = epd370_refresh(true, true);
    if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "1) qual_phase1 + 高 3bit 平面");
    e = epd370_wave(wf_qual_phase1, WF_LEN);       if (e != ESP_OK) return e;
    e = epd370_write_index_plane(index8, 0);       if (e != ESP_OK) return e;
    e = epd370_refresh(true, false);               if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "2) qual_phase2（复用 RAM，不重传图）");
    e = epd370_wave(wf_qual_phase2, WF_LEN);       if (e != ESP_OK) return e;
    e = epd370_refresh(false, false);              if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "3) qual_phase3 + 低 3bit 平面");
    e = epd370_wave(wf_qual_phase3, WF_LEN);       if (e != ESP_OK) return e;
    e = epd370_write_index_plane(index8, 1);       if (e != ESP_OK) return e;
    e = epd370_refresh(false, true);               if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "显示完成");
    return ESP_OK;
}

/* ColorFast 2 相显示 */
static esp_err_t display_fast(const uint8_t *index8)
{
    if (index8 == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "0) full_clear");
    esp_err_t e = epd370_wave(wf_full_clear, WF_LEN);
    if (e != ESP_OK) return e;
    (void)epd370_fill(0x00);
    e = epd370_refresh(true, true);
    if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "1) fast_color_phase1 + 高 3bit 平面");
    e = epd370_wave(wf_fast_color_phase1, WF_LEN); if (e != ESP_OK) return e;
    e = epd370_write_index_plane(index8, 0);       if (e != ESP_OK) return e;
    e = epd370_refresh(true, false);               if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "2) fast_color_phase2 + 低 3bit 平面");
    e = epd370_wave(wf_fast_color_phase2, WF_LEN); if (e != ESP_OK) return e;
    e = epd370_write_index_plane(index8, 1);       if (e != ESP_OK) return e;
    e = epd370_refresh(false, true);               if (e != ESP_OK) return e;

    /* 重写高平面（不刷新），让控制器 RAM 和屏上状态一致 */
    e = epd370_write_index_plane(index8, 0);       if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "显示完成");
    return ESP_OK;
}

/* 索引卡：8x8 = 64 个格子，第 (r,c) 格填索引 r*8+c */
#define CARD_COLS 8
#define CARD_ROWS 8
#define CARD_CW   (EPD370_WIDTH / CARD_COLS)    /* 90 */
#define CARD_CH   (EPD370_HEIGHT / CARD_ROWS)   /* 60 */

static uint8_t *s_index;   /* 720x480 的 8bpp 索引缓冲 */

static void index_card_build(void)
{
    /* 先整屏填索引 3（rgb_qual 里最暗的候选），当格子之间的分隔 */
    memset(s_index, 3, (size_t)EPD370_WIDTH * EPD370_HEIGHT);

    for (int r = 0; r < CARD_ROWS; r++) {
        for (int c = 0; c < CARD_COLS; c++) {
            const uint8_t idx = (uint8_t)(r * CARD_COLS + c);
            const int x0 = c * CARD_CW + 2;
            const int x1 = (c + 1) * CARD_CW - 3;
            const int y0 = r * CARD_CH + 2;
            const int y1 = (r + 1) * CARD_CH - 3;
            for (int y = y0; y <= y1; y++) {
                uint8_t *row = s_index + (size_t)y * EPD370_WIDTH;
                memset(row + x0, idx, (size_t)(x1 - x0 + 1));
            }
        }
    }
}

static void step3_index_card(void)
{
    ESP_LOGI(TAG, "=== 第 3 步：索引卡（64 索引，ColorQual 3 相）===");
    if (s_index == NULL) {
        ESP_LOGE(TAG, "索引缓冲没分配");
        return;
    }
    index_card_build();
    if (display_qual(s_index) == ESP_OK) {
        ESP_LOGI(TAG, "第 3 步完成");
    } else {
        ESP_LOGE(TAG, "第 3 步失败");
    }
}

/* 从串口收一张 8bpp 索引图并显示 */
static void recv_and_display(bool use_fast)
{
    if (s_index == NULL) {
        ESP_LOGE(TAG, "索引缓冲没分配");
        return;
    }

    const size_t len = (size_t)EPD370_WIDTH * EPD370_HEIGHT;

    /* 没初始化过就先初始化：不然写波形会被 ESP_ERR_INVALID_STATE 拒掉 */
    if (!epd370_is_ready()) {
        ESP_LOGW(TAG, "还没初始化，先跑一遍 init");
        if (epd370_init() != ESP_OK) {
            ESP_LOGE(TAG, "初始化失败，放弃");
            return;
        }
    }

    ESP_LOGI(TAG, "=== 收图（%u 字节）-> %s ===", (unsigned)len,
             use_fast ? "ColorFast 2 相" : "ColorQual 3 相");

    if (image_recv_serial(s_index, len) != ESP_OK) {
        ESP_LOGE(TAG, "收图失败");
        return;
    }

    const esp_err_t e = use_fast ? display_fast(s_index) : display_qual(s_index);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "显示失败: %s", esp_err_to_name(e));
    }
}

/* ------------------------------------------------------ TF 卡上的 BMP 浏览 */

/* 48 色照片专门放这个子目录，和卡上其它东西隔开 */
#define IMAGE_DIR  SDCARD_MOUNT_POINT "/e6"

static sdcard_entry_t s_files[SDCARD_MAX_ENTRIES];
static int s_file_count;
static int s_file_index;
static bmp_rot_t s_rot = BMP_ROT_CW;   /* 竖版 BMP 的旋转方向 */

static const char *rot_name(bmp_rot_t r)
{
    return (r == BMP_ROT_CCW) ? "逆时针" : "顺时针";
}

/* 打印一张图的参数：大小 / BMP 尺寸 / 位深 / 颜色表项数。
 * 服务器靠这行给网页填列表，格式尽量稳定：
 *   [ 0] name.bmp  345910 B  480x720  8bpp  64色
 *   [ 1] bad.bmp   1234 B    (不是可用的 BMP)      */
static void sd_print_entry(int i)
{
    char path[SDCARD_NAME_MAX + 32];
    snprintf(path, sizeof(path), "%s/%s", IMAGE_DIR, s_files[i].name);

    struct stat st;
    const long size = (stat(path, &st) == 0) ? (long)st.st_size : -1;

    bmp_info_t info;
    if (bmp_read_info(path, &info) == ESP_OK) {
        printf("  [%2d] %s  %ld B  %dx%d  %ubpp  %u色\n",
               i, s_files[i].name, size, info.width, info.height,
               (unsigned)info.bit_depth, (unsigned)info.palette_colors);
    } else {
        printf("  [%2d] %s  %ld B  (不是可用的 BMP)\n", i, s_files[i].name, size);
    }
    fflush(stdout);
}

static void sd_scan(void)
{
    if (!sdcard_is_mounted() && sdcard_mount() != ESP_OK) {
        printf("TF 卡没挂上（卡插了吗？）\n");
        fflush(stdout);
        return;
    }

    (void)sdcard_ensure_dir(IMAGE_DIR);
    s_file_count = sdcard_scan_dir(IMAGE_DIR, ".bmp", s_files, SDCARD_MAX_ENTRIES);
    s_file_index = 0;

    printf("\n%s 里找到 %d 张 BMP：\n", IMAGE_DIR, s_file_count);
    for (int i = 0; i < s_file_count; i++) {
        sd_print_entry(i);
    }
    printf("按 'j' 输编号跳转，'n'/'p' 翻页，'d' 重显，'r' 换旋转方向\n");
    fflush(stdout);
}

/* 显示第 idx 张（越界会绕回） */
static void sd_show(int idx)
{
    if (s_file_count <= 0) {
        printf("还没有图片列表，先按 'l' 扫描 TF 卡\n");
        fflush(stdout);
        return;
    }
    if (idx < 0) {
        idx = s_file_count - 1;
    }
    if (idx >= s_file_count) {
        idx = 0;
    }
    s_file_index = idx;

    char path[SDCARD_NAME_MAX + 32];
    snprintf(path, sizeof(path), "%s/%s", IMAGE_DIR, s_files[idx].name);

    if (s_index == NULL) {
        return;
    }

    ESP_LOGI(TAG, "[%d/%d] 读 %s", idx + 1, s_file_count, s_files[idx].name);
    if (bmp_load_index8(path, s_index, EPD370_WIDTH, EPD370_HEIGHT, s_rot) != ESP_OK) {
        return;
    }

    if (!epd370_is_ready() && epd370_init() != ESP_OK) {
        ESP_LOGE(TAG, "初始化失败");
        return;
    }

    /* BMP 自带颜色表 —— 拿它和我们的两张色板比，判断用户选的是哪张，
     * 从而决定走 ColorQual 3 相还是 ColorFast 2 相。 */
    const int match_qual = bmp_count_palette_match(path, palette_qual, PALETTE_ENTRIES);
    const int match_fast = bmp_count_palette_match(path, palette_fast, PALETTE_ENTRIES);
    const bool use_fast = match_fast > match_qual;

    ESP_LOGI(TAG, "色板匹配：ColorQual %d / 64，ColorFast %d / 64 -> 走 %s",
             match_qual, match_fast, use_fast ? "ColorFast 2 相" : "ColorQual 3 相");
    if (match_qual < 32 && match_fast < 32) {
        ESP_LOGW(TAG, "两张都不太匹配 —— 导出时可能没选 48 色色板，颜色会不对");
    }

    const esp_err_t e = use_fast ? display_fast(s_index) : display_qual(s_index);

    /* 显示这十几秒里按的键全部丢掉 —— 免得网页连点导致连刷好几张 */
    image_drain_input(200);

    if (e != ESP_OK) {
        ESP_LOGE(TAG, "显示失败: %s", esp_err_to_name(e));
    }
}

/* 从串口收一个文件存到 TF 卡，然后直接显示它 */
static void recv_file_to_sd(void)
{
    if (!sdcard_is_mounted() && sdcard_mount() != ESP_OK) {
        ESP_LOGE(TAG, "TF 卡没挂上，收不了文件");
        return;
    }

    char path[256];
    if (image_recv_file(IMAGE_DIR, path, sizeof(path)) != ESP_OK) {
        ESP_LOGE(TAG, "收文件失败");
        return;
    }

    /* 存完顺手显示 —— 重扫一次列表，让新文件进目录 */
    s_file_count = sdcard_scan_dir(IMAGE_DIR, ".bmp", s_files, SDCARD_MAX_ENTRIES);
    s_file_index = 0;
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    for (int i = 0; i < s_file_count; i++) {
        if (strcasecmp(s_files[i].name, base) == 0) {
            sd_show(i);
            return;
        }
    }

    ESP_LOGW(TAG, "存好了但没在 .bmp 列表里找到 %s（不是 .bmp？）", base);
}

/* -------------------------------------------------------- 按编号跳转（'j'）
 * 网页要能点列表里的图直接显示，所以固件得支持"输编号 -> 显示第 N 张"。
 */
static bool s_jump_mode;
static char s_jump_digits[6];
static int  s_jump_len;

static bool jump_input(int c)
{
    if (c >= '0' && c <= '9') {
        if (s_jump_len < 5) {
            s_jump_digits[s_jump_len++] = (char)c;
        }
        return true;
    }

    if (c == '\n' || c == '\r') {
        if (s_jump_len > 0) {
            char tmp[8];
            memcpy(tmp, s_jump_digits, (size_t)s_jump_len);
            tmp[s_jump_len] = '\0';
            s_jump_len = 0;

            const int want = atoi(tmp);
            if (want >= 1 && want <= s_file_count) {
                s_jump_mode = false;
                sd_show(want - 1);
            } else {
                printf("编号超出范围 1~%d\n", s_file_count);
                fflush(stdout);
            }
        }
        return true;
    }

    if (c == 27 || c == 'x') {   /* ESC */
        s_jump_mode = false;
        printf("取消跳转\n");
        fflush(stdout);
        return true;
    }

    return true;   /* 跳转模式里其它键一律忽略 */
}

/* ---------------------------------------------------------------- 校准模式
 *
 * 目的：拿仪器逐个测出每个索引在**这块屏上的真实颜色**。
 * 有了实测色板，量化时按实测值选色，颜色才准（标称值是 FrameFilm 在
 * GDEH037E01 上标的，和我们这块玻璃未必完全一致）。
 *
 * 编号按「有效槽位」从 1 开始（不是原始索引）—— 量化只会在有效槽里选，
 * 占位槽没有校准的意义。
 *
 * 行为约定（按实际操作习惯定的）：
 *   - 开机直接进校准模式，不挂卡、不刷屏
 *   - 只在串口输入后才刷，绝不自动刷
 *   - 刷新期间按的键全部丢掉，不排队（避免连按跳过好几个编号）
 *   - 串口只输出三行：
 *       开始刷编号N，原始索引I
 *       刷新完成
 *       输入N进入下一个
 */
static bool s_cal_mode;
static int  s_cal_last = -1;   /* 上次刷过的编号（0-based），-1 = 还没刷过 */
static bool s_cal_fast;        /* 校准用哪张色板 */
static char s_cal_digits[8];
static int  s_cal_digits_len;

static void print_help(void);

static const uint8_t *cal_pal(void)
{
    return s_cal_fast ? (const uint8_t *)palette_fast : (const uint8_t *)palette_qual;
}

static const uint8_t *cal_active(void)
{
    return s_cal_fast ? palette_fast_active : palette_qual_active;
}

static int cal_len(void)
{
    return s_cal_fast ? palette_fast_active_len : palette_qual_active_len;
}

static const char *cal_pal_name(void)
{
    return s_cal_fast ? "ColorFast 2相" : "ColorQual 3相";
}

/* 刷一个编号（1-based）：满屏纯色，仪器好测 */
static void cal_flash(int num)
{
    const int n = cal_len();

    if (num < 1 || num > n) {
        printf("编号超出范围 1~%d\n", n);
        fflush(stdout);
        return;
    }
    if (s_index == NULL) {
        return;
    }

    const uint8_t idx = cal_active()[num - 1];
    s_cal_last = num - 1;

    printf("开始刷编号%d，原始索引%d\n", num, idx);
    fflush(stdout);

    memset(s_index, idx, (size_t)EPD370_WIDTH * EPD370_HEIGHT);
    const esp_err_t e = s_cal_fast ? display_fast(s_index) : display_qual(s_index);

    /* 刷新的这十几秒里按的键全部丢掉 —— 不排队，免得连按跳过好几个 */
    image_drain_input(200);

    if (e == ESP_OK) {
        printf("刷新完成\n");
    } else {
        printf("刷新失败: %s\n", esp_err_to_name(e));
    }
    printf("输入N进入下一个\n");
    fflush(stdout);
}

static void cal_enter(void)
{
    s_cal_mode = true;
    s_cal_last = -1;
    s_cal_digits_len = 0;

    /* 开机那点残留按键先丢掉 */
    image_drain_input(150);

    /* 校准期间把刷新过程的日志压掉，串口只留上面那三行 */
    esp_log_level_set("epd370", ESP_LOG_WARN);
    esp_log_level_set("main", ESP_LOG_WARN);

    printf("\n=== 校准模式：%s，共 %d 色 ===\n", cal_pal_name(), cal_len());
    printf("编号+回车 跳转 | N 下一个 | P 上一个 | F 切换色板 | X 退出\n");
    printf("输入N进入下一个\n");
    fflush(stdout);
}

static void cal_exit(void)
{
    s_cal_mode = false;
    esp_log_level_set("epd370", ESP_LOG_INFO);
    esp_log_level_set("main", ESP_LOG_INFO);
    printf("已退出校准模式\n");
    print_help();
}

/* 返回 true = 这个键被校准模式吃掉了；false = 让它走普通 CLI。
 *
 * 传图类命令（u/w/l/d）要能直接用 —— 否则从网页传图时板子还在校准模式里
 * 干等，什么都不发生。遇到这些就先退出校准再交给普通分支。 */
static bool cal_input(int c)
{
    /* 数字累积到回车再跳转 */
    if (c >= '0' && c <= '9') {
        if (s_cal_digits_len < 6) {
            s_cal_digits[s_cal_digits_len++] = (char)c;
        }
        return true;
    }

    if (c == '\n' || c == '\r') {
        if (s_cal_digits_len > 0) {
            char tmp[8];
            memcpy(tmp, s_cal_digits, (size_t)s_cal_digits_len);
            tmp[s_cal_digits_len] = '\0';
            s_cal_digits_len = 0;

            const int want = atoi(tmp);
            if (want >= 1) {
                cal_flash(want);
            }
        }
        return true;
    }

    s_cal_digits_len = 0;

    switch (c) {
        case 'n':
        case 'N':
            cal_flash(((s_cal_last + 1) % cal_len()) + 1);
            return true;
        case 'p':
        case 'P':
            cal_flash(((s_cal_last - 1 + cal_len()) % cal_len()) + 1);
            return true;
        case 'f':
        case 'F':
            /* 只切色板，不刷、不重排编号 —— 这样和校准表能对上 */
            s_cal_fast = !s_cal_fast;
            printf("已切到 %s（共 %d 色）\n", cal_pal_name(), cal_len());
            fflush(stdout);
            return true;
        case 'x':
        case 'X':
        case 27:   /* ESC */
            cal_exit();
            return true;

        /* 传图/看图命令：退出校准，交给普通分支执行 */
        case 'u':
        case 'w':
        case 'l':
        case 'd':
        case 'j':
            printf("（自动退出校准）\n");
            fflush(stdout);
            cal_exit();
            return false;

        default:
            return true;   /* 校准模式里其它键一律忽略，避免误触 */
    }
}

static void print_help(void)
{
    printf("\n"
           "--- 3.7\" JD7601 48色验证 ---\n"
           "  1  第1步：复位 + 初始化（验协议）\n"
           "  2  第2步：full_clear 波形 + 全0帧 + 刷新（全屏应变黑）\n"
           "  3  第3步：索引卡 64 格 + ColorQual 3 相（判玻璃匹配）\n"
           "  c  进入校准模式（逐个刷满屏，打实测用）\n"
           "  l  扫描 TF 卡 " IMAGE_DIR " 下的 .bmp（带大小/尺寸/色数）\n"
           "  j  输编号跳转到某张图\n"
           "  n  下一张     p  上一张     d  重显当前张\n"
           "  r  切换竖版 BMP 的旋转方向（顺时针/逆时针）\n"
           "  w  串口收一个文件存到 TF 卡并显示（host/send_file.py）\n"
           "  u  串口收一张 8bpp 索引图并用 ColorQual 3 相显示\n"
           "  f  同上但用 ColorFast 2 相\n"
           "  k  只写 full_clear 波形不刷新（诊断）\n"
           "  i  重新初始化\n"
           "  s  POF 断电\n"
           "  ?  这份帮助\n");
    fflush(stdout);
}

static void cli_loop(void)
{
    for (;;) {
        const int c = fgetc(stdin);
        if (c == EOF) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        /* 校准模式优先吃掉输入；传图命令会被放行给下面的普通分支 */
        if (s_cal_mode && cal_input(c)) {
            continue;
        }

        /* 跳转模式（按编号选图）同理 */
        if (s_jump_mode && jump_input(c)) {
            continue;
        }

        switch (c) {
            case '1': step1_init();        break;
            case '2': step2_full_clear();  break;
            case '3': step3_index_card();  break;
            case 'c':
                cal_enter();
                break;
            case 'l': sd_scan();           break;
            case 'j':
                if (s_file_count <= 0) {
                    sd_scan();          /* 还没扫过就先扫一遍 */
                }
                if (s_file_count > 0) {
                    s_jump_mode = true;
                    s_jump_len = 0;
                    printf("输编号 1~%d 然后回车（ESC 取消）\n", s_file_count);
                    fflush(stdout);
                }
                break;
            case 'n': if (s_file_count > 0) sd_show(s_file_index + 1); break;
            case 'p': if (s_file_count > 0) sd_show(s_file_index - 1); break;
            case 'd': sd_show(s_file_index); break;
            case 'r':
                s_rot = (s_rot == BMP_ROT_CW) ? BMP_ROT_CCW : BMP_ROT_CW;
                printf("旋转方向改成 %s，重显当前张\n", rot_name(s_rot));
                fflush(stdout);
                if (s_file_count > 0) {
                    sd_show(s_file_index);
                }
                break;
            case 'u': recv_and_display(false); break;
            case 'f': recv_and_display(true);  break;
            case 'w': recv_file_to_sd();       break;
            case 'k': step_wave_only();    break;
            case 'i':
                ESP_LOGI(TAG, "重新初始化");
                (void)epd370_init();
                break;
            case 's':
                ESP_LOGI(TAG, "POF -> %s", esp_err_to_name(epd370_power_off()));
                break;
            case '?':
            case '\n':
            case '\r':
                print_help();
                break;
            default:
                break;
        }
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "3.7\" JD7601 48色校准固件启动");

    /* 8bpp 索引缓冲：720*480 = 345600 字节，放 PSRAM */
    s_index = (uint8_t *)heap_caps_malloc((size_t)EPD370_WIDTH * EPD370_HEIGHT,
                                          MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_index == NULL) {
        ESP_LOGE(TAG, "索引缓冲分配失败（需要 %d 字节）", EPD370_WIDTH * EPD370_HEIGHT);
    }

    /* 只做复位和寄存器初始化，不刷屏 */
    if (epd370_init() != ESP_OK) {
        ESP_LOGW(TAG, "开机初始化没成功，按 'c' 会重试");
    }

    /* 按校准使用习惯：开机不挂卡、不刷屏，直接进校准模式等串口输入。
     * 要看 TF 卡上的图，先按 'x' 退出校准，再按 'l'。 */
    cal_enter();

    cli_loop();
}
