/* WiFi + HTTP 服务 —— 让网页直接通过局域网传图，绕开 115200 的串口。
 *
 * 动机：串口 115200 下 345 KB 要传 30 秒。局域网随便就是几 Mbps，
 * 同样的图不到 0.5 秒。真正慢的是面板刷新的 15 秒，那个绕不开。
 *
 * 所有设备操作都走 device_api.h，并且拿锁 —— 面板 SPI 和 TF 卡是共享的。 */

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "device_api.h"
#include "epd370.h"
#include "net.h"
#include "wifi_config.h"

static const char *TAG = "net";

static char s_ip[20] = "0.0.0.0";
static bool s_ready = false;
static httpd_handle_t s_srv = NULL;

/* ---------------------------------------------------------------- 事件 */
static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_ready = false;
        snprintf(s_ip, sizeof(s_ip), "0.0.0.0");
        ESP_LOGW(TAG, "WiFi 断了，重连中…");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *e = (const ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&e->ip_info.ip));
        s_ready = true;
        ESP_LOGI(TAG, "拿到 IP: %s", s_ip);
        /* 这条很重要：用户得知道去哪个地址打开网页 */
        printf("\n");
        printf("========================================\n");
        printf("  网页传图地址:  http://%s/\n", s_ip);
        printf("  （串口照常可用，两条路都通）\n");
        printf("========================================\n\n");
        fflush(stdout);
    }
}

/* ---------------------------------------------------------------- 小工具 */
static void cors(httpd_req_t *req)
{
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Headers", "Content-Type");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Methods", "GET,POST,OPTIONS");
}

static esp_err_t send_json(httpd_req_t *req, const char *json)
{
    cors(req);
    httpd_resp_set_type(req, "application/json; charset=utf-8");
    return httpd_resp_sendstr(req, json);
}

/* ---------------------------------------------------------------- 显示任务
 *
 * 为什么要有这个任务：刷屏要 15 秒，如果在 httpd 任务里**同步**跑，
 * 那 15 秒里网页连 /status 都问不到 —— 进度就没法做。
 * 所以 /raw 收完数据丢进队列立刻返回，实际显示交给这个任务，
 * 网页轮询 /status 里的 phase/progress。 */
typedef struct { uint8_t *data; size_t len; } raw_job_t;
static QueueHandle_t s_raw_q = NULL;

/* /raw 收的是 8bpp 索引 —— 一像素一字节，720*480 = 345600。
 * 别用 EPD370_FRAME_BYTES：那是 4bpp 打包后的 172800，传错会直接被拒。 */
#define RAW_INDEX_BYTES (720 * 480)

static void display_task(void *arg)
{
    (void)arg;
    raw_job_t job;
    for (;;) {
        if (xQueueReceive(s_raw_q, &job, portMAX_DELAY) != pdTRUE) continue;

        ESP_LOGI(TAG, "开始显示 %u 字节索引", (unsigned)job.len);
        if (device_lock(60000)) {
            device_show_raw(job.data, job.len);
            device_unlock();
        }
        free(job.data);
        device_set_phase("idle", -1);
        ESP_LOGI(TAG, "显示结束");
    }
}

/* JSON 里的字符串要转义，last_msg 可能带引号 */
static void json_escape(char *dst, size_t cap, const char *src)
{
    size_t j = 0;
    for (const char *p = src; *p && j + 1 < cap; p++) {
        if (*p == '"' || *p == '\\') { if (j + 2 >= cap) break; dst[j++] = '\\'; }
        dst[j++] = *p;
    }
    dst[j] = '\0';
}

/* ---------------------------------------------------------------- /status */
static esp_err_t h_status(httpd_req_t *req)
{
    char last[128];
    json_escape(last, sizeof(last), device_last_msg() ? device_last_msg() : "");

    char buf[400];
    snprintf(buf, sizeof(buf),
             "{\"ok\":true,\"ip\":\"%s\",\"ready\":%s,\"files\":%d,"
             "\"phase\":\"%s\",\"progress\":%d,\"last\":\"%s\"}",
             s_ip, s_ready ? "true" : "false", device_count(),
             device_phase(), device_progress(), last);
    return send_json(req, buf);
}

/* ---------------------------------------------------------------- /raw
 * 收一份 8bpp 索引，丢给显示任务，**立刻返回**。
 * 网页随后轮询 /status 看 phase/progress。 */
static esp_err_t h_raw(httpd_req_t *req)
{
    const int total = req->content_len;
    if (total != RAW_INDEX_BYTES) {
        char b[128];
        snprintf(b, sizeof(b),
                 "{\"ok\":false,\"error\":\"长度不对: %d，应该是 345600\"}", total);
        return send_json(req, b);
    }
    if (device_progress() >= 0) {
        return send_json(req, "{\"ok\":false,\"error\":\"设备正忙，等这次刷完\"}");
    }

    ESP_LOGI(TAG, "收到原始索引 %d 字节", total);
    device_set_last_msg("接收索引 %d 字节", total);
    device_set_phase("recv", 2);

    uint8_t *buf = heap_caps_malloc(total, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) {
        device_set_phase("idle", -1);
        return send_json(req, "{\"ok\":false,\"error\":\"分配缓冲失败\"}");
    }

    int got = 0;
    while (got < total) {
        const int r = httpd_req_recv(req, (char *)buf + got, total - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (r <= 0) {
            free(buf);
            device_set_phase("idle", -1);
            device_set_last_msg("接收中断（%d/%d）", got, total);
            return send_json(req, "{\"ok\":false,\"error\":\"接收中断\"}");
        }
        got += r;
    }
    ESP_LOGI(TAG, "收完 %d 字节，交给显示任务", got);

    raw_job_t job = { .data = buf, .len = (size_t)got };
    if (xQueueSend(s_raw_q, &job, 0) != pdTRUE) {
        free(buf);
        device_set_phase("idle", -1);
        return send_json(req, "{\"ok\":false,\"error\":\"显示任务忙\"}");
    }
    /* 立刻返回 —— 显示进度让网页去轮询 /status */
    return send_json(req, "{\"ok\":true,\"queued\":true}");
}

/* ---------------------------------------------------------------- OPTIONS */
static esp_err_t h_options(httpd_req_t *req)
{
    cors(req);
    httpd_resp_set_status(req, "204 No Content");
    return httpd_resp_send(req, NULL, 0);
}

/* ---------------------------------------------------------------- 启动 */
static esp_err_t start_http(void)
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 80;
    cfg.max_uri_handlers = 10;
    cfg.lru_purge_enable = true;
    cfg.stack_size = 8192;
    /* 默认 5 秒。传 345 KB 时中间可能卡顿，给足余量。 */
    cfg.recv_wait_timeout = 30;
    cfg.send_wait_timeout = 30;

    esp_err_t e = httpd_start(&s_srv, &cfg);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start 失败: %s", esp_err_to_name(e));
        return e;
    }

    const httpd_uri_t uris[] = {
        { .uri = "/status", .method = HTTP_GET,     .handler = h_status },
        { .uri = "/raw",    .method = HTTP_POST,    .handler = h_raw },
        { .uri = "/status", .method = HTTP_OPTIONS, .handler = h_options },
        { .uri = "/raw",    .method = HTTP_OPTIONS, .handler = h_options },
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        httpd_register_uri_handler(s_srv, &uris[i]);
    }
    ESP_LOGI(TAG, "HTTP 服务已启动（80 端口）");
    return ESP_OK;
}

esp_err_t net_start(void)
{
    /* NVS —— WiFi 驱动要用它存校准数据。可能已经被别处初始化过。 */
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        e = nvs_flash_init();
    }
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init 失败: %s", esp_err_to_name(e));
        return e;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&ic));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &on_wifi, NULL, NULL));

    wifi_config_t wc = { 0 };
    snprintf((char *)wc.sta.ssid, sizeof(wc.sta.ssid), "%s", WIFI_SSID);
    snprintf((char *)wc.sta.password, sizeof(wc.sta.password), "%s", WIFI_PASSWORD);
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));   /* 省电模式会拖慢传输 */
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "正在连 %s …", WIFI_SSID);

    /* 显示任务 —— /raw 收完数据由它去刷屏，httpd 任务不阻塞 */
    s_raw_q = xQueueCreate(2, sizeof(raw_job_t));
    if (s_raw_q == NULL) {
        ESP_LOGE(TAG, "显示队列创建失败");
        return ESP_FAIL;
    }
    if (xTaskCreate(display_task, "display", 6144, NULL, 5, NULL) != pdPASS) {
        ESP_LOGE(TAG, "显示任务创建失败");
        return ESP_FAIL;
    }

    /* HTTP 服务不用等 IP —— 起来了就能接（虽然没 IP 时访问不到） */
    return start_http();
}

bool net_ready(void) { return s_ready; }
const char *net_ip(void) { return s_ip; }

bool net_wait_ready(uint32_t timeout_ms)
{
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    while (!s_ready && xTaskGetTickCount() < deadline) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    return s_ready;
}
