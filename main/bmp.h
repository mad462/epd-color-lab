#ifndef BMP_H
#define BMP_H

#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>

/* ------------------------------------------------------------------------
 * 读 QuickShakePic 导出的 8bit 索引 BMP。
 *
 * 为什么能直接用：那个网站（配 48-color .act）导出的 BMP 里，
 * **像素字节就是面板的波形索引**（0~63）—— 因为 .act 把 64 个颜色
 * 放在和波形索引完全一致的位置上。
 *
 * 支持：BI_RGB(不压缩) / 8bpp / 行 4 字节对齐 / 正高度（自下而上存储）。
 * 不支持：RLE 压缩、自上而下(负高度)、其它位深。
 * ---------------------------------------------------------------------- */

typedef struct {
    int      width;
    int      height;
    uint16_t bit_depth;
    uint32_t palette_colors;
    uint32_t pixel_offset;
    uint32_t row_size;
} bmp_info_t;

/* BMP 是 480x720（竖）而屏要 720x480（横）时的旋转方向。
 * 网站里如果用了竖屏预设导出，就靠这个转过来。 */
typedef enum {
    BMP_ROT_CW = 0,   /* 顺时针 90°（默认） */
    BMP_ROT_CCW,      /* 逆时针 90° */
} bmp_rot_t;

/* 只读文件头，不读像素。 */
esp_err_t bmp_read_info(const char *path, bmp_info_t *info);

/* 读像素到 out（out 必须是 expect_w*expect_h 字节，每像素一个索引）。
 * 尺寸正好是 expect_w x expect_h 就直接读；
 * 是 expect_h x expect_w（转 90°）就按 rot 方向转过来。
 * 其它尺寸报错。 */
esp_err_t bmp_load_index8(const char *path, uint8_t *out,
                          int expect_w, int expect_h, bmp_rot_t rot);

/* 把 BMP 颜色表和给定表逐项比，返回完全匹配的项数（-1 = 读不到）。
 * 用来判断导出时用的是 ColorQual 还是 ColorFast 的 .act。 */
int bmp_count_palette_match(const char *path, const uint8_t (*table)[3], int entries);

#endif /* BMP_H */
