/* ESP-IDF port: stub. It shows the entry point and lists what the port must implement; nothing here is built yet.
 *
 * fwk_hal.h (framework)
 *   fwk_hal_now_us        esp_timer_get_time(); survives deep sleep through the RTC timer (32 kHz crystal Y1, R20)
 *   fwk_hal_irq_save/restore  portENTER_CRITICAL / portEXIT_CRITICAL on one spinlock
 *   fwk_hal_wdt_feed      RTC watchdog (rtc_wdt / LP WDT), timeout BOARD_WDT_TIMEOUT_MS (D19); paused in deep sleep
 *   fwk_hal_idle(until, mode)
 *       LIGHT: esp_sleep_enable_timer_wakeup + esp_light_sleep_start (the pulse RMT and GPIO holds keep running)
 *       DEEP:  save the scheduler timers and every module context to RTC memory (RTC_DATA_ATTR), timer wakeup at
 *              `until`, GPIO wake for BTN (esp_deep_sleep_enable_gpio_wakeup, after the D20 pin swap), then
 *              esp_deep_sleep_start; on boot restore them before fwk_run (no cold INIT for a timer wake)
 *
 * board_hal.h (this board)
 *   board_gpio_out/hiz    gpio_set_direction/level; hi-Z = input, no pull (rule 2)
 *   board_gpio_hold       gpio_hold_en/dis (rule 8c)
 *   board_adc_mv          adc_oneshot + adc_cali (curve fitting), ADC1, ATTEN 6 dB (R22)
 *   board_i2c_*           i2c_master, 100 kHz; bus released (hi-Z) before SENS_EN drops
 *   board_wait_us         esp_rom_delay_us
 *   board_wifi_tx_busy    from the Wi-Fi event handler (rule 4)
 *   board_pulse_start     two RMT TX channels started together (rmt_sync_manager): hi_gpio high and lo_gpio low for
 *                         drive_us, both high for brake_us, both low; the tx-done callback calls
 *                         fwk_post_isr(ev_pulse_done(relay)) and nothing else (F2)
 *
 * Interrupts: the BTN edge posts ev_button() (fwk_post_isr) and nothing else.
 * Config: CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG (IO20/21 drive K2, rule 1), CONFIG_RTC_CLK_SRC_EXT_CRYS, Wi-Fi/MQTT in
 *         comms (R7, hourly) stay inside the comms module.
 * Startup: report BOARD_CONTRACT_ID over MQTT so a board and an image can be matched. */
#include "fwk.h"

void app_init(void);

void app_main(void)
{
    fwk_init();
    app_init();
    fwk_run();
}
