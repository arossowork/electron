/* ESP-IDF entry point (stub, not built in this repository). It shows how the port calls the portable core.
 *
 * What the port still needs (docs/hwsw-codesign.md, "Firmware stage"):
 *  - hal_esp.c implementing include/hal.h:
 *      gpio_config / gpio_set_level; hal_gpio_hiz = input, no pull; hal_gpio_hold = gpio_hold_en/dis (rule 8c);
 *      adc_oneshot on ADC1 BOARD_ADC1_CH_VBAT_ADC, ADC_ATTEN_DB_6, adc_cali curve fitting (+-10 mV, R22);
 *      i2c_master bus on BOARD_GPIO_SDA/SCL, 100 kHz, created after SENS_EN goes high and deleted before it goes low;
 *      hal_now_us = esp_timer_get_time; hal_wait_us = esp_rom_delay_us (the relay window is 1.2 ms wide: never
 *      vTaskDelay, whose default 100 Hz tick rounds 31 ms to 30 or 40 ms); hal_light_sleep_us = esp_sleep_enable_timer_wakeup
 *      + esp_light_sleep_start; hal_wifi_tx_busy from the Wi-Fi event state; hal_wdt_feed = RTC WDT (BOARD_WDT_TIMEOUT_MS).
 *  - sdkconfig: UART0 console off, console on USB-Serial-JTAG (rule 1); bootloader log none and
 *    esp_deep_sleep_disable_rom_logging(); 32 kHz external crystal as RTC slow clock (R20); brown-out level checked at
 *    the 2.0 V cut-off (bench); CONFIG_ESP_SLEEP_GPIO_RESET_WORKAROUND off so IO2 stays hi-Z (rule 2).
 *  - RTC_DATA_ATTR for app_rtc_t; relay states also in NVS for the cold-boot re-assert (rule 6).
 *  - Wi-Fi wake every BOARD_WIFI_PERIOD_S with fast reconnect (stored channel/BSSID, kept IP, MQTT persistent session,
 *    TLS resumption; R7 <= 50 uAh), never while a relay pulses (rule 4); TX power cap 15 dBm if the R3 bench test
 *    fails (rule 11); schedule and set-points in NVS (R21).
 *  - Deep sleep: IN pins, SDA, SCL hi-Z without pulls, SENS_EN low, LED off (rule 2); timer wake at
 *    BOARD_CONTROL_PERIOD_S. The setup button (IO9) cannot wake the C3: it is read at the control wake (partition.md P9).
 */
#include "app.h"
#include "board.h"
#include "esp_sleep.h"

static RTC_DATA_ATTR app_rtc_t rtc;
static RTC_DATA_ATTR uint32_t now_s;

void app_main(void)
{
    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_UNDEFINED) {  /* cold boot: new cells */
        uint8_t stored[RELAY_COUNT] = {0, 0};   /* TODO: read from NVS */
        app_cold_boot(&rtc, stored);
        now_s = 0;
    }
    wake_report_t rep;
    app_control_wake(&rtc, now_s, &rep);
    /* TODO: every BOARD_WIFI_PERIOD_S, or on BAT_CUTOFF_NOW / BAT_WARN / a fault: connect and publish rep over MQTT */
    now_s += BOARD_CONTROL_PERIOD_S;
    esp_sleep_enable_timer_wakeup((uint64_t)BOARD_CONTROL_PERIOD_S * 1000000u);
    esp_deep_sleep_start();
}
