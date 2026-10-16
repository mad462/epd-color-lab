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
#include "freertos/task.h"

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "device_api.h"
#include "net.h"
#include "wifi_config.h"

static const char *TAG = "net";

static char s_ip[20] = "0.0.0.0";
static bool s_ready = false;
static httpd_handle_t s_srv = NULL;

#define UPLOAD_MAX (2 * 1024 * 1024)

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

/* 从 query 里取一个参数并做 URL 解码（文件名里可能有空格等） */
static void url_decode(char *s)
{
    char *o = s;
    for (char *p = s; *p; p++) {
        if (*p == '%' && p[1] && p[2]) {
            const int hi = p[1] >= 'a' ? p[1] - 'a' + 10 : (p[1] >= 'A' ? p[1] - 'A' + 10 : p[1] - '0');
            const int lo = p[2] >= 'a' ? p[2] - 'a' + 10 : (p[2] >= 'A' ? p[2] - 'A' + 10 : p[2] - '0');
            *o++ = (char)((hi << 4) | lo);
            p += 2;
        } else if (*p == '+') {
            *o++ = ' ';
        } else {
            *o++ = *p;
        }
    }
    *o = '\0';
}

static bool query_get(httpd_req_t *req, const char *key, char *out, size_t out_len)
{
    char q[256];
    if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) {
        return false;
    }
    if (httpd_query_key_value(q, key, out, out_len) != ESP_OK) {
        return false;
    }
    url_decode(out);
    return true;
}

/* ---------------------------------------------------------------- /status */
static esp_err_t h_status(httpd_req_t *req)
{
    char buf[320];
    snprintf(buf, sizeof(buf),
             "{\"ok\":true,\"ip\":\"%s\",\"ready\":%s,\"files\":%d,\"last\":\"%s\"}",
             s_ip, s_ready ? "true" : "false", device_count(),
             device_last_msg() ? device_last_msg() : "");
    return send_json(req, buf);
}

/* ---------------------------------------------------------------- /list */
static esp_err_t h_list(httpd_req_t *req)
{
    if (!device_lock(5000)) {
        return send_json(req, "{\"ok\":false,\"error\":\"设备忙\"}");
    }

    device_file_t *files = heap_caps_malloc(sizeof(device_file_t) * DEVICE_MAX_FILES,
                                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!files) {
        device_unlock();
        return send_json(req, "{\"ok\":false,\"error\":\"内存不足\"}");
    }
    const int n = device_scan(files, DEVICE_MAX_FILES);

    /* 每个文件最多 ~200 字节 JSON，128 个够用 */
    const size_t cap = 1024 + (size_t)n * 200;
    char *out = heap_caps_malloc(cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!out) {
        free(files);
        device_unlock();
        return send_json(req, "{\"ok\":false,\"error\":\"内存不足\"}");
    }

    size_t used = 0;
    used += snprintf(out + used, cap - used, "{\"ok\":true,\"files\":[");
    for (int i = 0; i < n && used + 220 < cap; i++) {
        char info[96];
        if (files[i].is_bmp) {
            snprintf(info, sizeof(info), "%dx%d  %dbpp  %" PRIu32 "色",
                     files[i].w, files[i].h, files[i].bpp, files[i].colors);
        } else {
            snprintf(info, sizeof(info), "不是可用的 BMP");
        }
        used += snprintf(out + used, cap - used,
                         "%s{\"index\":%d,\"name\":\"%s\",\"size\":%ld,\"info\":\"%s\"}",
                         i ? "," : "", i, files[i].name, files[i].size, info);
    }
    used += snprintf(out + used, cap - used, "]}");

    free(files);
    device_unlock();
    device_set_last_msg("读取列表 %d 张", n);
    return send_json(req, out ? out : "{}");   /* out 在 send 之后才 free */
}

/* ---------------------------------------------------------------- /show */
static esp_err_t h_show(httpd_req_t *req)
{
    char val[16];
    if (!query_get(req, "i", val, sizeof(val))) {
        return send_json(req, "{\"ok\":false,\"error\":\"缺少 i 参数\"}");
    }
    const int idx = atoi(val);

    if (!device_lock(60000)) {
        return send_json(req, "{\"ok\":false,\"error\":\"设备忙\"}");
    }
    const esp_err_t e = device_show(idx);
    device_unlock();

    if (e != ESP_OK) {
        return send_json(req, "{\"ok\":false,\"error\":\"编号越界或读图失败\"}");
    }
    return send_json(req, "{\"ok\":true}");
}

/* ---------------------------------------------------------------- /upload */
static esp_err_t h_upload(httpd_req_t *req)
{
    char name[DEVICE_NAME_MAX];
    if (!query_get(req, "name", name, sizeof(name)) || name[0] == '\0') {
        return send_json(req, "{\"ok\":false,\"error\":\"缺少 name 参数\"}");
    }

    const int total = req->content_len;
    if (total <= 0 || total > UPLOAD_MAX) {
        char b[128];
        snprintf(b, sizeof(b), "{\"ok\":false,\"error\":\"大小不对: %d\"}", total);
        return send_json(req, b);
    }

    ESP_LOGI(TAG, "收到上传请求：%s  %d 字节", name, total);
    device_set_last_msg("接收 %s …", name);

    uint8_t *buf = heap_caps_malloc(total, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) {
        return send_json(req, "{\"ok\":false,\"error\":\"分配缓冲失败\"}");
    }

    int got = 0;
    while (got < total) {
        const int r = httpd_req_recv(req, (char *)buf + got, total - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;                    /* 超时就接着收 */
        }
        if (r <= 0) {
            free(buf);
            device_set_last_msg("接收中断（%d/%d）", got, total);
            return send_json(req, "{\"ok\":false,\"error\":\"接收中断\"}");
        }
        got += r;
    }
    ESP_LOGI(TAG, "收完 %d 字节，开始落盘", got);

    if (!device_lock(60000)) {
        free(buf);
        return send_json(req, "{\"ok\":false,\"error\":\"设备忙\"}");
    }
    const esp_err_t e = device_save_and_show(name, buf, (size_t)got);
    device_unlock();
    free(buf);

    if (e != ESP_OK) {
        device_set_last_msg("保存/显示失败: %s", esp_err_to_name(e));
        return send_json(req, "{\"ok\":false,\"error\":\"保存或显示失败\"}");
    }
    device_set_last_msg("已显示 %s", name);
    return send_json(req, "{\"ok\":true}");
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
    cfg.max_uri_handlers = 8;
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
        { .uri = "/status",   .method = HTTP_GET,  .handler = h_status },
        { .uri = "/list",     .method = HTTP_GET,  .handler = h_list },
        { .uri = "/show",     .method = HTTP_GET,  .handler = h_show },
        { .uri = "/upload",   .method = HTTP_POST, .handler = h_upload },
        { .uri = "/upload",   .method = HTTP_OPTIONS, .handler = h_options },
        { .uri = "/list",     .method = HTTP_OPTIONS, .handler = h_options },
        { .uri = "/status",   .method = HTTP_OPTIONS, .handler = h_options },
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

    /* HTTP 服务不用等 IP —— 起来了就能接（虽然没 IP 时访问不到） */
    return start_http();
}

bool net_ready(void) { return s_ready; }
const char *net_ip(void) { return s_ip; }
