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

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "device_api.h"
#include "epd370.h"
#include "image.h"
#include "net.h"
#include "palette_table.h"
#include "bmp.h"
#include "sdcard.h"
#include "waveforms.h"
#include "wifi_store.h"

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
    device_set_phase("refresh1", 8);
    esp_err_t e = epd370_wave(wf_full_clear, WF_LEN);
    if (e != ESP_OK) return e;
    (void)epd370_fill(0x00);
    e = epd370_refresh(true, true);
    if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "1) qual_phase1 + 高 3bit 平面");
    device_set_phase("refresh2", 30);
    e = epd370_wave(wf_qual_phase1, WF_LEN);       if (e != ESP_OK) return e;
    e = epd370_write_index_plane(index8, 0);       if (e != ESP_OK) return e;
    e = epd370_refresh(true, false);               if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "2) qual_phase2（复用 RAM，不重传图）");
    device_set_phase("refresh3", 62);
    e = epd370_wave(wf_qual_phase2, WF_LEN);       if (e != ESP_OK) return e;
    e = epd370_refresh(false, false);              if (e != ESP_OK) return e;

    ESP_LOGI(TAG, "3) qual_phase3 + 低 3bit 平面");
    device_set_phase("refresh3", 92);
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


/* ------------------------------------------------------ TF 卡上的 BMP 浏览 */

/* 48 色照片专门放这个子目录，和卡上其它东西隔开 */
#define IMAGE_DIR  SDCARD_MOUNT_POINT "/e6"

static sdcard_entry_t s_files[SDCARD_MAX_ENTRIES];
static int s_file_count;
static int s_file_index;
static bmp_rot_t s_rot = BMP_ROT_CW;   /* 竖版 BMP 的旋转方向 */






/* -------------------------------------------------------- 按编号跳转（'j'）
 * 网页要能点列表里的图直接显示，所以固件得支持"输编号 -> 显示第 N 张"。
 */


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

        /* 问设备信息 / 配网：退出校准，交给普通分支处理。
         * 不列在这里的话会被下面的 default 吞掉（校准模式忽略一切未列出的键）。 */
        case 'e':
        case 'W':
            printf("（自动退出校准）\n");
            fflush(stdout);
            cal_exit();
            return false;

        default:
            return true;   /* 校准模式里其它键一律忽略，避免误触 */
    }
}

/* ---------------- 串口配网 ----------------
 *
 * 固件里**不写** WiFi 密码（这仓库要开源）。改成串口配一次、存进 NVS，
 * 掉电不丢、重烧固件也不清（除非擦 flash）。
 *
 * 密码用 * 回显，不打明文 —— 免得留在串口日志或录屏里。
 */

/** 读一行。mask=true 时只回显 *；ESC 取消（返回 -1）；退格可删。 */
static int read_line(char *buf, int cap, bool mask)
{
    int n = 0;
    for (;;) {
        const int c = getchar();
        if (c == EOF) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (c == '\r' || c == '\n') {
            putchar('\n');
            break;
        }
        if (c == 27) {                       /* ESC */
            printf("（已取消）\n");
            buf[0] = '\0';
            fflush(stdout);
            return -1;
        }
        if (c == 8 || c == 127) {            /* 退格 */
            if (n > 0) {
                n--;
                printf("\b \b");
                fflush(stdout);
            }
            continue;
        }
        if (n < cap - 1) {
            buf[n++] = (char)c;
            putchar(mask ? '*' : c);
            fflush(stdout);
        }
    }
    buf[n] = '\0';
    return n;
}

static void wifi_setup_flow(void)
{
    char cur[WIFI_SSID_MAX] = { 0 };
    const bool have = (wifi_store_load(cur, sizeof(cur), NULL, 0) == ESP_OK && cur[0]);

    printf("\n=== WiFi 配网 ===\n");
    printf("  当前    : %s\n", have ? cur : "（还没配过）");
    printf("  说明    : 密码只存进设备 NVS，固件里没有；不回显、不打印\n");
    printf("  取消    : 按 ESC\n");
    printf("  清除    : SSID 处输入一个 S 回车\n");
    printf("\n");

    char ssid[WIFI_SSID_MAX];
    printf("WiFi 名称（SSID）: ");
    fflush(stdout);
    const int n = read_line(ssid, sizeof(ssid), false);
    if (n < 0) {
        return;
    }
    if (n == 0) {
        printf("没输入，放弃。\n");
        return;
    }
    if (n == 1 && (ssid[0] == 'S' || ssid[0] == 's')) {
        wifi_store_clear();
        printf("已清除保存的 WiFi。断电重开就回到「未配网」状态。\n");
        return;
    }

    char pass[WIFI_PASS_MAX];
    printf("密码（输入不回显，输完回车）: ");
    fflush(stdout);
    if (read_line(pass, sizeof(pass), true) < 0) {
        return;
    }

    printf("\n正在保存到 NVS 并连接 %s …\n", ssid);
    fflush(stdout);

    const esp_err_t e = wifi_store_save(ssid, pass);
    if (e != ESP_OK) {
        printf("保存失败：%s\n", esp_err_to_name(e));
        return;
    }
    if (!net_wifi_started()) {
        printf("WiFi 还没启动过（开机时没配过网）。重启设备后生效。\n");
        return;
    }
    if (net_apply_credentials(ssid, pass) != ESP_OK) {
        printf("连接失败，检查一下 SSID / 密码。\n");
        return;
    }

    printf("等待拿 IP");
    fflush(stdout);
    for (int i = 0; i < 40 && !net_ready(); i++) {      /* 最多等 8 秒 */
        vTaskDelay(pdMS_TO_TICKS(200));
        putchar('.');
        fflush(stdout);
    }
    putchar('\n');

    if (net_ready()) {
        printf("\n✓ 已连上，IP = %s\n", net_ip());
        printf("  网页传图地址: http://%s/\n\n", net_ip());
    } else {
        printf("\n✗ 8 秒内没拿到 IP。SSID / 密码对吗？信号够吗？\n\n");
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
           "  k  只写 full_clear 波形不刷新（诊断）\n"
           "  e  设备信息：WiFi 状态 / IP / 传图地址 / 文件数\n"
           "  W  WiFi 配网（串口输 SSID 和密码，存进 NVS；固件里不写密码）\n"
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

        /* 校准模式优先吃掉输入；问设备信息会被放行给下面的普通分支 */
        if (s_cal_mode && cal_input(c)) {
            continue;
        }

        switch (c) {
            case '1': step1_init();        break;
            case '2': step2_full_clear();  break;
            case '3': step3_index_card();  break;
            case 'c':
                cal_enter();
                break;
            case 'k': step_wave_only();    break;
            case 'W':
                wifi_setup_flow();
                break;

            case 'e': {
                /* 问设备要信息 —— 尤其是 IP（网页传图要用） */
                if (!net_wifi_started()) {
                    printf("\n还没配过 WiFi。敲 W 配网（串口输 SSID 和密码）。\n");
                    break;
                }
                if (!net_ready()) {
                    printf("正在连 WiFi…\n");
                    fflush(stdout);
                    net_wait_ready(8000);
                }
                printf("\n--- 设备信息 ---\n");
                printf("  WiFi      : %s\n", net_ready() ? "已连接" : "未连接");
                printf("  设备 IP   : %s\n", net_ip());
                if (net_ready()) {
                    printf("  网页传图  : http://%s/\n", net_ip());
                } else {
                    printf("  网页传图  : 不可用（没连上 WiFi）\n");
                }
                printf("  图片目录  : %s\n", IMAGE_DIR);
                if (s_file_count > 0) {
                    printf("  已扫到    : %d 张\n", s_file_count);
                } else {
                    printf("  已扫到    : 0 张（按 l 扫描）\n");
                }
                const char *msg = device_last_msg();
                printf("  最近操作  : %s\n", (msg && msg[0]) ? msg : "（无）");
                printf("\n");
                fflush(stdout);
                break;
            }
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

/* ============================================ 给 HTTP 服务用的设备接口
 *
 * 面板 SPI 和 TF 卡是**共享资源**，串口命令行和网页服务不能同时动它们。
 * 统一用一把互斥锁串起来（见 device_api.h）。
 */
static SemaphoreHandle_t s_dev_mutex;
static char s_last_msg[96] = "";

bool device_lock(uint32_t timeout_ms)
{
    if (s_dev_mutex == NULL) return true;
    return xSemaphoreTake(s_dev_mutex, pdMS_TO_TICKS(timeout_ms)) == pdTRUE;
}

void device_unlock(void)
{
    if (s_dev_mutex) xSemaphoreGive(s_dev_mutex);
}

void device_set_last_msg(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(s_last_msg, sizeof(s_last_msg), fmt, ap);
    va_end(ap);
}

const char *device_last_msg(void) { return s_last_msg; }
int device_count(void) { return s_file_count; }

/* ---------------- 进度上报 ---------------- */
static volatile int s_progress = -1;
static char s_phase[16] = "idle";

void device_set_phase(const char *phase, int progress)
{
    snprintf(s_phase, sizeof(s_phase), "%s", phase);
    s_progress = progress;
}
const char *device_phase(void) { return s_phase; }
int device_progress(void) { return s_progress; }




esp_err_t device_show_raw(const uint8_t *idx, size_t len)
{
    const size_t want = (size_t)EPD370_WIDTH * EPD370_HEIGHT;
    if (len != want) {
        ESP_LOGE(TAG, "索引数据长度不对：%u，应该是 %u", (unsigned)len, (unsigned)want);
        return ESP_ERR_INVALID_SIZE;
    }
    if (s_index == NULL) return ESP_ERR_NO_MEM;

    memcpy(s_index, idx, want);

    if (!epd370_is_ready() && epd370_init() != ESP_OK) {
        ESP_LOGE(TAG, "初始化失败");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "直接显示 %u 字节索引（ColorQual 3 相）", (unsigned)want);
    const esp_err_t e = display_qual(s_index);
    image_drain_input(200);
    device_set_last_msg("已直接显示 %u 字节", (unsigned)want);
    return e;
}


void app_main(void)
{
    ESP_LOGI(TAG, "3.7\" JD7601 48色校准固件启动");

    /* 设备操作互斥锁 —— 串口命令行和网页服务共用面板与 TF 卡 */
    s_dev_mutex = xSemaphoreCreateMutex();

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

    /* 连 WiFi 并起 HTTP 服务。失败了不影响串口那条路，所以不检查返回值。 */
    if (net_start() != ESP_OK) {
        ESP_LOGW(TAG, "WiFi/HTTP 没起来，只能用串口传图");
    }

    /* 按校准使用习惯：开机不挂卡、不刷屏，直接进校准模式等串口输入。
     * 要看 TF 卡上的图，先按 'x' 退出校准，再按 'l'。
     * 网页那条路不受影响 —— HTTP 请求会自己退出校准（见 cal_input）。 */
    cal_enter();

    cli_loop();
}
