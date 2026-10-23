#include "ntc.h"
#include "board.h"

#define Q16 65536u
/* R19 window: 2-98 % of the excitation (board.h, from interface.yaml) */
#define OPEN_Q16  ((uint32_t)(BOARD_PROBE_OPEN_RATIO * Q16))
#define SHORT_Q16 ((uint32_t)(BOARD_PROBE_SHORT_RATIO * Q16))

ntc_status_t ntc_ratio_q16(int32_t code_ain, int32_t code_ref, uint32_t *ratio_q16)
{
    if (code_ref <= 0)
        return NTC_NO_REF;                       /* SENS_EN off or ADC not answering */
    if (code_ain < 0)
        code_ain = 0;
    uint64_t r = ((uint64_t)code_ain * Q16 + (uint64_t)code_ref / 2) / (uint64_t)code_ref;
    *ratio_q16 = (uint32_t)r;
    if (r >= OPEN_Q16)
        return NTC_OPEN;
    if (r <= SHORT_Q16)
        return NTC_SHORT;
    return NTC_OK;
}

int32_t ntc_temp_mc(uint32_t r)
{
    const uint16_t *t = BOARD_NTC_TABLE_Q16;
    const int n = BOARD_NTC_TABLE_LEN;
    if (r >= t[0])
        return BOARD_NTC_T_MIN_C * 1000;
    if (r <= t[n - 1])
        return (BOARD_NTC_T_MIN_C + (n - 1) * BOARD_NTC_T_STEP_C) * 1000;
    int lo = 0, hi = n - 1;                      /* table falls with T: find t[i] >= r > t[i+1] */
    while (hi - lo > 1) {
        int mid = (lo + hi) / 2;
        if (t[mid] >= r)
            lo = mid;
        else
            hi = mid;
    }
    int32_t span = (int32_t)t[lo] - (int32_t)t[lo + 1];
    int32_t frac = (int32_t)(((int64_t)((int32_t)t[lo] - (int32_t)r) * BOARD_NTC_T_STEP_C * 1000 + span / 2) / span);
    return (BOARD_NTC_T_MIN_C + lo * BOARD_NTC_T_STEP_C) * 1000 + frac;
}
