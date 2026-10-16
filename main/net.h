#ifndef NET_H
#define NET_H

#include <stdbool.h>

#include "esp_err.h"

/* 连 WiFi 并在 80 端口起一个 HTTP 服务。
 *
 * 接口（都带 CORS 头，网页可以从 127.0.0.1:5174 直接调）：
 *   GET  /status         设备状态（IP、文件数、忙不忙、最近一次操作）
 *   GET  /list           图片列表 JSON
 *   GET  /show?i=N       切到第 N 张（0 基）
 *   POST /upload?name=x  把 BMP 存到 TF 卡并显示（body = 原始 BMP）
 *
 * 全部失败都不致命：连不上 WiFi 就只打印日志，串口那条路照常能用。 */
esp_err_t net_start(void);

/* 拿到 IP 没有 */
bool net_ready(void);

/* 当前 IP（未连接时返回 "0.0.0.0"） */
const char *net_ip(void);

#endif /* NET_H */
