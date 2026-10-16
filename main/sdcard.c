/*
 * 板载 TF 卡座 —— ESP32-S3 的 SDMMC 外设，4-bit 总线。
 * 引脚见 pin_map.h（取自 ESP32-S3-Demo-Schematic-V1.3）。
 * 和 e6-730 / jd7601 里那份是同一套已验证代码。
 */

#include "sdcard.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "driver/sdmmc_host.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"

#include "pin_map.h"

static const char *TAG = "sdcard";

static sdmmc_card_t *s_card;

static bool has_ext(const char *name, const char *ext)
{
    const size_t n = strlen(name);
    const size_t e = strlen(ext);

    if (n <= e) {
        return false;
    }
    return strcasecmp(name + n - e, ext) == 0;
}

static int cmp_entry(const void *a, const void *b)
{
    return strcasecmp(((const sdcard_entry_t *)a)->name,
                      ((const sdcard_entry_t *)b)->name);
}

bool sdcard_is_mounted(void)
{
    return s_card != NULL;
}

esp_err_t sdcard_mount(void)
{
    if (s_card != NULL) {
        return ESP_OK;
    }

    esp_vfs_fat_sdmmc_mount_config_t mount_cfg = {
        .format_if_mount_failed = false,
        .max_files = 5,
        .allocation_unit_size = 16 * 1024,
    };

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_DEFAULT;   /* 20 MHz */

    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.clk = tf_clk;
    slot.cmd = tf_cmd;
    slot.d0 = tf_d0;
    slot.d1 = tf_d1;
    slot.d2 = tf_d2;
    slot.d3 = tf_d3;
    slot.width = 4;
    slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;

    ESP_LOGI(TAG, "CLK=%d CMD=%d D0=%d D1=%d D2=%d D3=%d",
             (int)tf_clk, (int)tf_cmd, (int)tf_d0,
             (int)tf_d1, (int)tf_d2, (int)tf_d3);

    const esp_err_t err = esp_vfs_fat_sdmmc_mount(SDCARD_MOUNT_POINT, &host, &slot,
                                                  &mount_cfg, &s_card);
    if (err != ESP_OK) {
        s_card = NULL;
        ESP_LOGE(TAG, "挂载失败: %s（卡插了吗？）", esp_err_to_name(err));
        return err;
    }

    sdmmc_card_print_info(stdout, s_card);
    ESP_LOGI(TAG, "TF 卡已挂载到 %s", SDCARD_MOUNT_POINT);
    return ESP_OK;
}

void sdcard_unmount(void)
{
    if (s_card == NULL) {
        return;
    }
    esp_vfs_fat_sdcard_unmount(SDCARD_MOUNT_POINT, s_card);
    s_card = NULL;
}

int sdcard_scan_dir(const char *dir, const char *ext, sdcard_entry_t *out, int max_entries)
{
    if (s_card == NULL || out == NULL || max_entries <= 0 || dir == NULL) {
        return 0;
    }

    DIR *dp = opendir(dir);
    if (dp == NULL) {
        ESP_LOGE(TAG, "opendir(%s) 失败", dir);
        return 0;
    }

    int n = 0;
    struct dirent *de;
    while ((de = readdir(dp)) != NULL && n < max_entries) {
        if (de->d_name[0] == '.') {
            continue;
        }
        if (!has_ext(de->d_name, ext)) {
            continue;
        }
        strlcpy(out[n].name, de->d_name, SDCARD_NAME_MAX);
        n++;
    }
    closedir(dp);

    qsort(out, (size_t)n, sizeof(out[0]), cmp_entry);

    ESP_LOGI(TAG, "%s 里找到 %d 个 '%s' 文件", dir, n, ext);
    return n;
}

int sdcard_scan(const char *ext, sdcard_entry_t *out, int max_entries)
{
    return sdcard_scan_dir(SDCARD_MOUNT_POINT, ext, out, max_entries);
}

esp_err_t sdcard_ensure_dir(const char *dir)
{
    if (s_card == NULL || dir == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    struct stat st;
    if (stat(dir, &st) == 0) {
        return S_ISDIR(st.st_mode) ? ESP_OK : ESP_ERR_INVALID_STATE;
    }

    if (mkdir(dir, 0777) == 0) {
        ESP_LOGI(TAG, "建了目录 %s", dir);
        return ESP_OK;
    }

    ESP_LOGE(TAG, "建目录 %s 失败（errno %d）", dir, errno);
    return ESP_FAIL;
}
