#ifndef SDCARD_H
#define SDCARD_H

#include <stdbool.h>

#include "esp_err.h"

#define SDCARD_MOUNT_POINT "/sdcard"
#define SDCARD_NAME_MAX    128
#define SDCARD_MAX_ENTRIES 128

typedef struct {
    char name[SDCARD_NAME_MAX];
} sdcard_entry_t;

/* 挂载板载 TF 卡座（SDMMC 4-bit，CLK=6 CMD=4 D0=7 D1=8 D2=1 D3=2）。
 * 重复调用是安全的。 */
esp_err_t sdcard_mount(void);
void      sdcard_unmount(void);
bool      sdcard_is_mounted(void);

/* 扫描挂载点根目录，挑出扩展名匹配的文件（ext 形如 ".bmp"），按名字排序。 */
int sdcard_scan(const char *ext, sdcard_entry_t *out, int max_entries);

/* 扫描指定目录（dir 形如 "/sdcard/e6"）。 */
int sdcard_scan_dir(const char *dir, const char *ext, sdcard_entry_t *out, int max_entries);

/* 确保目录存在（不存在就建）。 */
esp_err_t sdcard_ensure_dir(const char *dir);

#endif /* SDCARD_H */
