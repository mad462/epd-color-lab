/* 自动生成，不要手改 —— 源：docs/palette_{qual,fast}.csv */
#ifndef PALETTE_TABLE_H
#define PALETTE_TABLE_H

#include <stdint.h>

#define PALETTE_ENTRIES 64

extern const uint8_t palette_qual[PALETTE_ENTRIES][3];
extern const uint8_t palette_fast[PALETTE_ENTRIES][3];

/* 有效槽位（占位槽不参与量化，校准也按这个列表编号） */
extern const uint8_t palette_qual_active[];
extern const int palette_qual_active_len;
extern const uint8_t palette_fast_active[];
extern const int palette_fast_active_len;

#endif
