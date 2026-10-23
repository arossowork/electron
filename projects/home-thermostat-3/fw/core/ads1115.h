/* ADS1115 single-shot read (R17): address and AINn from board.h, data rate BOARD_ADS_SPS (rule 12). */
#ifndef ADS1115_H
#define ADS1115_H
#include <stdint.h>

int ads_read(int ain, int16_t *code);   /* 0 = ok; light-sleeps for the conversion */

#endif
