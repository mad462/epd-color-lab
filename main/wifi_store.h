/* WiFi 凭据持久化（NVS）
 *
 * 为什么不用编译进去的 wifi_config.h：
 *   密码写进源码就必然进 git，这个仓库要开源 —— 那等于把家里 WiFi 密码
 *   发给全世界。所以改成**串口配一次，存进 NVS**，固件里一个字符都没有。
 *
 * NVS 是 ESP32 的键值存储，掉电不丢，重烧固件也不清（除非擦 flash）。
 * 命名空间 "wifi"，键 "ssid" / "pass"。
 */
#ifndef WIFI_STORE_H
#define WIFI_STORE_H

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

#define WIFI_SSID_MAX 33     /* 32 + '\0' */
#define WIFI_PASS_MAX 65     /* 64 + '\0' */

/* 读已保存的凭据。任一参数可传 NULL 表示不关心。
 * 返回 ESP_OK 表示读到了完整的 SSID（密码可以为空 = 开放网络）。 */
esp_err_t wifi_store_load(char *ssid, size_t ssid_cap,
                          char *pass, size_t pass_cap);

/* 有保存过吗 */
bool wifi_store_has(void);

/* 写入并立即提交 */
esp_err_t wifi_store_save(const char *ssid, const char *pass);

/* 抹掉 */
esp_err_t wifi_store_clear(void);

#endif
