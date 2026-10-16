#ifndef PIN_MAP_H
#define PIN_MAP_H

#include "driver/gpio.h"

/* ------------------------------------------------------------------------
 * 板子：OSPTEK ESP32-S3 Demo 开发板 V1.3
 * 屏：3.7" JD7601 / GDEH037E01 类，720x480 六色
 *
 * 和以前几块屏走同一组排针，引脚没变。
 * 本工程独立，不共享 ..\jd7601 / ..\e6-730 / ..\epd0356 的任何代码。
 * ---------------------------------------------------------------------- */

#define eink_sda    GPIO_NUM_38   /* MOSI */
#define eink_clk    GPIO_NUM_47   /* SCLK */
#define eink_cs     GPIO_NUM_41
#define eink_dc     GPIO_NUM_40
#define eink_rst    GPIO_NUM_39
#define eink_busy   GPIO_NUM_42   /* 低 = 忙，高 = 空闲 */

/* ------------------------------------------------------------- TF 卡座
 * SDMMC 4-bit。e6-730 / jd7601 上实测可用。
 */
#define tf_clk      GPIO_NUM_6
#define tf_cmd      GPIO_NUM_4
#define tf_d0       GPIO_NUM_7
#define tf_d1       GPIO_NUM_8
#define tf_d2       GPIO_NUM_1
#define tf_d3       GPIO_NUM_2
#define tf_detect   GPIO_NUM_5    /* 未使用 */

#endif /* PIN_MAP_H */
