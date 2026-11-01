#include "sim_board.h"
#include <string.h>
#include "board.h"
#include "board_hal.h"
#include "events.h"
#include "fwk_hal.h"

sb_t sb;
static uint64_t next_wake;
static uint64_t assist_stored_at_us;
static int assist_have;

/* Battery-side currents (mA) behind each HAL action. Sources: architecture.md section 4 per-event charges (modem side
 * x 1.41 boost factor): attach/poll 0.2 C -> 0.28 C = 70 mA x 4 s; upload+RRC tail 0.25 C -> 0.35 C = 100 mA x 3.5 s;
 * assistance download 0.3 C -> 0.42 C = 50 mA x 8.5 s (C14: 27648 B file = 11.75 s = 0.59 C); GNSS 10 mA x 1.41; buzzer 30 mA (conflicts.md C1); LED 5 mA (C1). */
#define MA_ATTACH 70.0
#define MA_UPLOAD 100.0
#define MA_ASSIST 50.0
#define MA_GNSS   14.1
#define MA_BUZ    30.0
#define MA_LED    5.0

static struct { uint64_t end; double ma; double *bucket; } op;
static int fota_chunk_no;

static double uc(double ma, uint64_t us) { return ma * (double)us / 1000.0; }

static void op_begin(double ma, uint64_t dur_us, double *bucket)
{
    op.end = fwk_hal_now_us() + dur_us;
    op.ma = ma;
    op.bucket = bucket;
    double q = uc(ma, dur_us);
    *bucket += q;
    sb.q_uc += q;
}

static int tx_busy(void) { return fwk_hal_now_us() < op.end; }

void sb_reboot(int reset_cause)
{
    uint32_t off = sb.fota_ret_off, kb = sb.fota_ret_kb; int bo = sb.brownout_ret, pc = sb.fota_pending_confirm, quota[16], nq = sb.nquota;
    int32_t q[16]; memcpy(q, sb.quota, sizeof q); (void)quota;
    sb_reset();
    sb.fota_ret_off = off; sb.fota_ret_kb = kb; sb.brownout_ret = bo; sb.fota_pending_confirm = pc; sb.nquota = nq; memcpy(sb.quota, q, sizeof q);
    sb.reset_cause = reset_cause;
}

void sb_reset(void)
{
    memset(&sb, 0, sizeof sb);
    memset(&op, 0, sizeof op);
    assist_have = 0;
    fota_chunk_no = 0;
    next_wake = 0;
    sb.vdd_low_mv = 3300; sb.vdd_full_mv = 4000; sb.vdd_full_at_us = 0;
    sb.cell_idle_mv = 3200; sb.cell_loaded_mv = 3000; sb.temp_c10 = 200;
    sb.ltem_ok = 1; sb.nb_ok = 1; sb.edrx_grant = 1; sb.upload_ok = 1; sb.assist_ok = 1;
    sb.attach_ms = 4000; sb.upload_ms = 3500; sb.assist_ms = 11750;   /* 27648 B EPO file at the 2353 B/s of the 20 kB x 8.5 s figure */
    sb.ttff_ms = 28000; sb.ttff_assist_ms = 5000; sb.assist_valid_h = BOARD_P_EPO_VALID_H;   /* LC76G: 28 s cold, 5 s with EPO (parts-rf.md); 3-day file (AN V1.1) */
    sb.fota_kbps_x10 = 125; sb.fota_fail_chunk = -1;                  /* 100 kbit/s = 12.5 kB/s (architecture.md section 4) */
    sb.reset_cause = RESET_POWERON; sb.assist_age_h0 = -1; sb.btn_level = 1;
    sb.txpower_dbm = 0;
}

/* ---- power */
void board_vbat_divider(int on)
{
    uint64_t now = fwk_hal_now_us();
    if (on && !sb.divider_on_now) sb.divider_on_since = now;
    if (!on && sb.divider_on_now) sb.divider_on_us += now - sb.divider_on_since;
    sb.divider_on_now = on;
}
int board_vbat_pin_mv(void)
{
    sb.vbat_reads++;
    if (!sb.divider_on_now) { sb.vbat_reads_divider_off++; return 0; }
    int cell = tx_busy() ? sb.cell_loaded_mv : sb.cell_idle_mv;
    return (int)(cell / BOARD_VBAT_DIV_RATIO);
}
int board_modem_vdd_mv(void)
{
    uint64_t now = fwk_hal_now_us();
    if (now >= sb.vdd_dip_from_us && now < sb.vdd_dip_to_us) return sb.vdd_low_mv;      /* supercap drooped by a burst */
    return now >= sb.vdd_full_at_us ? sb.vdd_full_mv : sb.vdd_low_mv;
}
int board_temp_c10(void) { return sb.temp_c10; }

/* ---- modem */
static void check_gate(int *counter)
{
    if (board_modem_vdd_mv() < BOARD_ATTACH_MIN_VBAT_MV)
        (*counter)++;
}
void board_modem_attach(int rat, int req_edrx)
{
    (void)req_edrx;
    uint64_t now = fwk_hal_now_us();
    sb.attach_calls++;
    if (!sb.t_first_attach_us) sb.t_first_attach_us = now;
    sb.t_last_attach_us = now;
    check_gate(&sb.attach_with_low_vdd);
    check_gate(&sb.tx_while_gate_low);
    if (rat == RAT_LTEM) sb.attach_ltem++; else sb.attach_nb++;
    int ok = rat == RAT_LTEM ? sb.ltem_ok : sb.nb_ok;
    uint64_t d = (uint64_t)sb.attach_ms * 1000;
    op_begin(MA_ATTACH, d, &sb.q_attach);
    if (ok) {
        fwk_sim_inject_at(now + d, ev_modem_evt(MK_ATTACH_OK, sb.edrx_grant));
        if (!sb.edrx_grant) {
            fwk_sim_inject_at(now + d + 500000, ev_modem_evt(MK_POLL_REPLY, sb.poll_pending));
            sb.poll_pending = 0;
        }
    } else
        fwk_sim_inject_at(now + d, ev_modem_evt(MK_ATTACH_FAIL, 0));
}
void board_modem_sleep_mode(int mode) { sb.sleep_mode = mode; }
void board_modem_set_txpower(int dbm) { sb.txpower_calls++; sb.txpower_dbm = dbm; }
void board_modem_upload(int valid, int age_s, int idle_mv, int loaded_mv, int low_batt)
{
    uint64_t now = fwk_hal_now_us();
    sb.uploads++;
    sb.upload_valid = valid; sb.upload_age = age_s; sb.upload_idle_mv = idle_mv; sb.upload_loaded_mv = loaded_mv; sb.upload_low = low_batt;
    sb.t_last_upload_us = now;
    check_gate(&sb.tx_while_gate_low);
    uint64_t d = (uint64_t)sb.upload_ms * 1000;
    op_begin(MA_UPLOAD, d, &sb.q_upload);
    fwk_sim_inject_at(now + d, ev_modem_evt(sb.upload_ok ? MK_UPLOAD_OK : MK_UPLOAD_FAIL, 0));
}
void board_modem_assist_download(void)
{
    uint64_t now = fwk_hal_now_us();
    sb.assists++;
    check_gate(&sb.tx_while_gate_low);
    uint64_t d = (uint64_t)sb.assist_ms * 1000;
    op_begin(MA_ASSIST, d, &sb.q_assist);
    fwk_sim_inject_at(now + d, ev_modem_evt(sb.assist_ok ? MK_ASSIST_OK : MK_ASSIST_FAIL, 27648));
}
void board_modem_abort(void)
{
    uint64_t now = fwk_hal_now_us();
    sb.aborts++;
    if (now < op.end && op.bucket) {                 /* refund the part that did not run */
        double q = uc(op.ma, op.end - now);
        *op.bucket -= q;
        sb.q_uc -= q;
        op.end = now;
    }
}

/* ---- gnss */
int board_assist_age_h(void)
{
    int age;
    if (assist_have) age = (int)((fwk_hal_now_us() - assist_stored_at_us) / 3600000000ull);
    else age = sb.assist_age_h0 < 0 ? -1 : sb.assist_age_h0 + (int)(fwk_hal_now_us() / 3600000000ull);
    if (age > sb.max_assist_age_h && assist_have) sb.max_assist_age_h = age;      /* age of a file that was refreshed in time */
    return age;
}
void board_assist_store(int bytes) { (void)bytes; assist_have = 1; assist_stored_at_us = fwk_hal_now_us(); }
void board_gnss_en(int on)
{
    uint64_t now = fwk_hal_now_us();
    if (on && !sb.gnss_en) {
        sb.gnss_on_since = now; sb.gnss_cycles++;
        int age = board_assist_age_h();
        int ttff = (age >= 0 && age <= sb.assist_valid_h) ? sb.ttff_assist_ms : sb.ttff_ms;
        if (age >= 0 && age <= sb.assist_valid_h) sb.hot_fixes++; else sb.cold_fixes++;
        if (ttff > 0)
            fwk_sim_inject_at(now + (uint64_t)ttff * 1000, ev_gnss_evt(GK_FIX_OK, ttff));
    }
    if (!on && sb.gnss_en) {
        uint64_t d = now - sb.gnss_on_since;
        sb.gnss_on_us += d;
        if (d > sb.gnss_max_on_us) sb.gnss_max_on_us = d;
        double q = uc(MA_GNSS, d);
        sb.q_gnss += q; sb.q_uc += q;
        if (sb.gnss_uart) sb.gnss_backpower_viol++;      /* UART still connected when the supply drops */
    }
    sb.gnss_en = on;
}
void board_gnss_uart(int connect)
{
    if (connect && !sb.gnss_en) sb.gnss_backpower_viol++;
    sb.gnss_uart = connect;
}
void board_gnss_rst(int drive_low) { sb.gnss_rst_low = drive_low; }
int board_gnss_inject_assist(void) { sb.gnss_inject_ok++; return assist_have; }

/* ---- ui */
void board_led(int on)
{
    uint64_t now = fwk_hal_now_us();
    if (on && !sb.led_on) { sb.led_on_since = now; sb.led_pulses++; }
    if (!on && sb.led_on) {
        uint64_t d = now - sb.led_on_since;
        sb.led_on_us += d;
        double q = uc(MA_LED, d);
        sb.q_led += q; sb.q_uc += q;
    }
    sb.led_on = on;
}
int board_btn_level(void) { return sb.btn_level; }
int sb_buz_active(void) { return sb.buz_cmd_on && fwk_hal_now_us() < sb.buz_end_us; }
static void buz_account(uint64_t until)
{
    if (!sb.buz_cmd_on) return;
    uint64_t end = until < sb.buz_end_us ? until : sb.buz_end_us;
    uint64_t d = end > sb.buz_since ? end - sb.buz_since : 0;
    sb.buz_on_us += d;
    if (d > sb.buz_max_run_us) sb.buz_max_run_us = d;
    double q = uc(MA_BUZ, d);
    sb.q_buz += q; sb.q_uc += q;
    sb.buz_cmd_on = 0;
}
void board_buz_start(uint32_t hz, uint32_t duration_ms)
{
    (void)hz;
    uint64_t now = fwk_hal_now_us();
    if (duration_ms > BOARD_P_BUZZER_ON_S * 1000u) sb.buz_overcap++;      /* the contract: never a longer sequence than 10 s */
    buz_account(now);
    sb.buz_starts++;
    sb.buz_cmd_on = 1; sb.buz_since = now; sb.buz_end_us = now + (uint64_t)duration_ms * 1000;
}
void board_buz_stop(void) { sb.buz_stop_calls++; buz_account(fwk_hal_now_us()); }

/* ---- wdt, reset, accel, storage */
void board_wdt_done_pulse(void) { sb.dones++; sb.last_done_us = fwk_hal_now_us(); }
int board_reset_cause(void) { return sb.reset_cause; }
void board_accel_arm(void) { sb.accel_arms++; }
void board_quota_load(int32_t *d, int *n) { for (int i = 0; i < sb.nquota; i++) d[i] = sb.quota[i]; *n = sb.nquota; }
void board_quota_save(const int32_t *d, int n) { sb.quota_saves++; sb.nquota = n; for (int i = 0; i < n; i++) sb.quota[i] = d[i]; }
int board_day_index(void) { return sb.day; }


/* ---- TPL5010 (SNAS651A 8.3, Figure 8): WAKE every tIP = wdt_period_s; DONE must come >= 20 ms before the next WAKE; a missed DONE
 * is detected at that next WAKE and RSTn falls one tIP after the missed WAKE: a hang right after a DONE resets after up to 2 x tIP
 * (the model flags a DONE later than one period after its WAKE; the reset time is checked in test_wdt against buzzer_hw_cap_s) */
void sb_run(uint64_t until)
{
    uint64_t period = (uint64_t)BOARD_P_WDT_PERIOD_S * 1000000ull;
    if (next_wake <= fsim.now) next_wake = (fsim.now / period + 1) * period;
    while (next_wake <= until) {
        fwk_sim_run_until(next_wake - 1);
        fwk_sim_inject_at(next_wake, ev_wdt_wake_irq(0));
        fwk_sim_run_until(next_wake);
        uint64_t lat = sb.last_done_us >= next_wake ? sb.last_done_us - next_wake : UINT64_MAX;
        sb.tpl_wakes++;
        if (lat > period) {                          /* DONE later than one period after WAKE: the TPL5010 would have reset */
            if (!sb.tpl_missed) sb.tpl_first_missed_wake_us = next_wake;
            sb.tpl_missed++;
        }
        next_wake += period;
    }
    fwk_sim_run_until(until);
}

/* ---- fota (IF-8): 100 kbit/s = 12.5 kB/s + 0.5 s request set-up at 50 mA (architecture.md section 4: 300 kB = 24 s x 50 mA) */
void board_fota_fetch(uint32_t offset, uint32_t len, uint32_t id)
{
    uint64_t now = fwk_hal_now_us();
    uint64_t d = 500000ull + (uint64_t)len * 10 * 1000000ull / ((uint64_t)sb.fota_kbps_x10 * 1000ull);
    if (!sb.fota_fetches) sb.fota_first_off = offset;
    if (sb.fota_fetches < 64) sb.fota_fetch_us[sb.fota_fetches] = now;
    sb.fota_fetches++; sb.fota_last_off = offset;
    check_gate(&sb.fota_fetch_low_vdd);
    check_gate(&sb.tx_while_gate_low);
    op_begin(MA_ASSIST, d, &sb.q_fota);
    if (sb.fota_fail_chunk == fota_chunk_no++)
        fwk_sim_inject_at(now + d, ev_modem_evt(MK_FOTA_FAIL, (int32_t)(id << 16)));
    else {
        sb.fota_bytes += len;
        fwk_sim_inject_at(now + d, ev_modem_evt(MK_FOTA_CHUNK, (int32_t)((id << 16) | len)));
    }
}
void board_fota_abort(void)
{
    uint64_t now = fwk_hal_now_us();
    sb.fota_aborts++;
    if (now < op.end && op.bucket) {                 /* refund the part that did not run */
        double q = uc(op.ma, op.end - now);
        *op.bucket -= q; sb.q_uc -= q; op.end = now;
    }
}
void board_fota_progress_load(uint32_t *offset, uint32_t *total_kb) { *offset = sb.fota_ret_off; *total_kb = sb.fota_ret_kb; }
void board_fota_progress_save(uint32_t offset, uint32_t total_kb) { sb.fota_ret_off = offset; sb.fota_ret_kb = total_kb; }
void board_fota_apply(void) { sb.fota_applies++; sb.fota_pending_confirm = 1; sb.fota_ret_off = 0; sb.fota_ret_kb = 0; }
int  board_fota_pending_confirm(void) { return sb.fota_pending_confirm; }
void board_fota_confirm(void) { sb.fota_confirms++; sb.fota_pending_confirm = 0; }
void board_fota_revert_reboot(void) { sb.fota_reverts++; sb.fota_pending_confirm = 0; }
int  board_brownout_load(void) { return sb.brownout_ret; }
void board_brownout_save(int count) { sb.brownout_saves++; sb.brownout_ret = count; }
