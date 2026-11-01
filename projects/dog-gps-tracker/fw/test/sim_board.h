/* Simulated tracker board for the host tests: modem, LC76G, battery, LED, buzzer, TPL5010, quota storage.
 * Numbers come from notes/ and architecture.md section 4 (named in sim_board.c). */
#ifndef SIM_BOARD_H
#define SIM_BOARD_H
#include <stdint.h>
#include "fwk_sim.h"

typedef struct {
    /* ---- knobs */
    int vdd_low_mv, vdd_full_mv; uint64_t vdd_full_at_us;      /* VDD_4V0 as AT%XVBAT sees it: low until vdd_full_at_us */
    int cell_idle_mv, cell_loaded_mv, temp_c10;
    int ltem_ok, nb_ok, edrx_grant, upload_ok, assist_ok, poll_pending;
    uint32_t attach_ms, upload_ms, assist_ms;
    int ttff_ms, ttff_assist_ms, assist_valid_h;               /* ttff_ms <= 0: never a fix */
    int reset_cause, day, assist_age_h0, btn_level;
    /* ---- observations */
    int attach_calls, attach_ltem, attach_nb, attach_with_low_vdd, uploads, upload_valid, upload_age, upload_low;
    int upload_idle_mv, upload_loaded_mv, assists, aborts, sleep_mode, txpower_calls, txpower_dbm, tx_while_gate_low;
    uint64_t t_first_attach_us, t_last_upload_us, t_last_attach_us;
    int vbat_reads, vbat_reads_divider_off, divider_on_now;
    uint64_t divider_on_us, divider_on_since;
    int gnss_en, gnss_uart, gnss_rst_low, gnss_backpower_viol, gnss_cycles, gnss_inject_ok, gnss_on_calls;
    uint64_t gnss_on_since, gnss_on_us, gnss_max_on_us;
    int led_on, led_pulses; uint64_t led_on_since, led_on_us;
    int buz_cmd_on, buz_stop_calls, buz_starts; uint64_t buz_end_us, buz_since, buz_on_us, buz_max_run_us;
    int dones; uint64_t last_done_us; int tpl_missed; uint64_t tpl_first_missed_wake_us;
    int accel_arms, quota_saves;
    double q_uc;                                                /* battery side charge from events, uC */
    double q_attach, q_upload, q_assist, q_gnss, q_buz, q_led;
    int32_t quota[16]; int nquota;
    int tpl_wakes; int cold_fixes, hot_fixes; uint64_t vdd_dip_from_us, vdd_dip_to_us; int buz_overcap; int max_assist_age_h; uint32_t fota_first_off; uint64_t fota_fetch_us[64];
    /* fota: knobs and observations */
    int fota_fail_chunk, fota_fetches, fota_aborts, fota_applies, fota_confirms, fota_reverts, fota_fetch_low_vdd, fota_pending_confirm;
    uint32_t fota_kbps_x10, fota_last_off, fota_bytes; double q_fota;
    uint32_t fota_ret_off, fota_ret_kb;      /* retained across sb_reset() */
    int brownout_ret, brownout_saves;       /* retained RAM across sb_reset() */
} sb_t;
extern sb_t sb;

void sb_reset(void);                   /* power-on: clears the retained storage too */
void sb_reboot(int reset_cause);        /* a reset that keeps the retained storage (brown-out, watchdog, FOTA swap) */
void sb_run(uint64_t until_us);        /* run with the TPL5010 WAKE pulse every wdt_period_s (and its missed-DONE check) */
int  sb_buz_active(void);
#endif
