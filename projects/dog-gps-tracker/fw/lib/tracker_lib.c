#include "tracker_lib.h"
#include "board.h"

int batt_cell_mv(int pin_mv)
{
    return (int)(pin_mv * BOARD_VBAT_DIV_RATIO + 0.5f);
}

int tx_gate_ok(int vdd_mv)
{
    return vdd_mv >= BOARD_ATTACH_MIN_VBAT_MV;
}

int tx_power_decide(int cell_loaded_mv, int temp_c10, int current_dbm)
{
    int sag = cell_loaded_mv < BOARD_P_TX_CELL_SAG_MV;
    int cold = temp_c10 < BOARD_P_TX_COLD_LIMIT_C * 10;
    if (sag || cold)
        return BOARD_P_TX_BACKOFF_DBM;
    if (current_dbm == BOARD_P_TX_BACKOFF_DBM) {      /* release only past the hysteresis */
        if (cell_loaded_mv < BOARD_P_TX_CELL_SAG_MV + BOARD_P_TX_HYST_MV ||
            temp_c10 < (BOARD_P_TX_COLD_LIMIT_C + BOARD_P_TX_HYST_C) * 10)
            return BOARD_P_TX_BACKOFF_DBM;
    }
    return BOARD_P_TX_NOMINAL_DBM;
}

int quota_allow(const int32_t *d, int n, int today)
{
    int in = 0;
    for (int i = 0; i < n; i++)
        if (today - d[i] < BOARD_P_LOST_WINDOW_DAYS)
            in++;
    return in < BOARD_MAX_LOST_SESSIONS_PER_MONTH;
}

int quota_record(int32_t *d, int n, int today)
{
    int k = 0;
    for (int i = 0; i < n; i++)
        if (today - d[i] < BOARD_P_LOST_WINDOW_DAYS)
            d[k++] = d[i];
    if (k < QUOTA_MAX)
        d[k++] = today;
    return k;
}

uint32_t backoff_s(int n)
{
    uint32_t cap = BOARD_P_POLL_PERIOD_MIN * 60u, v = BOARD_P_BROWNOUT_BACKOFF_S;
    while (n-- > 0 && v < cap)
        v *= 2;
    return v < cap ? v : cap;
}

float if2_device_allowance_s(float latency_max_s, float edrx_s, float ptw_s)
{
    return latency_max_s - edrx_s - ptw_s;
}

int buz_pwm_plan(uint32_t hz, uint32_t duration_ms, uint32_t *refresh, uint32_t *loop)
{
    uint64_t periods = (uint64_t)duration_ms * hz / 1000u;
    if (!hz || !periods || duration_ms > BOARD_P_BUZZER_ON_S * 1000u)
        return 0;
    uint32_t per_loop = 100;                        /* 100 periods per LOOP pass: 25 ms at 4 kHz */
    if (periods % per_loop || periods / per_loop > 65535u || per_loop - 1 > 0xFFFFFFu)
        return 0;
    *refresh = per_loop - 1;
    *loop = (uint32_t)(periods / per_loop);
    return 1;
}

int assist_covers(int age_h, int hours)
{
    return age_h >= 0 && age_h + hours <= BOARD_P_EPO_VALID_H;
}

int assist_due(int age_h)
{
    return age_h < 0 || age_h >= BOARD_P_ASSIST_PERIOD_H;
}

int assist_pays_off(int fixes_left)
{
    return (long)fixes_left * (BOARD_P_COLD_FIX_MC - BOARD_P_HOT_FIX_MC) > BOARD_P_ASSIST_DL_MC;
}

int low_batt_decide(int idle_mv, int sag_count)
{
    return idle_mv < BOARD_P_LOW_BATT_IDLE_MV || sag_count >= BOARD_P_LOW_BATT_SAG_COUNT;
}

int low_batt_clear(int idle_mv)
{
    return idle_mv >= BOARD_P_LOW_BATT_IDLE_MV + BOARD_P_TX_HYST_MV;
}

int fota_size_ok(uint32_t image_kb)
{
    return image_kb > 0 && image_kb <= BOARD_FOTA_SLOT_KB;
}

uint32_t fota_next_len(uint32_t offset, uint32_t total_bytes)
{
    uint32_t chunk = BOARD_P_FOTA_CHUNK_KB * 1024u;
    if (offset >= total_bytes)
        return 0;
    return total_bytes - offset < chunk ? total_bytes - offset : chunk;
}
