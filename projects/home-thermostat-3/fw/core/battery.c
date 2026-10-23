#include "battery.h"
#include "board.h"

void bat_cold_boot(bat_state_t *b)
{
    b->low_count = 0;
    b->cutoff = 0;
    b->warned = 0;
    b->has_last = 0;
    b->lithium = 0;
    b->last_on_mv = 0;
}

int bat_mv_from_adc(int adc_mv)
{
    return (int)((float)adc_mv * BOARD_VBAT_DIV_RATIO + 0.5f);
}

bat_event_t bat_update(bat_state_t *b, int vbat_mv)
{
    if (b->cutoff)
        return BAT_CUTOFF;
    if (vbat_mv <= BOARD_VBAT_CUTOFF_MV) {
        if (++b->low_count >= BOARD_CUTOFF_COUNT) {
            b->cutoff = 1;
            return BAT_CUTOFF_NOW;
        }
    } else {
        b->low_count = 0;
    }
#ifdef BOARD_VBAT_WARN_MV
    if (vbat_mv <= BOARD_VBAT_WARN_MV && !b->warned) {
        b->warned = 1;
        return BAT_WARN;
    }
#endif
    return BAT_OK;
}

int bat_reverse_suspect(int s1_mv, int s2_mv)
{
    return (s1_mv - s2_mv) > BOARD_REVERSE_DROP_MV;
}

int bat_guard_rule8a(const bat_state_t *b, int s1_mv)
{
    if (b->has_last)
        return b->last_on_mv < BOARD_GUARD_ON_BELOW_MV;
    return s1_mv < BOARD_COLD_BOOT_GUARD_BELOW_MV;
}

int bat_guard_wanted(const bat_state_t *b, int s1_mv)
{
    if (!b->has_last || b->lithium)
        return s1_mv < BOARD_COLD_BOOT_GUARD_BELOW_MV;
    return b->last_on_mv < BOARD_GUARD_ON_BELOW_MV;
}

void bat_store_reading(bat_state_t *b, int guard_on, int vbat_mv)
{
    b->has_last = 1;
    b->last_on_mv = (int16_t)vbat_mv;
    /* a guard-ON reading >= 3.35 V ends the guard (rule 8c) and marks the cells as not-alkaline as well */
    b->lithium = (uint8_t)(!guard_on || vbat_mv >= BOARD_GUARD_ON_BELOW_MV);
}
