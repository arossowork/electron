/* Pure arithmetic of the tracker firmware: no scheduler, no HAL. Tested on the host. */
#ifndef TRACKER_LIB_H
#define TRACKER_LIB_H
#include <stdint.h>

#define QUOTA_MAX 16   /* session start days kept (>= BOARD_MAX_LOST_SESSIONS_PER_MONTH) */

int  batt_cell_mv(int pin_mv);                     /* divider pin voltage -> cell voltage (PWR-6) */
int  tx_gate_ok(int vdd_mv);                       /* no attach/TX below attach_min_vbat_mv (PWR-7) */
/* TX power class from the loaded cell voltage and the temperature (0.1 C), with release hysteresis. */
int  tx_power_decide(int cell_loaded_mv, int temp_c10, int current_dbm);
/* lost-mode quota: sessions started in the last lost_window_days days (PWR-3) */
int  quota_allow(const int32_t *start_days, int n, int today);
int  quota_record(int32_t *start_days, int n, int today);   /* returns the new count, drops entries out of the window */
/* poll/edrx helper: next backoff delay after a brown-out reset, doubling up to the poll period */
uint32_t backoff_s(int n_brownouts);
/* request latency allowance left for the device after the eDRX cycle and the paging window (IF-2), seconds */
float if2_device_allowance_s(float latency_max_s, float edrx_s, float ptw_s);
/* buzzer: PWM sequence plan for hz and duration_ms. periods = ms x hz / 1000 = REFRESH_periods x LOOP. Returns 1 and fills
 * *refresh (additional periods per value, 24-bit field) and *loop (16-bit LOOP.CNT) or returns 0 (not representable,
 * longer than buzzer_on_s, or zero). 10 s at 4 kHz = 40000 periods = 100 x 400. */
int  buz_pwm_plan(uint32_t hz, uint32_t duration_ms, uint32_t *refresh, uint32_t *loop);
/* EPO assistance (IF-3): due when none is stored or the stored file has reached assist_period_h (validity - 12 h lead) */
int  assist_due(int age_h);
/* a stored file still valid for the next `hours` hours (lost session: lost_max_duration_s / 3600) */
int  assist_covers(int age_h, int hours);
/* lost mode: a download of dl_mc pays off when fixes_left x (cold_mc - hot_mc) > dl_mc */
int  assist_pays_off(int fixes_left);
/* low-battery report (PWR-6, lead default C16) */
int  low_batt_decide(int idle_mv, int sag_count);
int  low_batt_clear(int idle_mv);
/* FOTA: image fits the slot; next chunk length (0 = complete) */
int  fota_size_ok(uint32_t image_kb);
uint32_t fota_next_len(uint32_t offset, uint32_t total_bytes);
#endif
