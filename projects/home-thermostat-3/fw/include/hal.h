/* Tiny HAL: everything the portable core needs from the chip, nothing more.
 * port/esp-idf implements it with gpio_*, adc_oneshot + adc_cali, i2c_master, esp_timer and light sleep;
 * test/sim_hal.c implements it with a simulated board (cells, guard, divider, ADS1115, DRV8837 + relays). */
#ifndef HAL_H
#define HAL_H
#include <stddef.h>
#include <stdint.h>

void     hal_gpio_out(int gpio, int level);  /* push-pull output */
void     hal_gpio_hiz(int gpio);             /* input, no pull (deep-sleep state, rule 2) */
int      hal_gpio_in(int gpio);
void     hal_gpio_hold(int gpio, int on);    /* keep the output level through light sleep (rule 8c) */
int      hal_adc1_mv(int channel);           /* calibrated millivolts at the pin (adc_cali, ATTEN 6 dB) */
int      hal_i2c_write(uint8_t addr, const uint8_t *buf, size_t n);  /* 0 = ACK */
int      hal_i2c_read(uint8_t addr, uint8_t *buf, size_t n);
uint64_t hal_now_us(void);                   /* monotonic, 1 us resolution (esp_timer_get_time) */
void     hal_wait_us(uint32_t us);           /* busy wait, timer based, us accuracy */
void     hal_light_sleep_us(uint32_t us);    /* light sleep with timer wake */
int      hal_wifi_tx_busy(void);             /* 1 while the radio may transmit (rule 4) */
void     hal_wdt_feed(void);                 /* RTC watchdog (interface.yaml F2) */

#endif
