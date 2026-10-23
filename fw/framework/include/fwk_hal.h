/* The framework's platform boundary: time, interrupt masking, idle and the watchdog. Device access (GPIO, ADC, buses,
 * peripheral-timed pulses) is the project's board HAL, not the framework's. */
#ifndef FWK_HAL_H
#define FWK_HAL_H
#include <stdint.h>

enum { FWK_IDLE_LIGHT = 0, FWK_IDLE_DEEP = 1 };

uint64_t fwk_hal_now_us(void);                 /* monotonic, us (ESP-IDF: esp_timer_get_time) */
uint32_t fwk_hal_irq_save(void);               /* mask interrupts, return the previous state */
void     fwk_hal_irq_restore(uint32_t s);
void     fwk_hal_wdt_feed(void);
/* Sleep until `until_us` or an interrupt. FWK_IDLE_DEEP: no task is awake and the wait is long, so the port may
 * enter deep sleep (on ESP32-C3 this restarts the CPU: task contexts and timers live in RTC memory). */
void     fwk_hal_idle(uint64_t until_us, int mode);

#endif
