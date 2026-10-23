/* NTC conversion (R17) and probe fault detection (R19). Pure functions, no HAL. */
#ifndef NTC_H
#define NTC_H
#include <stdint.h>

typedef enum { NTC_OK = 0, NTC_OPEN, NTC_SHORT, NTC_NO_REF } ntc_status_t;

/* ratio = code_ain / code_ref in Q16 (ratiometric: both read at the same PGA, conflicts.md C8) */
ntc_status_t ntc_ratio_q16(int32_t code_ain, int32_t code_ref, uint32_t *ratio_q16);
/* temperature in milli-degC from the board table (linear interpolation, clamped to the table range) */
int32_t ntc_temp_mc(uint32_t ratio_q16);

#endif
