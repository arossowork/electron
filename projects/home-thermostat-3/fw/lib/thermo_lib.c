#include "thermo_lib.h"
#include "board.h"

#define Q16 65536u

int ntc_ratio_q16(int32_t ain, int32_t ref, uint32_t *q)
{
    if (ref <= 0)
        return NTC_NO_REF;
    if (ain < 0)
        ain = 0;
    uint64_t r = ((uint64_t)ain * Q16 + (uint64_t)ref / 2) / (uint64_t)ref;
    *q = (uint32_t)r;
    if (r >= (uint32_t)(BOARD_PROBE_OPEN_RATIO * Q16))
        return NTC_OPEN;
    if (r <= (uint32_t)(BOARD_PROBE_SHORT_RATIO * Q16))
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
    int lo = 0, hi = n - 1;
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

int bat_guard_rule8a(const bat_state_t *b, int s1)
{
    return b->has_last ? b->last_on_mv < BOARD_GUARD_ON_BELOW_MV : s1 < BOARD_COLD_BOOT_GUARD_BELOW_MV;
}

int bat_guard_wanted(const bat_state_t *b, int s1)
{
    if (!b->has_last || b->not_alkaline)
        return s1 < BOARD_COLD_BOOT_GUARD_BELOW_MV;
    return b->last_on_mv < BOARD_GUARD_ON_BELOW_MV;
}

void bat_store(bat_state_t *b, int guard_on, int mv)
{
    b->has_last = 1;
    b->last_on_mv = (int16_t)mv;
    b->not_alkaline = (uint8_t)(!guard_on || mv >= BOARD_GUARD_ON_BELOW_MV);
}

int bat_update(bat_state_t *b, int mv)
{
    if (b->cutoff)
        return BAT_CUT;
    if (mv <= BOARD_VBAT_CUTOFF_MV) {
        if (++b->low_count >= BOARD_CUTOFF_COUNT) {
            b->cutoff = 1;
            return BAT_CUTOFF_NOW;
        }
    } else {
        b->low_count = 0;
    }
#ifdef BOARD_VBAT_WARN_MV
    if (mv <= BOARD_VBAT_WARN_MV && !b->warned) {
        b->warned = 1;
        return BAT_WARN;
    }
#endif
    return BAT_OK;
}
