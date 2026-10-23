/* Device HAL for this board (the framework's own HAL is fwk_hal.h). The ESP-IDF port implements it with gpio_*,
 * adc_oneshot + adc_cali, i2c_master, esp_rom_delay_us and RMT; test/sim_board.c simulates it. */
#ifndef BOARD_HAL_H
#define BOARD_HAL_H
#include <stddef.h>
#include <stdint.h>

void board_gpio_out(int gpio, int level);
void board_gpio_hiz(int gpio);
void board_gpio_hold(int gpio, int on);           /* keep the level through light sleep (rule 8c) */
int  board_adc_mv(int channel);
int  board_i2c_write(uint8_t addr, const uint8_t *buf, size_t n);
int  board_i2c_read(uint8_t addr, uint8_t *buf, size_t n);
void board_wait_us(uint32_t us);                  /* short busy wait (< 100 us) */
int  board_wifi_tx_busy(void);
/* Peripheral-timed bridge pulse (ESP32-C3: two synchronised RMT channels): hi_gpio high and lo_gpio low for
 * drive_us, both high for brake_us, then both low. It finishes without the CPU; its end-of-transmission interrupt
 * posts EV_PULSE_DONE(relay) and nothing else (F2). */
int  board_pulse_start(int relay, int hi_gpio, int lo_gpio, uint32_t drive_us, uint32_t brake_us);

#endif
