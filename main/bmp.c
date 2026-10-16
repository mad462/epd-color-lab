#include "bmp.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "bmp";

static uint16_t rd_u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t rd_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

esp_err_t bmp_read_info(const char *path, bmp_info_t *info)
{
    if (path == NULL || info == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    FILE *fp = fopen(path, "rb");
    if (fp == NULL) {
        ESP_LOGE(TAG, "打不开 %s", path);
        return ESP_ERR_NOT_FOUND;
    }

    uint8_t hdr[54];
    if (fread(hdr, 1, sizeof(hdr), fp) != sizeof(hdr)) {
        ESP_LOGE(TAG, "文件太小，读不到 BMP 头");
        fclose(fp);
        return ESP_ERR_INVALID_SIZE;
    }
    fclose(fp);

    if (hdr[0] != 'B' || hdr[1] != 'M') {
        ESP_LOGE(TAG, "不是 BMP（魔数 %02X %02X）", hdr[0], hdr[1]);
        return ESP_ERR_INVALID_ARG;
    }

    const uint32_t dib_size = rd_u32(&hdr[14]);
    if (dib_size < 40) {
        ESP_LOGE(TAG, "不支持的 DIB 头大小 %u", (unsigned)dib_size);
        return ESP_ERR_NOT_SUPPORTED;
    }

    info->width          = (int32_t)rd_u32(&hdr[18]);
    info->height         = (int32_t)rd_u32(&hdr[22]);
    info->bit_depth      = rd_u16(&hdr[28]);
    const uint32_t comp  = rd_u32(&hdr[30]);
    info->palette_colors = rd_u32(&hdr[46]);
    info->pixel_offset   = rd_u32(&hdr[10]);
    info->row_size       = ((uint32_t)info->width * info->bit_depth + 31) / 32 * 4;

    if (comp != 0) {
        ESP_LOGE(TAG, "压缩方式 %u 不支持（只要 BI_RGB 不压缩）", (unsigned)comp);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (info->bit_depth != 8) {
        ESP_LOGE(TAG, "位深 %u 不支持 —— 导出时请选 48 色色板（会输出 8bit 索引 BMP）",
                 (unsigned)info->bit_depth);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (info->height <= 0) {
        ESP_LOGE(TAG, "高度 %d 不支持（自上而下存储的 BMP）", info->height);
        return ESP_ERR_NOT_SUPPORTED;
    }

    return ESP_OK;
}

/* 把 BMP 的颜色表和给定表逐项比，返回完全匹配的项数（-1 = 读不到）。
 * 用来判断用户导出时选的是 ColorQual 还是 ColorFast 色板。 */
int bmp_count_palette_match(const char *path, const uint8_t (*table)[3], int entries)
{
    bmp_info_t info;
    if (bmp_read_info(path, &info) != ESP_OK) {
        return -1;
    }

    FILE *fp = fopen(path, "rb");
    if (fp == NULL) {
        return -1;
    }

    const int n = (int)((info.palette_colors < (uint32_t)entries)
                        ? info.palette_colors : (uint32_t)entries);
    int matched = 0;
    for (int i = 0; i < n; i++) {
        uint8_t entry[4];   /* BMP 颜色表每项 4 字节：B G R 0 */
        if (fseek(fp, 54 + i * 4, SEEK_SET) != 0 ||
            fread(entry, 1, 4, fp) != 4) {
            break;
        }
        if (entry[2] == table[i][0] && entry[1] == table[i][1] && entry[0] == table[i][2]) {
            matched++;
        }
    }
    fclose(fp);
    return matched;
}

/* 把像素按 BMP 规则（自下而上）读进 dst，dst 是 info->width * info->height 字节 */
static esp_err_t read_pixels(FILE *fp, const bmp_info_t *info, uint8_t *dst)
{
    uint8_t *rowbuf = malloc(info->row_size);
    if (rowbuf == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t ret = ESP_OK;
    for (int y = 0; y < info->height; y++) {
        /* BMP 默认自下而上：文件里的第一行是图像最后一行 */
        const int file_row = info->height - 1 - y;
        const long off = (long)info->pixel_offset + (long)file_row * (long)info->row_size;

        if (fseek(fp, off, SEEK_SET) != 0 ||
            fread(rowbuf, 1, info->row_size, fp) != info->row_size) {
            ESP_LOGE(TAG, "读第 %d 行失败（偏移 %ld）", file_row, off);
            ret = ESP_ERR_INVALID_SIZE;
            break;
        }
        memcpy(dst + (size_t)y * info->width, rowbuf, (size_t)info->width);
    }

    free(rowbuf);
    return ret;
}

esp_err_t bmp_load_index8(const char *path, uint8_t *out,
                          int expect_w, int expect_h, bmp_rot_t rot)
{
    bmp_info_t info;
    esp_err_t err = bmp_read_info(path, &info);
    if (err != ESP_OK) {
        return err;
    }

    ESP_LOGI(TAG, "%s: %d x %d，%u bpp，颜色表 %u 项，行 %u 字节",
             path, info.width, info.height, (unsigned)info.bit_depth,
             (unsigned)info.palette_colors, (unsigned)info.row_size);

    const bool direct  = (info.width == expect_w && info.height == expect_h);
    const bool rotated = (info.width == expect_h && info.height == expect_w);

    if (!direct && !rotated) {
        ESP_LOGE(TAG, "尺寸不对：BMP 是 %d x %d，屏要 %d x %d（或转 90° 的 %d x %d）",
                 info.width, info.height, expect_w, expect_h, expect_h, expect_w);
        return ESP_ERR_INVALID_SIZE;
    }

    /* 颜色表项数是个很好的"用错色板"信号：48 色 .act 是 64 项 */
    if (info.palette_colors != 64) {
        ESP_LOGW(TAG, "颜色表是 %u 项（我们的 48 色 .act 是 64 项）。"
                      "如果用了别的色板，索引含义会不一样，颜色可能不对。",
                 (unsigned)info.palette_colors);
    }

    FILE *fp = fopen(path, "rb");
    if (fp == NULL) {
        return ESP_ERR_NOT_FOUND;
    }

    if (direct) {
        err = read_pixels(fp, &info, out);
        fclose(fp);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "读出 %d x %d 索引（直接，无旋转）", info.width, info.height);
        }
        return err;
    }

    /* 需要转 90°：先读进临时缓冲，再旋转到 out */
    ESP_LOGI(TAG, "BMP 是竖的 %d x %d，按 %s 转 90° 到 %d x %d",
             info.width, info.height, (rot == BMP_ROT_CCW) ? "逆时针" : "顺时针",
             expect_w, expect_h);

    const size_t tmp_len = (size_t)info.width * info.height;
    uint8_t *tmp = heap_caps_malloc(tmp_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (tmp == NULL) {
        tmp = malloc(tmp_len);
    }
    if (tmp == NULL) {
        fclose(fp);
        return ESP_ERR_NO_MEM;
    }

    err = read_pixels(fp, &info, tmp);
    fclose(fp);

    if (err == ESP_OK) {
        for (int y = 0; y < expect_h; y++) {
            uint8_t *dstrow = out + (size_t)y * expect_w;
            for (int x = 0; x < expect_w; x++) {
                dstrow[x] = (rot == BMP_ROT_CCW)
                    /* 逆时针：src(srcW-1-y, x) */
                    ? tmp[(size_t)x * info.width + (info.width - 1 - y)]
                    /* 顺时针：src(y, srcH-1-x) */
                    : tmp[(size_t)(info.height - 1 - x) * info.width + y];
            }
        }
        ESP_LOGI(TAG, "旋转完成 -> %d x %d", expect_w, expect_h);
    }

    free(tmp);
    return err;
}
