/* Port stub for nRF Connect SDK (nRF9151, Zephyr). Not built on the host. It lists what the port must provide; the
 * product code (modules/, lib/, app.c) is portable C11 and talks only to fwk_hal.h and board_hal.h.
 *
 * SDK config (prj.conf): CONFIG_NRF_MODEM_LIB, CONFIG_LTE_LINK_CONTROL, CONFIG_LTE_LC_PSM_MODULE, CONFIG_LTE_LC_EDRX_MODULE,
 *   CONFIG_NRF_MODEM_LIB_NET_IF off (AT sockets only); CONFIG_NRFX_PWM0, CONFIG_NRFX_SAADC, CONFIG_NRFX_UARTE1,
 *   CONFIG_NRFX_TWIM, CONFIG_NRFX_GPIOTE, CONFIG_NRFX_WDT; CONFIG_PM_DEVICE; CONFIG_RETAINED_MEM or NVS for the
 *   lost-mode quota and the assistance blob; CONFIG_BOOTLOADER_MCUBOOT (swap, dual slot, 2 x fota_slot_kb 440 kB in the 1 MB internal flash, no external flash),
 *   CONFIG_DFU_TARGET, CONFIG_DFU_TARGET_MCUBOOT, CONFIG_DFU_TARGET_MODEM_DELTA, CONFIG_IMG_MANAGER, CONFIG_RETENTION + zephyr,retained-ram
 *   (brown-out count); pm_static.yml: mcuboot 48 kB, 2 x 440 kB, 80 kB settings/NVS, 16 kB spare (IF-8).
 *
 * fwk_hal.h:
 *   fwk_hal_now_us          k_uptime / counter on RTC1 (32.768 kHz, 30.5 us)
 *   fwk_hal_irq_save/restore  irq_lock / irq_unlock
 *   fwk_hal_wdt_feed        nrfx_wdt (8 s, runs in sleep) fed by the scheduler; the TPL5010 is the hang backstop
 *   fwk_hal_idle            FWK_IDLE_LIGHT: k_sleep to the deadline (System ON, RTC). FWK_IDLE_DEEP: same, modem in PSM;
 *                           System OFF is NOT used: modem state and timers would be lost.
 *   Sleep and wake sources: RTC compare (timers), GPIOTE SENSE on BTN (low), WDT_WAKE (high), ACCEL_INT (high), modem events.
 *
 * board_hal.h (all pins from board.h, never numbers):
 *   board_vbat_divider      GPIO BOARD_GPIO_VBAT_SENSE_EN high/low
 *   board_vbat_pin_mv       SAADC AIN0, gain 1/6, 12 bit, oversample 16, one-point calibrated (PWR-6: calibration required)
 *   board_modem_vdd_mv      AT%XVBAT (needs CFUN>=4); fall back: SAADC VDD channel. board_temp_c10: AT%XTEMP
 *   board_modem_attach      AT+CEREG, AT%XSYSTEMMODE (LTE-M, NB-IoT), AT+CPSMS, AT+CEDRXS (81.92 s), SIM = MFF2 eUICC
 *   board_modem_sleep_mode  PSM+eDRX or PSM only; board_modem_set_txpower: AT%XEMPR (23 -> 20 dBm, power class 5)
 *   board_modem_upload / assist_download / abort   TLS/CoAP session to the 1NCE endpoint; completion via fwk_post_isr(ev_modem_evt)
 *   board_gnss_en           GPIO GNSS_EN; board_gnss_uart(0): GNSS_RX output low, GNSS_TX input no pull; (1): UARTE1 on GNSS_RX/TX
 *   board_gnss_rst          GNSS_RST_N open-drain low or disconnected, never driven high
 *   board_gnss_inject_assist / board_assist_age_h / board_assist_store   LC76G EPO file in flash (format open: parts-rf.md)
 *   board_led, board_btn_level (GPIOTE both edges -> fwk_post_isr(ev_btn_edge))
 *   board_buz_start/stop    PWM0 two channels in antiphase on BUZ_P/BUZ_N, 4 kHz, H0H1 drive. ONE finite sequence that the PWM
 *                           ends by itself (IF-7, F3, C15): COUNTERTOP = 1 MHz / 4 kHz = 250 (PRESCALER 4 of 16 MHz), DECODER individual
 *                           mode, SEQ[0] one period (CH0 duty 125, CH1 duty 125 with bit 15 set), SEQ[0].REFRESH = 99 (24-bit field),
 *                           LOOP.CNT = 400 (16-bit field, max 65535) = 100 x 400 = 40000 periods = 10.000 s; shortcut LOOPSDONE -> STOP;
 *                           pins return to the GPIO latch (low). buz_pwm_plan() in fw/lib computes refresh/loop and refuses > 10 s.
 *                           Limits: LOOP 65535 x 100 periods = 1638 s at 4 kHz, so 10 s has a wide margin. Never SEQ repeat-forever.
 *                           Bit widths from nrfx (nrf_pwm_loop_set uint16_t, REFRESH asserted against the 24-bit mask); nRF9151 PS
 *                           PWM chapter not readable in this pass (Nordic docs behind a bot check): confirm at bring-up 24.
 *   board_wdt_done_pulse    WDT_DONE high >= 100 ns then low (TPL5010 tDONE min 100 ns, SNAS651A 7.5); DONE must arrive >= 20 ms before the next WAKE; board_reset_cause: RESETREAS
 *   board_accel_arm         LIS2DW12 wake-up threshold on TWIM, INT on ACCEL_INT; clear the latch by reading ALL_INT_SRC
 *   board_quota_load/save, board_day_index   retained storage; day from AT+CCLK network time
 *   board_brownout_load/save  retained RAM (retention subsystem, magic + checksum): survives the TPS3838 pin reset and a WDT reset,
 *                           cleared by power-on; UNVERIFIED on nRF9151, bring-up 28; fallback NVS flash
 *   board_fota_fetch(offset, len, id)  HTTPS range request (Range: bytes=offset-) into the MCUboot secondary slot (dfu_target_mcuboot_write) or
 *                           dfu_target_modem_delta_write; completion fwk_post_isr(ev_modem_evt(MK_FOTA_CHUNK/FAIL, (id << 16) | bytes))
 *   board_fota_abort        close the HTTP connection (no completion event may follow, a late one is ignored by id)
 *   board_fota_progress_load/save   offset + total kB in NVS (written once per 16 kB chunk)
 *   board_fota_apply        dfu_target_done + boot_request_upgrade(BOOT_UPGRADE_TEST) / dfu_target_schedule_update, then sys_reboot
 *   board_fota_pending_confirm  !boot_is_img_confirmed(); board_fota_confirm  boot_write_img_confirmed();
 *   board_fota_revert_reboot  sys_reboot without confirming: MCUboot swaps the previous image back (Zephyr dfu/mcuboot.h; NCS dfu_target docs)
 *
 * Boot order: GPIO to the safe levels of interface.yaml first (GNSS_EN low, GNSS_RX low, BUZ low), then fwk_init(),
 * app_init(), fwk_run(). The modem library is initialised by conn, not here. */
#include "fwk.h"

void app_init(void);

int main(void)
{
    fwk_init();
    app_init();
    fwk_run();
    return 0;
}
