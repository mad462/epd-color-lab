#include "wifi_store.h"

#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "wifi_store";
static const char *NS = "wifi";

esp_err_t wifi_store_load(char *ssid, size_t ssid_cap,
                          char *pass, size_t pass_cap)
{
    nvs_handle_t h;
    esp_err_t e = nvs_open(NS, NVS_READONLY, &h);
    if (e != ESP_OK) {
        return e;                      /* 多半是还没配过 */
    }

    if (ssid && ssid_cap) {
        size_t n = ssid_cap;
        e = nvs_get_str(h, "ssid", ssid, &n);
        if (e != ESP_OK) {
            nvs_close(h);
            return e;
        }
    }
    if (pass && pass_cap) {
        size_t n = pass_cap;
        if (nvs_get_str(h, "pass", pass, &n) != ESP_OK) {
            pass[0] = '\0';            /* 没存密码 = 开放网络 */
        }
    }
    nvs_close(h);
    return ESP_OK;
}

bool wifi_store_has(void)
{
    char ssid[WIFI_SSID_MAX];
    return wifi_store_load(ssid, sizeof(ssid), NULL, 0) == ESP_OK && ssid[0] != '\0';
}

esp_err_t wifi_store_save(const char *ssid, const char *pass)
{
    nvs_handle_t h;
    esp_err_t e = nvs_open(NS, NVS_READWRITE, &h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "打开 NVS 失败: %s", esp_err_to_name(e));
        return e;
    }
    e = nvs_set_str(h, "ssid", ssid);
    if (e == ESP_OK) {
        e = nvs_set_str(h, "pass", pass ? pass : "");
    }
    if (e == ESP_OK) {
        e = nvs_commit(h);
    }
    nvs_close(h);

    if (e == ESP_OK) {
        /* 只打 SSID 和密码长度，**绝不**打密码本身 */
        ESP_LOGI(TAG, "已保存 WiFi：%s（密码 %u 字符）", ssid,
                 (unsigned)(pass ? strlen(pass) : 0));
    }
    return e;
}

esp_err_t wifi_store_clear(void)
{
    nvs_handle_t h;
    esp_err_t e = nvs_open(NS, NVS_READWRITE, &h);
    if (e != ESP_OK) {
        return e;
    }
    nvs_erase_all(h);
    e = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "已清除保存的 WiFi");
    return e;
}
