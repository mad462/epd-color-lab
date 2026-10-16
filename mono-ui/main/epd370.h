#ifndef EPD370_H
#define EPD370_H

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ------------------------------------------------------------------------
 * 3.7" JD7601 / GDEH037E01 类六色 EPD，720 x 480
 *
 * 协议照抄 FrameFilm（frame_film_ark/components/film_hal/src/hal_epd_370.c）：
 *   复位：双脉冲 RST=0 20ms -> RST=1 10ms -> RST=0 20ms -> RST=1 10ms
 *   初始化：0xE9 0x01 然后 0x30 0x04 / 0x41 0x00 / 0xE0 0x02 / 0xE6 0x19
 *   写波形：0x00 + 0x0F 0xA9 然后 0x20 + 799 字节
 *   刷新：  [0x04 PON] -> 0x12 0x00 -> [0x02 POF]
 *   wait_cycle = 先等 BUSY 拉低（进入忙），再等拉高（释放）
 *
 * BUSY：低 = 忙，高 = 空闲。
 * 一帧 172800 字节 = 360 字节/行 x 480 行（4bpp，行优先）。
 * ---------------------------------------------------------------------- */

#define EPD370_WIDTH        720
#define EPD370_HEIGHT       480
#define EPD370_BPL          (EPD370_WIDTH / 2)             /* 360 */
#define EPD370_FRAME_BYTES  (EPD370_BPL * EPD370_HEIGHT)   /* 172800 */

/* 复位（双脉冲） */
void epd370_hw_reset(void);

/* 复位 + 五条初始化寄存器。每一步都会打印 BUSY 电平。 */
esp_err_t epd370_init(void);

/* 裸发指令 + 参数（CS 全程拉低） */
esp_err_t epd370_cmd(uint8_t cmd, const uint8_t *data, size_t len);

/* 写波形表：0x00 + 0x0F 0xA9，然后 0x20 + len 字节 */
esp_err_t epd370_wave(const uint8_t *lut, size_t len);

/* 推一整帧的填充值（0x10 + 172800 字节）。不刷新。 */
esp_err_t epd370_fill(uint8_t value);

/* ---- DTM1 流式写入（mono 差分刷新用，360 字节/行）----
 * begin 发 0x10，然后每行调一次 row，最后 end 抬 CS。
 * 比先拼一整帧 172800 字节省一块缓冲。 */
esp_err_t epd370_dtm1_begin(void);
esp_err_t epd370_dtm1_row(const uint8_t *row, size_t bytes);
esp_err_t epd370_dtm1_end(void);

/* 一次发多行（<= EPD370_CHUNK）。data 必须是**内部 RAM**（DMA 读不了 PSRAM）。
 * 逐行调 dtm1_row 的话，480 次事务的开销本身就要 ~100 ms —— 合并成 24 次。 */
esp_err_t epd370_dtm1_bulk(const uint8_t *data, size_t bytes);

/* 把 8bpp 索引缓冲的一个 3bit 平面编码成 4bpp 推下去。
 * plane 0 = index>>3，plane 1 = index&7。长度必须是 720*480。 */
esp_err_t epd370_write_index_plane(const uint8_t *index8, int plane);

/* 触发刷新。power_on -> 先 PON；power_off -> 最后 POF。 */
esp_err_t epd370_refresh(bool power_on, bool power_off);

/* 先等 BUSY 拉低（进入忙），再等拉高（释放）。
 * 返回拉低耗时 ms；*asserted 表示有没有真的看到"进入忙"。超时返回 -1。 */
int32_t epd370_wait_cycle(uint32_t assert_ms, uint32_t release_ms, bool *asserted);

/* 只等 BUSY 回到空闲（FrameFilm 的 epd_wait_busy 语义）。用于初始化那几条。 */
int32_t epd370_wait_idle(uint32_t timeout_ms);

/* 当前 BUSY 原始电平（1 = 空闲） */
int epd370_busy_level(void);

/* SPI 总线建好了没（init 过没有） */
bool epd370_is_ready(void);

/* POF（幂等由调用方负责） */
esp_err_t epd370_power_off(void);

#endif /* EPD370_H */
