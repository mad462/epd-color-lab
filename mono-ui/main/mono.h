/* 单色 UI 层 —— 黑白差分快刷（MonoFast）
 *
 * 做法来自 FrameFilm 的 hal_epd_370_ark.c。核心不是"传位图"，是传**跳变码**：
 *
 *   每个像素 2 bit： code = 2 * 上一帧 + 这一帧
 *   面板只驱动状态变了的像素 —— 没变的不动 —— 所以快、而且不闪。
 *
 * 线路格式：720 像素 × 4 bit = 360 字节/行（每个像素一个 4bit 半字节，
 * 2bit 码放低 2 位，高 2 位不用）。整帧 360 × 480。
 *
 * 波形用 waveforms.c 里的 wf_mono_fast / wf_mono_clear —— 和 FrameFilm 的
 * mono_fast / mono_clear 逐字节相同。
 *
 * 编码约定（FrameFilm 实测得出）：
 *   码位 1 = 白、0 = 黑       （注意和位图相反：位图 1 = 黑）
 *   清屏后面板实际是黑        -> 上一帧初值全 0
 *   位图字节内 MSB 在前       -> 由我们按像素取位，不用额外反序
 */
#ifndef MONO_H
#define MONO_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define MONO_W        720
#define MONO_H        480
#define MONO_STRIDE   (MONO_W / 8)     /* 90 字节/行 —— 帧缓冲（1bpp） */
#define MONO_FB_BYTES (MONO_STRIDE * MONO_H)   /* 43200 */

/* 送到面板的一行：720 像素 × 4bit = 360 字节。
 * ⚠️ 别写成 MONO_STRIDE/2（那是 45）—— 会把缓冲写爆 8 倍。 */
#define MONO_LINE_BYTES (MONO_W * 4 / 8)       /* 360 */

/* ---------------- 帧缓冲 ---------------- */
void     mono_clear(bool black);                 /* 整屏填 */
uint8_t *mono_fb(void);                          /* 直接拿缓冲（43200 字节） */

/* ---------------- 绘制原语（坐标自动裁剪）---------------- */
void mono_pixel(int x, int y, bool black);
void mono_hline(int x, int y, int w, bool black);
void mono_vline(int x, int y, int h, bool black);
void mono_fill(int x, int y, int w, int h, bool black);   /* 实心矩形 */
void mono_rect(int x, int y, int w, int h, bool black);   /* 空心矩形 */
void mono_thick_rect(int x, int y, int w, int h, int t, bool black);

/* 七段数码管风格的大数字（不用字库，缩放自由，很适合计数器/时钟） */
void mono_digit_seg(int x, int y, int w, int h, int digit, int seg_thick, bool black);

/* 5x7 点阵 ASCII（可选，做小字标签用） */
void mono_text5x7(int x, int y, int scale, const char *s, bool black);

/* ---------------- 输出到面板 ---------------- */
/* 把当前帧缓冲按差分推上去并刷新。内部维护"上一帧"。 */
esp_err_t mono_push(void);

/* 会话模式：连续刷多帧时保持面板上电，省掉每帧的 PON/POF。
 * 用 SessionBegin / 若干次 push / SessionEnd 包起来。 */
void mono_session_begin(void);
void mono_session_end(void);

/* 模式切换：当前不在 mono 态时，先做一次全清把面板推到已知起点。
 * 首次调用前必须已经 epd370_init()。 */
esp_err_t mono_enter(void);

#endif /* MONO_H */
