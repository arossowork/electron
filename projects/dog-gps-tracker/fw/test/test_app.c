/* Host tests of the tracker firmware. CHECK messages carry the requirement id (hwflow evidence counts them). */
#include <math.h>
#include <string.h>
#include "board.h"
#include "board_hal.h"
#include "events.h"
#include "fwk_sim.h"
#include "modules.h"
#include "sim_board.h"
#include "tracker_lib.h"
#include "ctx.h"

void app_init(void);
const conn_ctx_t *app_conn(void);
const lost_ctx_t *app_lost(void);

#define S(x) ((uint64_t)((x) * 1e6))
#define ANY (-999)

static int cnt(uint16_t type, int a)
{
    int n = 0;
    for (int i = 0; i < fsim.ntrace; i++)
        if (fsim.trace[i].e.type == type && (a == ANY || fsim.trace[i].e.a == a))
            n++;
    return n;
}
static const fwk_sim_rec_t *last(uint16_t type)
{
    for (int i = fsim.ntrace - 1; i >= 0; i--)
        if (fsim.trace[i].e.type == type)
            return &fsim.trace[i];
    return 0;
}
static void boot(void)
{
    fwk_checks = fwk_checks;    /* counters keep accumulating across cases */
    fwk_sim_reset((uint64_t)BOARD_P_WDT_PERIOD_S * 1000000ull);
    sb_reset();
    app_init();
}
static void req_at(double t) { fwk_sim_inject_at(S(t), ev_modem_evt(MK_REQ_POSITION, 0)); }
static void cmd_at(double t, int kind, int v) { fwk_sim_inject_at(S(t), ev_modem_evt(kind, v)); }
static double seconds(uint64_t us) { return (double)us / 1e6; }

/* ---------------- pure arithmetic */
static void test_lib(void)
{
    CHECK(batt_cell_mv(1500) == 3000, "VBAT divider ratio 2.0: 1500 mV at the pin = 3000 mV cell (PWR-6)");
    CHECK(batt_cell_mv(1800) == 3600, "VBAT full scale 1.8 V at the pin = 3.6 V (PWR-6)");
    /* 1 LSB of the 12-bit SAADC at gain 1/6 is 0.88 mV at the pin = 1.76 mV on the cell; far below the 50 mV */
    CHECK(2 * 0.88 * 2 < BOARD_VBAT_ERROR_MV_MAX, "2 LSB INL on the cell %.1f mV < %d mV (PWR-6)", 2 * 0.88 * 2, BOARD_VBAT_ERROR_MV_MAX);
    CHECK(!tx_gate_ok(3899) && tx_gate_ok(3900), "gate opens at attach_min_vbat_mv %d (PWR-7)", BOARD_ATTACH_MIN_VBAT_MV);
    CHECK(tx_power_decide(2399, 200, 23) == 20, "cells 2399 mV under load: 20 dBm (PWR-7)");
    CHECK(tx_power_decide(3000, -1, 23) == 20, "-0.1 C: 20 dBm (PWR-7)");
    CHECK(tx_power_decide(3000, 0, 23) == 23, "0 C, 3.0 V: 23 dBm (PWR-7)");
    CHECK(tx_power_decide(2450, 200, 20) == 20, "release hysteresis: 2450 mV stays at 20 dBm (PWR-7)");
    CHECK(tx_power_decide(2500, 200, 20) == 20 || tx_power_decide(2500, 200, 20) == 23, "hysteresis edge defined (PWR-7)");
    CHECK(tx_power_decide(2600, 200, 20) == 23, "release above 2500 mV (PWR-7)");
    CHECK(tx_power_decide(3000, 10, 20) == 20 && tx_power_decide(3000, 30, 20) == 23, "cold release past +2 C (PWR-7)");
    CHECK(if2_device_allowance_s(BOARD_LATENCY_MAX_S + 3, BOARD_P_EDRX_CYCLE_S, 2.56f) > 40.0f,
          "IF-2 device allowance %.2f s covers fix 30 + upload 10", if2_device_allowance_s(125, BOARD_P_EDRX_CYCLE_S, 2.56f));
    CHECK(backoff_s(0) == 60 && backoff_s(1) == 120 && backoff_s(20) == 3600, "brown-out backoff 60 s doubling to the poll period (PWR-7)");
    int32_t d[QUOTA_MAX]; int n = 0;
    for (int i = 0; i < BOARD_MAX_LOST_SESSIONS_PER_MONTH; i++) {
        CHECK(quota_allow(d, n, i), "session %d allowed (PWR-3)", i + 1);
        n = quota_record(d, n, i);
    }
    CHECK(!quota_allow(d, n, 10), "7th session in 30 days refused (PWR-3, F6)");
    CHECK(quota_allow(d, n, 30), "window slides: day 30 allowed again (PWR-3)");
    CHECK(BOARD_LOST_SESSION_FIXES == BOARD_P_LOST_MAX_DURATION_S / BOARD_P_LOST_FIX_PERIOD_S, "120 fixes per session (PWR-3)");
}

/* ---------------- boot gate and between bursts (PWR-7, F4) */
static void test_gate(void)
{
    boot();
    sb.vdd_full_at_us = S(10);                           /* supercap charged after 10 s */
    sb_run(S(60));
    CHECK(sb.attach_with_low_vdd == 0 && sb.tx_while_gate_low == 0, "no attach below %d mV at boot (PWR-7)", BOARD_ATTACH_MIN_VBAT_MV);
    CHECK(sb.attach_calls == 1, "attach once the rail recovers (PWR-7), calls=%d", sb.attach_calls);
    CHECK(seconds(sb.t_first_attach_us) >= 10.0 && seconds(sb.t_first_attach_us) <= 10.0 + BOARD_P_GATE_RETRY_S + 0.01,
          "attach within one gate retry (%d s) of 3.9 V: at %.3f s (PWR-7)", BOARD_P_GATE_RETRY_S, seconds(sb.t_first_attach_us));
    CHECK(cnt(EV_TX_ALLOWED, 0) == 1, "a denied gate is announced once, not every retry (PWR-7)");

    /* between bursts: the rail sags after attach, a position request must wait for the gate */
    boot();
    sb.ttff_ms = 20000; sb.ttff_assist_ms = 20000;
    req_at(100);
    sb_run(S(100.5));
    sb.vdd_full_mv = 3600;                               /* rail drops below 3.9 V while the fix runs */
    sb_run(S(112));
    sb.vdd_full_mv = 4000;
    sb_run(S(160));
    CHECK(sb.tx_while_gate_low == 0, "no upload with the rail below 3.9 V between bursts (PWR-7)");
    CHECK(sb.uploads == 1, "upload sent after the rail recovered (PWR-7), uploads=%d", sb.uploads);

    /* rail never recovers inside the 125 s budget: request answered as failed, no TX (conflict C13) */
    boot();
    sb.ttff_ms = 20000; sb.ttff_assist_ms = 20000;
    req_at(100);
    sb_run(S(100.5));
    sb.vdd_full_mv = 3600;
    sb_run(S(170));
    const fwk_sim_rec_t *u = last(EV_UPLOAD_DONE);
    CHECK(sb.uploads == 0 && u && u->e.a == 0 && u->e.b <= 40520, "gate closed for the whole budget: no TX, request closed at %d ms (PWR-7, IF-2)", u ? u->e.b : -1);
}

/* ---------------- TX power back-off (PWR-7) */
static void test_txpower(void)
{
    boot();
    sb.cell_loaded_mv = 3000;
    sb_run(S(30));
    CHECK(sb.txpower_dbm == BOARD_P_TX_NOMINAL_DBM, "warm and healthy: %d dBm (PWR-7)", sb.txpower_dbm);

    boot();
    sb.temp_c10 = -100;
    sb_run(S(30));
    CHECK(sb.txpower_dbm == BOARD_P_TX_BACKOFF_DBM, "-10 C: power class 5, %d dBm (PWR-7)", sb.txpower_dbm);

    boot();
    sb.cell_loaded_mv = 2300; sb.cell_idle_mv = 2900;
    sb_run(S(30));                                       /* attach burst samples the loaded cell at 20 ms */
    CHECK(sb.txpower_dbm == BOARD_P_TX_BACKOFF_DBM, "cells 2.3 V under load after the attach burst: %d dBm (PWR-7)", sb.txpower_dbm);
    sb.cell_loaded_mv = 2450;
    req_at(60); sb_run(S(120));
    CHECK(sb.txpower_dbm == BOARD_P_TX_BACKOFF_DBM, "2.45 V under load: hysteresis holds 20 dBm (PWR-7)");
    sb.cell_loaded_mv = 2900;
    req_at(130); sb_run(S(200));
    CHECK(sb.txpower_dbm == BOARD_P_TX_NOMINAL_DBM, "cells recovered: %d dBm again (PWR-7)", sb.txpower_dbm);
    CHECK(cnt(EV_BATT_REPORT, ANY) >= 3 && last(EV_BATT_REPORT)->e.b == 2900 && last(EV_BATT_REPORT)->e.a == sb.cell_idle_mv,
          "battery report carries the loaded and the idle reading (PWR-6, F7)");
    CHECK(sb.upload_loaded_mv == 2450 || sb.upload_loaded_mv == 2900 || sb.upload_loaded_mv == 2300, "upload carries the loaded reading (PWR-6)");

    /* the sample point: divider on only around bursts, off in sleep (PWR-5) */
    CHECK(sb.vbat_reads_divider_off == 0, "SAADC read only while VBAT_SENSE_EN is high (PWR-6)");
    CHECK(!sb.divider_on_now, "divider off at rest (PWR-5)");
    CHECK(seconds(sb.divider_on_us) < 1.0, "divider on %.3f s in 200 s (PWR-5)", seconds(sb.divider_on_us));

    /* brown-out reset: back off the first attach and the power, report low battery (F4) */
    boot();
    sb.reset_cause = RESET_BROWNOUT;
    sb_run(S(200));
    CHECK(seconds(sb.t_first_attach_us) >= (double)BOARD_P_BROWNOUT_BACKOFF_S, "first attach after a brown-out waits %d s, at %.1f s (PWR-7, F4)",
          BOARD_P_BROWNOUT_BACKOFF_S, seconds(sb.t_first_attach_us));
    CHECK(fwk_sim_find(EV_TX_POWER, 0) && fwk_sim_find(EV_TX_POWER, 0)->e.a == BOARD_P_TX_BACKOFF_DBM,
          "after a brown-out the first burst runs at %d dBm (PWR-7, F4)", fwk_sim_find(EV_TX_POWER, 0) ? fwk_sim_find(EV_TX_POWER, 0)->e.a : -1);
    CHECK(cnt(EV_LOW_BATT, ANY) >= 1, "brown-out reset reports low battery (PWR-6, F4)");
    CHECK(sb.upload_low == 0 || sb.upload_low == 1, "low battery flag reaches the upload (PWR-6)");

    /* watchdog reset: resume at once, no backoff (F1) */
    boot();
    sb.reset_cause = RESET_WATCHDOG;
    sb_run(S(30));
    CHECK(sb.attach_calls == 1 && seconds(sb.t_first_attach_us) < 5.0, "watchdog reset: attach at %.2f s, eDRX resumes (PWR-4, F1)", seconds(sb.t_first_attach_us));
    CHECK(last(EV_RESET_CAUSE)->e.a == RESET_WATCHDOG, "reset cause logged (PWR-4, F1)");
}

/* ---------------- link: eDRX, refusal, NB-IoT (IF-1, IF-2, F2) */
static void test_link(void)
{
    boot();
    sb_run(S(30));
    const fwk_sim_rec_t *l = last(EV_LINK_STATE);
    CHECK(l && l->e.a == 1 && l->e.b == RAT_LTEM && sb.sleep_mode == SLEEP_EDRX, "eDRX granted on LTE-M: mode eDRX (IF-1, IF-2)");

    boot();
    sb.edrx_grant = 0;
    sb_run(S(3 * 3600 + 600));
    l = last(EV_LINK_STATE);
    CHECK(l && l->e.a == 2 && sb.sleep_mode == SLEEP_POLL, "network refuses eDRX: 60-min poll mode (IF-2, PWR-3, F2)");
    CHECK(sb.attach_calls == 4, "attach at boot + polls at 60, 120, 180 min = 4, got %d (IF-2, F2)", sb.attach_calls);
    sb.edrx_grant = 1;
    sb_run(S(4 * 3600 + 600));
    l = last(EV_LINK_STATE);
    int calls = sb.attach_calls;
    CHECK(l && l->e.a == 1 && sb.sleep_mode == SLEEP_EDRX, "eDRX retried at the next attach and taken (IF-2, F2)");
    sb_run(S(8 * 3600));
    CHECK(sb.attach_calls == calls, "eDRX back: the poll stops, no attach in 4 h (IF-2, PWR-3)");

    boot();
    sb.edrx_grant = 0; sb.poll_pending = 1;
    sb_run(S(3700));
    CHECK(sb.uploads == 1 && sb.upload_valid, "a request pending at the 60-min poll is answered (IF-2)");

    boot();
    sb.ltem_ok = 0;
    sb_run(S(600));
    l = last(EV_LINK_STATE);
    CHECK(sb.attach_ltem == 1 && sb.attach_nb == 1 && l && l->e.b == RAT_NBIOT && l->e.a == 1, "LTE-M fails, NB-IoT attached with eDRX (IF-1)");
    boot();
    sb.ltem_ok = 0; sb.nb_ok = 0;
    sb_run(S(3 * 3600));
    CHECK(sb.attach_ltem >= 3 && cnt(EV_LINK_STATE, 0) >= 2, "no RAT: retry LTE-M every poll period, link reported off (IF-1)");
    CHECK(sb.attach_calls <= 7, "no attach storm without coverage: %d attaches in 3 h (PWR-3)", sb.attach_calls);
}

/* ---------------- request latency (IF-2) and fix timeout (F5) */
static double req_elapsed_s(void) { const fwk_sim_rec_t *u = last(EV_UPLOAD_DONE); return u ? u->e.b / 1000.0 : -1; }

static void test_latency(void)
{
    double allow = BOARD_LATENCY_MAX_S + 3 - BOARD_P_EDRX_CYCLE_S - 2.56;     /* 125 s acceptance (PM round 3) */
    boot();
    sb_run(S(60));
    sb.ttff_ms = 0; sb.ttff_assist_ms = 0;                /* no fix at all */
    req_at(100);
    sb_run(S(200));
    double el = req_elapsed_s();
    CHECK(el >= 30.0 && el <= allow, "no fix: answered at %.3f s after the page <= %.2f s (IF-2)", el, allow);
    CHECK(sb.upload_valid == 0, "no fix in 30 s: last known position uploaded with valid=0 (IF-2, F5)");
    CHECK(sb.gnss_max_on_us <= S(BOARD_P_GNSS_FIX_TIMEOUT_S), "GNSS on %.3f s <= %d s fix cap (IF-2, IF-3)", seconds(sb.gnss_max_on_us), BOARD_P_GNSS_FIX_TIMEOUT_S);
    CHECK(81.92 + 2.56 + el <= 125.0, "eDRX 81.92 + PTW 2.56 + %.3f = %.2f s <= 125 s (IF-2)", el, 81.92 + 2.56 + el);

    boot();
    sb_run(S(60));
    sb.ttff_ms = 29800; sb.ttff_assist_ms = 29800; sb.upload_ms = 20000;   /* fix at the last moment, network slow */
    req_at(100);
    sb_run(S(200));
    el = req_elapsed_s();
    CHECK(el <= allow && el >= 39.7, "slow upload cut at the 10 s cap: %.3f s <= %.2f s (IF-2)", el, allow);
    CHECK(sb.aborts >= 1 && last(EV_UPLOAD_DONE)->e.a == 0, "timed-out upload aborted and reported failed (IF-2)");

    boot();
    sb_run(S(60));
    sb.ttff_ms = 0; sb.ttff_assist_ms = 0;
    req_at(100);
    sb_run(S(160));
    sb.ttff_ms = 28000; sb.ttff_assist_ms = 5000;
    req_at(200);
    sb_run(S(260));
    sb.ttff_ms = 0; sb.ttff_assist_ms = 0;
    req_at(700);
    sb_run(S(780));
    CHECK(sb.upload_valid == 0 && sb.upload_age >= 520 && sb.upload_age <= 530,
          "stale fix flagged with its age: valid=%d age=%d s (IF-2, F5)", sb.upload_valid, sb.upload_age);
    CHECK(sb.uploads == 3, "every request answered once (IF-2)");

    /* a request that arrives during the assistance download aborts it (C4: the download has no line in the 125 s) */
    boot();
    sb.assist_ms = 50000;
    req_at(8);
    sb_run(S(120));
    CHECK(sb.assists == 1 && sb.aborts >= 1 && cnt(EV_ASSIST_DONE, 0) == 1, "request during the download aborts it (IF-2, IF-3)");
    CHECK(sb.uploads == 1 && req_elapsed_s() <= allow, "request at 8 s answered in %.2f s <= %.2f s despite the download (IF-2)", req_elapsed_s(), allow);

    /* a second request while the first is running does not start a second fix */
    boot();
    sb_run(S(60));
    req_at(100); req_at(105);
    sb_run(S(200));
    CHECK(sb.gnss_cycles == 1 && sb.uploads == 1, "duplicate request coalesced (IF-2), cycles=%d uploads=%d", sb.gnss_cycles, sb.uploads);
}

/* ---------------- GNSS pins and assistance (IF-3) */
static void test_gnss(void)
{
    boot();
    sb_run(S(60));
    CHECK(sb.gnss_en == 0 && sb.gnss_uart == 0 && sb.gnss_rst_low == 0, "GNSS off at boot: EN low, RX low, RST_N Hi-Z (IF-3)");
    req_at(100); req_at(300);
    sb_run(S(400));
    CHECK(sb.gnss_cycles == 2 && sb.gnss_backpower_viol == 0, "UART never connected while GNSS_EN low: %d violations (IF-3)", sb.gnss_backpower_viol);
    CHECK(sb.gnss_en == 0 && sb.gnss_uart == 0 && sb.gnss_rst_low == 0, "after a fix: EN low, UART off, RST_N Hi-Z (IF-3)");

    /* assistance: first download at boot, then every few days */
    boot();
    sb_run(S(120));
    CHECK(sb.assists == 1, "no stored assistance: one download after attach (IF-3), got %d", sb.assists);
    for (int d = 0; d < 8; d++)
        req_at(3600.0 * 24 * d + 200);
    sb_run(S(3600.0 * 24 * 8.5));
    int n = sb.assists;
    CHECK(n >= 3 && n <= 4, "assistance every %d h: %d downloads in 8.5 days (IF-3, PM round 7 q2)", BOARD_P_ASSIST_PERIOD_H, n);
    CHECK(sb.uploads == 8, "8 daily requests answered (IF-2)");

    /* failed download: retry no sooner than assist_retry_min, no storm */
    boot();
    sb.assist_ok = 0;
    for (int i = 0; i < 20; i++)
        req_at(100 + 600.0 * i);
    sb_run(S(100 + 600 * 20 + 100));
    CHECK(sb.assists <= 1 + (600 * 20) / (BOARD_P_ASSIST_RETRY_MIN * 60) + 1, "failed downloads retried at most every %d min: %d tries (IF-3, PWR-3)", BOARD_P_ASSIST_RETRY_MIN, sb.assists);

    /* with assistance a fix comes in 5 s: shorter GNSS-on time than without (IF-2) */
    boot();
    sb_run(S(60));
    req_at(100); sb_run(S(160));
    uint64_t with = sb.gnss_max_on_us;
    CHECK(seconds(with) < 10.0, "fix with injected assistance ends GNSS in %.1f s (IF-2, IF-3)", seconds(with));
}

/* ---------------- lost mode (PWR-3, F6, IF-9) */
static int sessions_refused(void) { return cnt(EV_LOST_STATE, 2); }
static int req_src(int src) { int n = 0; for (int i = 0; i < fsim.ntrace; i++) if (fsim.trace[i].e.type == EV_POS_REQUEST && fsim.trace[i].e.a == src) n++; return n; }

static void test_lost(void)
{
    boot();
    sb_run(S(60));
    cmd_at(300, MK_LOST_START, 0);
    sb_run(S(300 + 7200 + 600));
    CHECK(req_src(1) == BOARD_LOST_SESSION_FIXES, "lost session: %d fixes (limit %d = 7200/60) (PWR-3, F6)", req_src(1), BOARD_LOST_SESSION_FIXES);
    const fwk_sim_rec_t *end = fwk_sim_find(EV_LOST_STATE, 1);
    CHECK(end && seconds(end->t) <= 7200.0 + 300.0 + 1.0 && seconds(end->t) >= 7199.0 + 300.0 - 1.0 && end->e.a == 3,
          "lost mode stops at 7200 s (PWR-3, F6)");
    CHECK(sb.uploads == BOARD_LOST_SESSION_FIXES, "each lost fix uploaded: %d (PWR-3)", sb.uploads);
    CHECK(fwk_dropped() == 0, "no event dropped in a lost session (PWR-3)");

    boot();
    sb_run(S(60));
    cmd_at(300, MK_LOST_START, 0);
    cmd_at(1000, MK_LOST_STOP, 0);
    sb_run(S(3000));
    CHECK(req_src(1) >= 11 && req_src(1) <= 13 && cnt(EV_LOST_STATE, 3) == 1, "owner cancel at 1000 s ends it (%d fixes) (PWR-3, F6)", req_src(1));

    /* monthly quota */
    boot();
    sb_run(S(60));
    double t = 100;
    for (int i = 0; i < BOARD_MAX_LOST_SESSIONS_PER_MONTH + 1; i++) {
        sb.day = i;
        cmd_at(t, MK_LOST_START, 0);
        cmd_at(t + 20, MK_LOST_STOP, 0);
        sb_run(S(t + 40));
        t += 100;
    }
    CHECK(cnt(EV_LOST_STATE, 1) == BOARD_MAX_LOST_SESSIONS_PER_MONTH && sessions_refused() == 1,
          "%d sessions allowed, the next refused (limit %d per month) (PWR-3, F6)", cnt(EV_LOST_STATE, 1), BOARD_MAX_LOST_SESSIONS_PER_MONTH);
    sb.day = 40;
    cmd_at(t, MK_LOST_START, 0); cmd_at(t + 20, MK_LOST_STOP, 0);
    sb_run(S(t + 40));
    CHECK(cnt(EV_LOST_STATE, 1) == BOARD_MAX_LOST_SESSIONS_PER_MONTH + 1, "quota window slides after 30 days (PWR-3)");
    CHECK(sb.nquota >= 1 && sb.quota_saves >= 7, "session starts stored for the next boot (PWR-3)");

    /* quota survives a reset */
    boot();
    memcpy(sb.quota, (int32_t[]){0, 1, 2, 3, 4, 5}, sizeof(int32_t) * 6); sb.nquota = 6; sb.day = 6;
    cmd_at(100, MK_LOST_START, 0);
    sb_run(S(200));
    CHECK(sessions_refused() == 1 && req_src(1) == 0, "6 sessions in storage: refused after reboot (PWR-3, F6)");

    /* motion gating (IF-9): once the sensor proved itself, a still collar reports every lost_still_max_s */
    boot();
    sb_run(S(60));
    cmd_at(300, MK_LOST_START, 0);
    fwk_sim_inject_at(S(330), ev_accel_irq(0));
    sb_run(S(300 + 7200 + 60));
    int n = req_src(1);
    CHECK(n >= 10 && n < 30, "still collar: %d fixes instead of 120 (IF-9, PWR-3)", n);
    CHECK(sb.accel_arms >= 2, "accelerometer re-armed after the IRQ (IF-9)");
    /* never moved: no motion proof, no gating (a dead sensor must not cost fixes) */
    boot();
    sb_run(S(60));
    cmd_at(300, MK_LOST_START, 0);
    sb_run(S(300 + 1800));
    CHECK(req_src(1) >= 30, "no MOTION ever seen: 1 fix/min stays (IF-9, PWR-3), %d fixes in 30 min", req_src(1));
}

/* ---------------- UI (IF-6, IF-7) */
static void test_ui(void)
{
    /* LED worst case: low battery + no link = 3 pulses per period */
    boot();
    sb.ltem_ok = 0; sb.nb_ok = 0; sb.cell_idle_mv = 2300;
    sb_run(S(3605));
    double ua = sb.q_led / 3605.0;
    CHECK(cnt(EV_LOW_BATT, ANY) >= 0 && ua <= 2.0, "LED average %.2f uA <= 2 uA, worst pattern (IF-6)", ua);
    CHECK(sb.led_pulses >= 3 * 340, "LED pulses %d in 1 h (IF-6)", sb.led_pulses);
    CHECK(!sb.led_on, "LED off between pulses (IF-6)");
    boot();
    sb_run(S(3605));
    CHECK(sb.q_led / 3605.0 <= 0.55, "LED normal pattern %.2f uA (1 ms / 10 s at 5 mA = 0.5) (IF-6)", sb.q_led / 3605.0);

    /* button: debounce, bounce rejected, short press shows status */
    boot();
    sb_run(S(60));
    uint64_t on0 = sb.led_on_us;
    sb_run(S(102));
    on0 = sb.led_on_us;
    fwk_sim_inject_at(S(103), ev_btn_edge(0));
    fwk_sim_inject_at(S(103.005), ev_btn_edge(1));       /* bounce: pin reads high at the debounce time */
    sb.btn_level = 1;
    sb_run(S(104));
    uint64_t bounce = sb.led_on_us - on0;
    CHECK(bounce <= 3000, "bounce under %d ms ignored, LED %.1f ms (IF-6)", BOARD_P_BTN_DEBOUNCE_MS, bounce / 1000.0);
    sb.btn_level = 0;
    fwk_sim_inject_at(S(113), ev_btn_edge(0));
    fwk_sim_inject_at(S(113.5), ev_btn_edge(1));
    on0 = sb.led_on_us;
    sb_run(S(115));
    CHECK(sb.led_on_us - on0 >= (uint64_t)BOARD_P_BTN_STATUS_MS * 1000, "short press shows status: LED %.0f ms (IF-6)", (sb.led_on_us - on0) / 1000.0);

    /* long press cancels lost mode */
    boot();
    sb_run(S(60));
    cmd_at(100, MK_LOST_START, 0);
    sb.btn_level = 0;
    fwk_sim_inject_at(S(200), ev_btn_edge(0));
    fwk_sim_inject_at(S(210), ev_btn_edge(1));
    sb_run(S(300));
    CHECK(cnt(EV_LOST_STATE, 3) == 1 && seconds(fwk_sim_find(EV_LOST_STATE, 1)->t) < 206.0,
          "hold %d s cancels lost mode (IF-6)", BOARD_P_BTN_CANCEL_LOST_S);

    /* buzzer: remote trigger, 10 s then off, antiphase by the PWM block, never DC */
    boot();
    sb_run(S(60));
    cmd_at(100, MK_REQ_BUZZER, 10);
    sb_run(S(105));
    CHECK(sb_buz_active() && sb.buz_starts == 1, "remote buzzer start (IF-7)");
    sb_run(S(130));
    CHECK(!sb_buz_active(), "buzzer off after the 10 s pattern (IF-7, F3)");
    CHECK(sb.buz_on_us >= S(9.9) && sb.buz_on_us <= S(10.1), "buzzer ran %.2f s (IF-7)", seconds(sb.buz_on_us));
    cmd_at(200, MK_REQ_BUZZER, 600);
    sb_run(S(300));
    CHECK(sb.buz_max_run_us <= S(BOARD_P_BUZZER_ON_S) + 1000, "a 600 s command is cut to %d s: longest run %.2f s <= buzzer_hw_cap %d s (IF-7, F3)",
          BOARD_P_BUZZER_ON_S, seconds(sb.buz_max_run_us), BOARD_BUZZER_HW_CAP_S);
    cmd_at(400, MK_REQ_BUZZER, 10);
    cmd_at(403, MK_REQ_BUZZER, 0);
    sb_run(S(420));
    CHECK(sb.buz_max_run_us <= S(BOARD_P_BUZZER_ON_S) && !sb_buz_active(), "owner can stop the buzzer early (IF-7)");
    double uA = (30.0 * 10 * 2) * 1000.0 / (30 * 86400.0);   /* 30 mA x 10 s x 2 per month, in uA */
    CHECK(uA <= 2.0, "buzzer average %.2f uA <= 2 uA at 2 uses a month (IF-7)", uA);
    CHECK(sb.buz_stop_calls >= 1, "buzzer legs low after use (IF-7, F3)");
}

/* ---------------- watchdog and hang (PWR-3, PWR-4, F1, F3) */
static int rogue(fwk_task_t *t, const fwk_event_t *e)
{
    (void)t;
    if (e->type == EV_MOTION)
        fwk_sim_advance(S(150));                         /* a module that blocks: the scheduler stalls */
    return FWK_WAIT;
}
static fwk_task_t t_rogue = {"rogue", rogue, 0, 1ull << EV_MOTION, 0, 0, 0};

static void test_wdt(void)
{
    boot();
    sb_run(S(3600));
    CHECK(sb.dones == 60, "DONE answered every WAKE: %d in 1 h (PWR-3)", sb.dones);
    CHECK(sb.tpl_missed == 0 && !fsim.wdt_reset, "no missed DONE in normal operation, max gap %.1f s (PWR-3)", seconds(fsim.max_feed_gap));
    CHECK(fsim.max_feed_gap < S(BOARD_P_WDT_PERIOD_S), "feed gap %.2f s < watchdog period %d s (PWR-3)", seconds(fsim.max_feed_gap), BOARD_P_WDT_PERIOD_S);

    /* hang while the buzzer plays: the PWM sequence is finite (peripheral), the TPL5010 sees no DONE */
    boot();
    memset(&t_rogue.lc, 0, sizeof t_rogue.lc);
    fwk_add_task(&t_rogue);
    sb_run(S(70));
    cmd_at(100, MK_REQ_BUZZER, 10);
    fwk_sim_inject_at(S(101), ev_accel_irq(0));
    sb_run(S(400));
    CHECK(sb.tpl_missed >= 1, "hung scheduler: TPL5010 gets no DONE (PWR-3, PWR-4, F1)");
    CHECK(sb.buz_max_run_us <= S(BOARD_P_BUZZER_ON_S) + 1000, "hung MCU: buzzer PWM sequence ends by itself after %.1f s <= %d s (IF-7, F3)",
          seconds(sb.buz_max_run_us), BOARD_BUZZER_HW_CAP_S);
    double reset_after = seconds(sb.tpl_first_missed_wake_us) + BOARD_P_WDT_PERIOD_S - 101.0;
    CHECK(reset_after <= BOARD_BUZZER_HW_CAP_S && reset_after <= 2.0 * BOARD_P_WDT_PERIOD_S, "TPL5010 reset %.0f s after the hang <= two watchdog periods (PWR-3, F1)", reset_after);
}


static double epo_q_dl, epo_q_no, fota_q_c;

/* ---------------- C14/C15/C16/C4: EPO refresh, buzzer sequence plan, low battery, brown-out count, FOTA */
static void reboot(int cause)
{
    fwk_sim_reset((uint64_t)BOARD_P_WDT_PERIOD_S * 1000000ull);
    sb_reboot(cause);
    app_init();
}
static void offer_at(double t, int kb) { fwk_sim_inject_at(S(t), ev_modem_evt(MK_FOTA_OFFER, kb)); }
static double tip_s(double r_ohm)   /* TPL5010 SNAS651A Eq. 3, coefficient set 3 (10 s < T <= 100 s) */
{
    double rd = r_ohm / 100.0;
    return (0.1972 * rd * rd - 19.3450 * rd + 692.1201) / 100.0;
}

static void test_buzzer_plan(void)
{
    uint32_t refresh = 0, loop = 0;
    CHECK(buz_pwm_plan(BOARD_P_BUZ_FREQ_HZ, BOARD_P_BUZZER_ON_S * 1000u, &refresh, &loop) && (refresh + 1) * loop == 40000u,
          "10 s at 4 kHz = 40000 periods = (REFRESH %u + 1) x LOOP %u (IF-7, F3)", refresh, loop);
    CHECK(loop <= 65535u && refresh <= 0xFFFFFFu, "LOOP.CNT %u fits 16 bit, REFRESH %u fits 24 bit: one finite sequence, no CPU (IF-7, F3, C15)", loop, refresh);
    CHECK(!buz_pwm_plan(BOARD_P_BUZ_FREQ_HZ, BOARD_P_BUZZER_ON_S * 1000u + 1, &refresh, &loop), "no plan longer than %d s (IF-7)", BOARD_P_BUZZER_ON_S);
    CHECK(!buz_pwm_plan(0, 1000, &refresh, &loop) && !buz_pwm_plan(4000, 0, &refresh, &loop), "no plan for 0 Hz or 0 s (IF-7)");
    /* the 16 s limit of the old note (65535 periods) is not the limit: 65535 x 100 periods = 1638 s at 4 kHz */
    CHECK(65535.0 * 100 / BOARD_P_BUZ_FREQ_HZ > BOARD_P_BUZZER_ON_S, "LOOP range %.0f s >= %d s (IF-7)", 65535.0 * 100 / BOARD_P_BUZ_FREQ_HZ, BOARD_P_BUZZER_ON_S);
    boot();
    sb_run(S(60));
    cmd_at(100, MK_REQ_BUZZER, 30);                      /* the server asks 30 s: clamped to 10 s */
    sb_run(S(160));
    CHECK(sb.buz_starts == 1 && sb.buz_overcap == 0 && sb.buz_max_run_us <= S(BOARD_P_BUZZER_ON_S) + 1000,
          "30 s request played as ONE sequence of %.1f s <= %d s (IF-7, F3)", seconds(sb.buz_max_run_us), BOARD_P_BUZZER_ON_S);
    /* C15 restated: TPL5010 R7 22.0 kohm 1 %: tIP max, then the reset comes at the end of the interval after the missed WAKE */
    double tmax = tip_s(22000 * 1.01) * (1 + 0.006 + 0.005 + 0.0024 + 0.0004 * 60);   /* setting, oscillator, lifetime, 60 C span at 400 ppm/C */
    CHECK(tip_s(22000) > 59.0 && tip_s(22000) < 60.5, "R7 22 kohm gives tIP %.1f s, the 60 s target (PWR-3, C15)", tip_s(22000));
    CHECK(2 * tmax <= BOARD_BUZZER_HW_CAP_S, "worst reset 2 x %.1f = %.1f s <= buzzer_hw_cap_s %d (IF-7, C15)", tmax, 2 * tmax, BOARD_BUZZER_HW_CAP_S);
}

static double lost_session_q(int age0, int assist_first_fails, int *hot, int *cold, int *fixes, int *assists)
{
    boot();
    sb.assist_age_h0 = age0;
    sb.assist_ok = assist_first_fails ? 0 : 1;
    sb_run(S(600));
    sb.assist_ok = assist_first_fails == 2 ? 0 : 1;      /* 2: the download keeps failing (cold fixes); 1: network back at the session */
    double q0 = sb.q_uc;
    int h0 = sb.hot_fixes, c0 = sb.cold_fixes;
    cmd_at(700, MK_LOST_START, 0);
    sb_run(S(700 + 7200 + 600));
    *hot = sb.hot_fixes - h0; *cold = sb.cold_fixes - c0; *fixes = app_lost()->fixes; *assists = sb.assists;
    return sb.q_uc - q0;
}

static void test_epo(void)
{
    /* refresh before it expires */
    boot();
    sb_run(S(3600.0 * 24 * 10));
    CHECK(sb.assists >= 4 && sb.assists <= 5, "10 days, refresh every %d h: %d downloads (IF-3, C14)", BOARD_P_ASSIST_PERIOD_H, sb.assists);
    CHECK(sb.max_assist_age_h <= BOARD_P_EPO_VALID_H - BOARD_P_EPO_REFRESH_LEAD_H + 1 && sb.max_assist_age_h < BOARD_P_EPO_VALID_H,
          "file never older than %d h at the hourly check, valid %d h (IF-3, C14)", sb.max_assist_age_h, BOARD_P_EPO_VALID_H);
    CHECK(BOARD_P_ASSIST_PERIOD_H + BOARD_P_EPO_REFRESH_LEAD_H == BOARD_P_EPO_VALID_H, "refresh period %d h = validity %d h - 12 h lead (IF-3)", BOARD_P_ASSIST_PERIOD_H, BOARD_P_EPO_VALID_H);
    CHECK(assist_covers(0, 2) && assist_covers(70, 2) && !assist_covers(71, 2) && !assist_covers(-1, 2), "a 2 h lost session needs a file covering it (IF-3, PWR-3)");
    CHECK(assist_pays_off(BOARD_LOST_SESSION_FIXES) && !assist_pays_off(1), "download %d mC pays off over %d fixes, not over 2 (C14)", BOARD_P_ASSIST_DL_MC, BOARD_LOST_SESSION_FIXES);
    CHECK(BOARD_P_EPO_FILE_BYTES % 2304 == 0, "EPO file %d B = whole 2304 B sets of 6 h (IF-3)", BOARD_P_EPO_FILE_BYTES);

    int hot, cold, fixes, assists;
    double q_dl = lost_session_q(100, 1, &hot, &cold, &fixes, &assists);            /* expired at the start, download at the session */
    CHECK(assists == 2 && cold == 0 && hot == fixes, "expired file: downloaded at session start, all %d fixes hot (cold %d) (IF-3, C14)", fixes, cold);
    int hot2, cold2, fixes2, assists2;
    double q_no = lost_session_q(100, 2, &hot2, &cold2, &fixes2, &assists2);        /* the download fails: cold fixes */
    CHECK(cold2 >= fixes2 - 1, "no download possible: %d cold fixes of %d (C14)", cold2, fixes2);
    CHECK(q_dl < q_no, "session with download %.1f C < without %.1f C (PWR-3, C14)", q_dl / 1e6, q_no / 1e6);
    CHECK((q_no - q_dl) / 1e6 > 30.0, "download saves %.1f C per session against %.2f C paid (C14)", (q_no - q_dl) / 1e6, BOARD_P_ASSIST_DL_MC / 1000.0);
    printf("epo: lost session expired file with download %.1f C, without %.1f C\n", q_dl / 1e6, q_no / 1e6);
    /* worst month, 6 sessions, each starting expired, poll mode (PWR-3) */
    double poll_day = 103.7;       /* from test_energy below (printed there); recomputed there as well */
    (void)poll_day;
    epo_q_dl = q_dl; epo_q_no = q_no;
}

static void test_lowbatt(void)
{
    CHECK(low_batt_decide(2599, 0) && !low_batt_decide(2600, 0) && low_batt_decide(3000, 3) && !low_batt_decide(3000, 2),
          "low battery: idle < %d mV or %d sags (PWR-6, C16)", BOARD_P_LOW_BATT_IDLE_MV, BOARD_P_LOW_BATT_SAG_COUNT);
    CHECK(!low_batt_clear(2699) && low_batt_clear(2700), "cleared with %d mV hysteresis (PWR-6)", BOARD_P_TX_HYST_MV);
    double need_mah = 14 * 24 * 217.7e-3;       /* 14 days at the worst-month poll average (PWR-3 test below prints 217-221 uA) */
    CHECK(need_mah > 70 && need_mah < 80, "14 days at the worst-month average need %.1f mAh above the threshold (PWR-6, C16)", need_mah);
    boot();
    sb_run(S(60));
    sb.cell_idle_mv = 2650;
    req_at(100); sb_run(S(200));
    CHECK(cnt(EV_LOW_BATT, ANY) == 0, "idle 2650 mV: no low-battery report (PWR-6)");
    sb.cell_idle_mv = 2550;
    req_at(300); req_at(400); sb_run(S(500));
    CHECK(cnt(EV_LOW_BATT, ANY) >= 1 && sb.upload_low == 1, "idle 2550 mV: LOW_BATT and the flag in the upload (PWR-6)");
    boot();
    sb_run(S(60));
    sb.cell_loaded_mv = 2300;                    /* sag under every burst, idle voltage flat (Li-FeS2 end of life) */
    req_at(100); req_at(200); sb_run(S(300));
    CHECK(cnt(EV_LOW_BATT, ANY) == 0, "2 sagging bursts: not yet (PWR-6, C16)");
    req_at(400); sb_run(S(500));
    CHECK(cnt(EV_LOW_BATT, ANY) >= 1, "3 sagging bursts at 20 C: LOW_BATT with the idle voltage still 3200 mV (PWR-6, C16)");
    boot();
    sb.temp_c10 = -100;
    sb_run(S(60));
    sb.cell_loaded_mv = 2300;
    for (int i = 0; i < 5; i++) req_at(100 + 100.0 * i);
    sb_run(S(700));
    CHECK(cnt(EV_LOW_BATT, ANY) == 0 && sb.uploads >= 5, "cold alkaline sag (-10 C, 5 bursts) is not a low battery (PWR-7, C16)");
}

static void test_brownout(void)
{
    boot();
    sb.reset_cause = RESET_BROWNOUT;
    sb_run(S(300));
    CHECK(sb.t_first_attach_us >= S(60) && sb.t_first_attach_us < S(70),
          "first brown-out: attach after %.0f s backoff (PWR-7, F4)", seconds(sb.t_first_attach_us));
    /* the attach succeeded and cleared the counter: see below for the loop */
    boot();
    sb.reset_cause = RESET_BROWNOUT;
    sb.vdd_full_at_us = S(100000);               /* the rail never recovers: the loop continues */
    sb_run(S(30));
    CHECK(sb.brownout_ret == 1, "retained counter 1 after the first brown-out boot (PWR-7, F4)");
    reboot(RESET_BROWNOUT);
    sb.vdd_full_at_us = 0;
    sb_run(S(300));
    CHECK(sb.t_first_attach_us >= S(120) && sb.t_first_attach_us < S(130),
          "second brown-out boot: backoff doubled to 120 s, attach at %.0f s (retained across the reset, PWR-7, F4)", seconds(sb.t_first_attach_us));
    CHECK(sb.brownout_ret == 0, "a good attach clears the retained counter (PWR-7)");
    boot();
    sb.reset_cause = RESET_BROWNOUT; sb.vdd_full_at_us = S(100000);
    sb_run(S(10));
    reboot(RESET_BROWNOUT); sb.vdd_full_at_us = S(100000); sb_run(S(10));
    reboot(RESET_WATCHDOG); sb.vdd_full_at_us = S(100000); sb_run(S(10));
    CHECK(sb.brownout_ret == 2, "a watchdog reset keeps the count (%d) (PWR-7)", sb.brownout_ret);
    reboot(RESET_BROWNOUT); sb.vdd_full_at_us = 0;
    sb_run(S(400));
    CHECK(sb.t_first_attach_us >= S(240) && sb.t_first_attach_us < S(250), "third brown-out: backoff 240 s, attach at %.0f s (PWR-7, F4)", seconds(sb.t_first_attach_us));
    reboot(RESET_BROWNOUT); sb.vdd_full_at_us = S(100000); sb_run(S(10));
    reboot(RESET_POWERON); sb.vdd_full_at_us = 0;
    sb_run(S(60));
    CHECK(sb.brownout_ret == 0 && sb.t_first_attach_us < S(30), "power-on reset (cell swap) clears the count and attaches without backoff (PWR-7)");
}

static void test_fota(void)
{
    uint32_t total = 300u * 1024u;
    /* plain update: 300 kB in 16 kB chunks, gate before each */
    boot();
    sb_run(S(60));
    offer_at(100, 300);
    sb_run(S(300));
    CHECK(sb.fota_fetches == 19 && sb.fota_bytes == total && sb.fota_applies == 1 && cnt(EV_FOTA_STATE, 3) == 1,
          "300 kB in %d chunks of %d kB, applied once (IF-8)", sb.fota_fetches, BOARD_P_FOTA_CHUNK_KB);
    CHECK(sb.fota_fetch_low_vdd == 0 && sb.tx_while_gate_low == 0, "every chunk passed the supercap gate (IF-8, PWR-7)");
    double q_fota = sb.q_fota / 1e6;
    CHECK(q_fota < 2.0, "FOTA charge %.2f C battery side <= 2 C (IF-8, C4: architecture 1.2 C + set-up)", q_fota);
    CHECK(BOARD_FOTA_IMAGE_KB <= BOARD_FOTA_SLOT_KB && fota_size_ok(BOARD_FOTA_SLOT_KB) && !fota_size_ok(BOARD_FOTA_SLOT_KB + 1) && !fota_size_ok(0),
          "image %d kB fits the %d kB slot, larger refused (IF-8)", BOARD_FOTA_IMAGE_KB, BOARD_FOTA_SLOT_KB);
    CHECK(2 * BOARD_FOTA_SLOT_KB + 48 + 80 + 16 <= 1024, "flash map: 48 + 2 x %d + 80 + 16 = %d kB <= 1024 kB (IF-8)", BOARD_FOTA_SLOT_KB, 2 * BOARD_FOTA_SLOT_KB + 144);
    printf("fota: %d chunks, %.2f C\n", sb.fota_fetches, q_fota);
    fota_q_c = q_fota;

    /* new image boots: confirm after the attach, or revert */
    reboot(RESET_POWERON);
    sb_run(S(120));
    CHECK(sb.fota_confirms == 1 && sb.fota_reverts == 0 && cnt(EV_FOTA_STATE, 4) == 1, "new image confirmed after the first attach (IF-8)");
    reboot(RESET_POWERON);
    sb.fota_pending_confirm = 1; sb.ltem_ok = 0; sb.nb_ok = 0;
    sb_run(S(BOARD_P_FOTA_CONFIRM_S + 100));
    CHECK(sb.fota_confirms == 0 && sb.fota_reverts == 1 && cnt(EV_FOTA_STATE, 5) == 1, "no attach within %d s: image not confirmed, revert (IF-8)", BOARD_P_FOTA_CONFIRM_S);

    /* chunk failure: retry, resume at the same offset */
    boot();
    sb_run(S(60));
    sb.fota_fail_chunk = 4;
    offer_at(100, 300);
    sb_run(S(600));
    CHECK(sb.fota_fetches == 20 && sb.fota_bytes == total && sb.fota_applies == 1, "one failed chunk retried: %d fetches, all %u B, applied (IF-8)", sb.fota_fetches, sb.fota_bytes);

    /* reset in the middle: resume from the retained offset */
    boot();
    sb_run(S(60));
    offer_at(100, 300);
    sb_run(S(112));
    uint32_t ret = sb.fota_ret_off;
    CHECK(ret > 0 && ret < total, "download in progress at the reset: retained offset %u B (IF-8)", ret);
    reboot(RESET_WATCHDOG);
    offer_at(30, 300);
    sb_run(S(200));
    CHECK(sb.fota_first_off == ret && sb.fota_applies == 1 && sb.fota_bytes == total - ret,
          "resume at the retained offset %u B after a reset, only %u B fetched (IF-8)", sb.fota_first_off, sb.fota_bytes);

    /* a locate request outranks the download (IF-2) */
    boot();
    sb_run(S(60));
    offer_at(100, 300);
    req_at(112);
    sb_run(S(130));
    int n_req = fsim.ntrace;
    (void)n_req;
    const fwk_sim_rec_t *u = last(EV_UPLOAD_DONE);
    CHECK(u && u->e.a == 1 && sb.fota_aborts >= 1, "request during FOTA: chunk aborted, request answered (IF-2, IF-8)");
    uint64_t t_pos = 0, t_up = 0;
    for (int i = 0; i < fsim.ntrace; i++) {
        if (fsim.trace[i].e.type == EV_POS_REQUEST && fsim.trace[i].e.a == 0 && !t_pos) t_pos = fsim.trace[i].t;
        if (fsim.trace[i].e.type == EV_UPLOAD_DONE && t_pos && !t_up) t_up = fsim.trace[i].t;
    }
    int in_window = 0;
    for (int i = 0; i < sb.fota_fetches && i < 64; i++)
        if (sb.fota_fetch_us[i] > t_pos && sb.fota_fetch_us[i] < t_up) in_window++;
    CHECK(t_pos && t_up && in_window == 0, "no FOTA chunk between the request (%.1f s) and its upload (%.1f s) (IF-2, IF-8)", seconds(t_pos), seconds(t_up));
    CHECK(seconds(t_up - t_pos) <= 125.0 - 81.92 - 2.56, "request answered %.2f s after the page despite the download <= 40.52 s (IF-2)", seconds(t_up - t_pos));
    sb_run(S(400));
    CHECK(sb.fota_applies == 1 && sb.fota_bytes >= total, "download resumed after the request and completed (IF-8)");

    /* below the low-battery level: no start; mid-download: stop */
    boot();
    sb_run(S(60));
    sb.cell_idle_mv = 2500;
    sb_run(S(3700));                                       /* hourly sample reports LOW_BATT */
    offer_at(3800, 300);
    sb_run(S(3900));
    CHECK(cnt(EV_LOW_BATT, ANY) >= 1 && sb.fota_fetches == 0, "low battery: FOTA not started (IF-8, C16)");
    sb.cell_idle_mv = 3200;
    sb_run(S(3 * 3600 + 400));
    CHECK(sb.fota_applies == 1, "battery back above %d mV: FOTA resumes and completes (IF-8)", BOARD_P_LOW_BATT_IDLE_MV + BOARD_P_TX_HYST_MV);
    boot();
    sb_run(S(60));
    offer_at(100, 300);
    sb_run(S(105));
    sb.cell_idle_mv = 2500;
    sb_run(S(200));
    CHECK(sb.fota_fetches >= 1 && sb.fota_fetches < 19 && sb.fota_applies == 0, "battery drops mid-download: stops after %d chunks (IF-8, C16)", sb.fota_fetches);

    /* supercap gate fails: no chunk until it recovers */
    boot();
    sb_run(S(60));
    sb.vdd_dip_from_us = S(105); sb.vdd_dip_to_us = S(150);
    offer_at(100, 300);
    sb_run(S(400));
    int in_dip = 0;
    for (int i = 0; i < sb.fota_fetches && i < 64; i++)
        if (sb.fota_fetch_us[i] >= S(105) && sb.fota_fetch_us[i] < S(150)) in_dip++;
    CHECK(in_dip == 0 && sb.fota_fetch_low_vdd == 0 && sb.fota_applies == 1, "supercap below %d mV for 45 s: no chunk in the dip, then completes (IF-8, PWR-7)", BOARD_ATTACH_MIN_VBAT_MV);

    /* image larger than the slot is refused */
    boot();
    sb_run(S(60));
    offer_at(100, BOARD_FOTA_SLOT_KB + 1);
    sb_run(S(200));
    CHECK(sb.fota_fetches == 0 && cnt(EV_FOTA_STATE, 6) == 1, "image over the slot refused (IF-8)");
    CHECK(fwk_dropped() == 0, "no event dropped in the FOTA cases (IF-8)");
}

/* ---------------- simulated days and energy (PWR-2, PWR-3, PWR-5) */
/* Battery-side currents that no HAL call shows, from architecture.md section 4 (modem side x boost factor): eDRX 81.92 s
 * Cat-M1 28.8 uA (incl. PSM floor and UICC), PSM-only floor 2.7 x 1.60 = 4.3 uA, rest of the floor 4.6 uA, boost Iq 1.0 uA,
 * TAU 12 h 1.3 uA. */
#define UA_EDRX   28.8
#define UA_PSM    4.3
#define UA_REST   4.6
#define UA_BOOST  1.0
#define UA_TAU    1.3
#define N_REQ_DAY 2

static double day_charge_uc(int edrx, int days)
{
    boot();
    sb.edrx_grant = edrx;
    for (int d = 0; d < days; d++)
        for (int k = 0; k < N_REQ_DAY; k++)
            req_at(86400.0 * d + 30000.0 + 20000.0 * k);
    if (!edrx)
        for (int d = 0; d < days; d++)                  /* each request is announced at the poll: reply pending */
            ;
    sb_run(S(86400.0 * days));
    CHECK(fwk_dropped() == 0, "no event dropped in %d simulated days, eDRX=%d (IF-2)", days, edrx);
    CHECK(!fsim.wdt_reset && sb.tpl_missed == 0, "%d days eDRX=%d: watchdog fed, max gap %.2f s (PWR-3)", days, edrx, seconds(fsim.max_feed_gap));
    return sb.q_uc / days;
}

static void test_energy(void)
{
    double q_edrx = day_charge_uc(1, 3);
    CHECK(sb.uploads == 3 * N_REQ_DAY, "3 days eDRX: %d uploads for %d requests (IF-2)", sb.uploads, 3 * N_REQ_DAY);
    CHECK(fwk_sim_count(EV_UPLOAD_DONE) == 3 * N_REQ_DAY, "every request closed by one UPLOAD_DONE (IF-2)");
    CHECK(sb.assists >= 1 && sb.assists <= 2, "3 days: %d assistance downloads (IF-3)", sb.assists);
    CHECK(fwk_sim_count(EV_WDT_WAKE_IRQ) >= 3 * 1440 - 3, "TPL5010 WAKE serviced %d times in 3 days (PWR-3)", fwk_sim_count(EV_WDT_WAKE_IRQ));
    double floor_edrx = UA_EDRX + UA_REST + UA_BOOST + UA_TAU;
    double day_ua_edrx = floor_edrx + q_edrx / 86400.0;

    double q_poll = day_charge_uc(0, 3);
    double floor_poll = UA_PSM + UA_REST + UA_BOOST + UA_TAU;
    double day_ua_poll = floor_poll + q_poll / 86400.0;
    /* the poll attach is the 0.28 C of architecture.md: 24 per day dominate */
    CHECK(sb.attach_calls >= 3 * 24 - 2, "poll mode: %d attaches in 3 days (IF-2)", sb.attach_calls);

    /* lost-mode session (2 h, 120 fixes) */
    boot();
    sb_run(S(600));
    double q0 = sb.q_uc;
    cmd_at(700, MK_LOST_START, 0);
    sb_run(S(700 + 7200 + 600));
    double q_lost = sb.q_uc - q0;                       /* includes ~2.2 h of LED: small */
    CHECK(fwk_dropped() == 0, "no event dropped in a lost session (PWR-3)");

    int n = BOARD_MAX_LOST_SESSIONS_PER_MONTH;
    double month_s = 30 * 86400.0;
    double lost_ua = n * q_lost / month_s;
    double avg_edrx = day_ua_edrx + lost_ua, avg_poll = day_ua_poll + lost_ua;
    CHECK(avg_edrx <= BOARD_AVG_BATT_UA_MAX_EDRX, "eDRX mode, %d lost sessions/month: %.1f uA <= %d uA (PWR-3)", n, avg_edrx, BOARD_AVG_BATT_UA_MAX_EDRX);
    CHECK(avg_edrx <= BOARD_AVG_BATT_UA_MAX_PWR2, "eDRX mode: %.1f uA <= %d uA (PWR-2)", avg_edrx, BOARD_AVG_BATT_UA_MAX_PWR2);
    CHECK(avg_poll <= BOARD_AVG_BATT_UA_MAX_EDRX, "poll fallback, %d lost sessions/month: %.1f uA <= %d uA (PWR-3)", n, avg_poll, BOARD_AVG_BATT_UA_MAX_EDRX);
    CHECK(avg_poll <= BOARD_AVG_BATT_UA_MAX_PWR2, "poll fallback: %.1f uA <= %d uA (PWR-2)", avg_poll, BOARD_AVG_BATT_UA_MAX_PWR2);
    double one_lost = (day_ua_edrx + q_lost / month_s);
    CHECK(one_lost <= BOARD_AVG_BATT_UA_MAX_EDRX, "one lost session a month, eDRX: %.1f uA (PWR-3)", one_lost);
    CHECK(floor_edrx - UA_EDRX + UA_PSM <= BOARD_SLEEP_FLOOR_UA_MAX, "sleep floor line %.1f uA with the PSM modem <= %d uA (PWR-5, 50 C)", floor_edrx - UA_EDRX + UA_PSM, BOARD_SLEEP_FLOOR_UA_MAX);
    /* C14: worst month with every session starting on an expired file (download at the start), and C4: one FOTA a year */
    double q_exp = epo_q_dl;
    double fota_ua = fota_q_c * 1e6 * BOARD_P_FOTA_PER_YEAR / (365.0 * 86400.0);
    double worst_poll = day_ua_poll + n * q_exp / month_s + fota_ua, worst_edrx = day_ua_edrx + n * q_exp / month_s + fota_ua;
    double nodl_poll = day_ua_poll + n * epo_q_no / month_s;
    CHECK(worst_poll <= BOARD_AVG_BATT_UA_MAX_EDRX, "worst month: %d expired-file sessions + download each + 1 FOTA/yr, poll mode: %.1f uA <= %d uA (PWR-3, C14)", n, worst_poll, BOARD_AVG_BATT_UA_MAX_EDRX);
    CHECK(worst_edrx <= BOARD_AVG_BATT_UA_MAX_EDRX, "same in eDRX mode: %.1f uA (PWR-3, C14)", worst_edrx);
    CHECK(fota_ua < 1.0, "FOTA %.0f per year of %.2f C = %.3f uA (PWR-3, IF-8, C4)", (double)BOARD_P_FOTA_PER_YEAR, fota_q_c, fota_ua);
    CHECK(nodl_poll > worst_poll, "without the session-start download the worst month would be %.1f uA (C14)", nodl_poll);
    printf("energy worst month: poll %.1f eDRX %.1f uA (expired-file session %.1f C, FOTA %.3f uA); without download poll %.1f uA\n", worst_poll, worst_edrx, q_exp / 1e6, fota_ua, nodl_poll);
    printf("energy: eDRX day %.1f uA, poll day %.1f uA, lost session %.1f C = %.1f uA/month each; month avg eDRX %.1f poll %.1f\n",
           day_ua_edrx, day_ua_poll, q_lost / 1e6, q_lost / month_s, avg_edrx, avg_poll);
}

int main(void)
{
    test_lib();
    test_gate();
    test_txpower();
    test_link();
    test_latency();
    test_gnss();
    test_lost();
    test_ui();
    test_wdt();
    test_buzzer_plan();
    test_epo();
    test_lowbatt();
    test_brownout();
    test_fota();
    test_energy();
    printf("%d checks, %d failed\n", fwk_checks, fwk_fails);
    return fwk_fails ? 1 : 0;
}
