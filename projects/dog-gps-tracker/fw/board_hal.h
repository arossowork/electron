/* Device HAL of the tracker (the framework's own HAL is fwk_hal.h). The nRF Connect SDK port implements it
 * (port/nrf-connect-sdk/main.c lists the needs); test/sim_board.c simulates it. Each function belongs to the module
 * that owns the signal: a module calls only its own. Async calls answer with an ISR-posted EV_MODEM_EVT/EV_GNSS_EVT. */
#ifndef BOARD_HAL_H
#define BOARD_HAL_H
#include <stdint.h>

/* EV_MODEM_EVT kinds (a) */
enum { MK_ATTACH_OK = 1, MK_ATTACH_FAIL, MK_UPLOAD_OK, MK_UPLOAD_FAIL, MK_ASSIST_OK, MK_ASSIST_FAIL,
       MK_REQ_POSITION, MK_REQ_BUZZER, MK_LOST_START, MK_LOST_STOP, MK_POLL_REPLY,
       MK_FOTA_OFFER, MK_FOTA_CHUNK, MK_FOTA_FAIL };   /* 12 offer (b = image kB), 13 chunk done (b = bytes), 14 chunk failed */
/* EV_GNSS_EVT kinds (a) */
enum { GK_FIX_OK = 1, GK_NO_FIX };
enum { RAT_LTEM = 0, RAT_NBIOT = 1 };
enum { SLEEP_POLL = 0, SLEEP_EDRX = 1 };
enum { RESET_BROWNOUT = 1, RESET_WATCHDOG = 2, RESET_POWERON = 3 };

/* power: VBAT_SENSE_EN, VBAT_SENSE, modem rail and temperature (AT%XVBAT, AT%XTEMP) */
void board_vbat_divider(int on);
int  board_vbat_pin_mv(void);               /* SAADC, 16-sample average, calibrated, mV at the pin */
int  board_modem_vdd_mv(void);              /* AT%XVBAT: VDD_4V0 */
int  board_temp_c10(void);                  /* AT%XTEMP, 0.1 C */
/* conn: the modem */
void board_modem_attach(int rat, int req_edrx);                  /* requests PSM + eDRX 81.92 s when req_edrx */
void board_modem_sleep_mode(int mode);                           /* SLEEP_EDRX: listen to paging; SLEEP_POLL: PSM only */
void board_modem_set_txpower(int dbm);
void board_modem_upload(int valid, int age_s, int idle_mv, int loaded_mv, int low_batt);
void board_modem_assist_download(void);
void board_modem_abort(void);                                    /* cancel an upload or download that timed out */
/* gnss: LC76G */
void board_gnss_en(int on);                 /* GNSS_EN */
void board_gnss_uart(int connect);          /* 0: GNSS_RX driven low and GNSS_TX input, no pull; 1: UARTE1 pins */
void board_gnss_rst(int drive_low);         /* GNSS_RST_N: 1 drive low, 0 Hi-Z; never driven high */
int  board_gnss_inject_assist(void);        /* after power-up send PAIR590 (UTC from network time) and PAIR600 (last position); a newly downloaded EPO
                                             * file goes once into the LC76G flash (Flash EPO binary protocol, Quectel AGNSS AN V1.1 sec 2.1, persists with
                                             * VCC off); returns 0 when no valid file is stored */
int  board_assist_age_h(void);              /* hours since the stored assistance data, -1 none */
void board_assist_store(int bytes);
/* ui: LED, BTN, BUZ_P/BUZ_N */
void board_led(int on);
int  board_btn_level(void);
/* ONE finite PWM sequence in antiphase on BUZ_P/BUZ_N that the PWM peripheral ends by itself (SEQ REFRESH + LOOP, then
 * LOOPSDONE -> STOP shortcut, pins idle low): no CPU, no timer, no repeat-forever mode. duration_ms is at most
 * buzzer_on_s * 1000 (10 s): the caller checks it with buz_pwm_plan() (lib) and does not call this when the plan is 0.
 * Why this and not buzzer_hw_cap_s: the watchdog ends a stuck buzzer only after up to 128 s (C15); the 10 s of IF-7 hold
 * because the sequence is finite. */
void board_buz_start(uint32_t hz, uint32_t duration_ms);
void board_buz_stop(void);                  /* both legs low */
/* wdt: WDT_DONE, reset cause */
void board_wdt_done_pulse(void);
int  board_reset_cause(void);
/* motion: LIS2DW12 */
void board_accel_arm(void);
/* fota (IF-8, C4): MCUboot dual slot for the app, modem delta for the modem (Zephyr dfu/mcuboot.h, NCS dfu_target) */
void     board_fota_fetch(uint32_t offset, uint32_t len, uint32_t id);   /* async: ISR-posted MK_FOTA_CHUNK / MK_FOTA_FAIL with b = (id << 16) | bytes written to the slot; a late completion of an aborted chunk carries an old id and is ignored */
void     board_fota_progress_load(uint32_t *offset, uint32_t *total_kb);   /* retained flash (NVS), survives any reset */
void     board_fota_progress_save(uint32_t offset, uint32_t total_kb);
void     board_fota_apply(void);                             /* complete image: boot_request_upgrade(BOOT_UPGRADE_TEST) / dfu_target_schedule_update, then reboot */
int      board_fota_pending_confirm(void);                   /* 1 while the running image is a test image: !boot_is_img_confirmed() */
void     board_fota_abort(void);                             /* cancel the chunk in flight (no completion event follows) */
void     board_fota_confirm(void);                           /* boot_write_img_confirmed() */
void     board_fota_revert_reboot(void);                     /* reboot WITHOUT confirming: MCUboot swaps the previous image back */
/* brown-out backoff counter (PWR-7, F4) in RETAINED RAM (Zephyr retention subsystem, zephyr,retained-ram with magic + checksum)
 * so that it survives the pin reset the TPS3838 causes and a watchdog reset; a power-on reset clears it (cell swap, drained
 * supercap): that is correct, the backoff is for a repeating brown-out. Cleared after a good attach. UNVERIFIED: that nRF9151
 * RAM retention holds across a pin reset (Nordic PS pages were not readable in this pass); bring-up item 28 checks it, and
 * the fallback is NVS flash (<= 1 write per backoff step). */
int      board_brownout_load(void);
void     board_brownout_save(int count);
/* retained storage and calendar day (network time) */
void board_quota_load(int32_t *days, int *n);
void board_quota_save(const int32_t *days, int n);
int  board_day_index(void);
#endif
