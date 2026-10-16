#ifndef IMAGE_H
#define IMAGE_H

#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>

/* 把串口上已经积压的字节丢掉（刷新期间按的键都在这儿被清掉，不排队）。 */
void image_drain_input(uint32_t ms);

/* 从 UART0 收 len 个字节的原始索引图（配合 host/send_index.py）。
 * 尾部跟 4 字节小端 CRC32，校验不过直接报错。 */
esp_err_t image_recv_serial(uint8_t *fb, size_t len);

/* 从 UART0 收一个文件并存到 dir 下（配合 host/send_file.py）。
 *
 * 线序：
 *   "EPF" (3B) | name_len (1B) | name (name_len B) | size (4B LE) | crc32 (4B LE)
 *   然后 size 个字节的数据
 *
 * 收到头之后会打印一行 "开始发送数据"，host 看到再发数据。
 * 返回保存后的完整路径到 out_path（可传 NULL）。 */
esp_err_t image_recv_file(const char *dir, char *out_path, size_t out_path_len);

#endif /* IMAGE_H */
