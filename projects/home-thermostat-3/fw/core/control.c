#include "control.h"

int control_decide(room_t *r, int32_t temp_mc, ntc_status_t st, uint32_t now_s, uint32_t min_dwell_s)
{
    int want = r->on;
    if (st != NTC_OK)
        want = 0;
    else if (temp_mc < r->setpoint_mc - APP_HYST_MC / 2)
        want = 1;
    else if (temp_mc > r->setpoint_mc + APP_HYST_MC / 2)
        want = 0;
    if (want == r->on)
        return r->on;
    int fault_off = (st != NTC_OK && want == 0);
    if (!fault_off && r->changed && now_s - r->last_change_s < min_dwell_s)
        return r->on;                            /* pulse budget (interface.yaml relay_max_pulses_per_day) */
    r->on = (uint8_t)want;
    r->changed = 1;
    r->last_change_s = now_s;
    return r->on;
}
